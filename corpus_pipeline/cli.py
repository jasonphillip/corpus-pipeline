"""Unified command-line entry point for the corpus pipeline."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .config import CORPUS_ROOT, CONFIG_PATH, get_embedding_config


HELP = """corpus — local document acquisition, indexing, and search

  corpus research <topic> [--input sources.json]
  corpus acquire <topic> <url> [url ...]
  corpus process <topic> [context|rag|both] [--force] [--only paths.txt]
  corpus search <topic> <query ...>
  corpus search --corpora a,b <query ...>
  corpus first-mention <topic> <phrase> [options]
  corpus refresh <snapshot|diff> <topic>
  corpus health [options]
  corpus list
  corpus config

Maintenance commands: deduplicate, bridge-frontmatter, strip-frontmatter,
reembed [--rebuild-vectors], import-labels.
"""


def _list_corpora() -> int:
    rows: list[tuple[str, str]] = []
    if CORPUS_ROOT.is_dir():
        for directory in sorted(CORPUS_ROOT.iterdir()):
            database = directory / "processed" / "chunks.db"
            if not database.is_file():
                continue
            try:
                connection = sqlite3.connect(str(database))
                count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                connection.close()
                rows.append((directory.name, str(count)))
            except sqlite3.Error:
                rows.append((directory.name, "index unreadable"))
    if not rows:
        print(f"No processed corpora under {CORPUS_ROOT}")
        return 0
    width = max(len(name) for name, _ in rows)
    for name, count in rows:
        print(f"  {name:<{width}}  {count} chunks")
    return 0


def _show_config() -> int:
    config = get_embedding_config()
    print(f"config_file: {CONFIG_PATH}")
    print(f"corpus_root: {CORPUS_ROOT}")
    for key in ("provider", "url", "model", "dims", "chunk_size", "label"):
        print(f"{key}: {config.get(key)}")
    return 0


def _research(argv: list[str]) -> int:
    from .manifest import add_source, read_manifest, write_manifest

    parser = argparse.ArgumentParser(prog="corpus research")
    parser.add_argument("topic")
    parser.add_argument("--input", type=Path)
    args = parser.parse_args(argv)
    raw = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    if not raw.strip():
        print("Provide a JSON array through --input or standard input.", file=sys.stderr)
        return 1
    sources = json.loads(raw)
    if not isinstance(sources, list):
        raise ValueError("source input must be a JSON array")
    manifest = read_manifest(args.topic)
    for source in sources:
        add_source(
            manifest,
            url=source["url"],
            source_type=source.get("type", "single_page"),
            method=source.get("method", "reader"),
            priority=source.get("priority", 5),
            quality_score=source.get("quality_score", 0.5),
            quality_rationale=source.get("quality_rationale", ""),
            estimated_pages=source.get("estimated_pages", 0),
            content_date=source.get("content_date", ""),
        )
    manifest["sources"].sort(
        key=lambda source: (source.get("priority", 99), -source.get("quality_score", 0))
    )
    write_manifest(args.topic, manifest)
    print(f"Manifest contains {len(manifest['sources'])} sources.")
    return 0


def _process(argv: list[str]) -> int:
    from .commands.context import build_context
    from .commands.process import process_topic
    from .config import corpus_path
    from .status import update_corpus_status

    parser = argparse.ArgumentParser(prog="corpus process")
    parser.add_argument("topic")
    parser.add_argument("mode", nargs="?", choices=["context", "rag", "both"], default="both")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", type=Path, metavar="PATHS_FILE")
    args = parser.parse_args(argv)
    if args.only and args.mode == "context":
        parser.error("--only requires rag or both mode")
    if args.mode in {"context", "both"} and args.only is None:
        build_context(args.topic)
    if args.mode in {"rag", "both"}:
        try:
            process_topic(args.topic, force=args.force, only=args.only)
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
    stats: dict[str, object] = {}
    context_file = corpus_path(args.topic) / "processed" / "CONTEXT.md"
    if context_file.is_file():
        stats["context_tokens"] = len(context_file.read_text(encoding="utf-8")) // 4
    database = corpus_path(args.topic) / "processed" / "chunks.db"
    if database.is_file():
        from .corpus_db import connect, get_stats
        from .embedder import get_label

        connection = connect(args.topic)
        db_stats = get_stats(connection)
        connection.close()
        stats.update({"chunks": db_stats["total_chunks"], "embed_model": get_label()})
    update_corpus_status(args.topic, f"process {args.topic} {args.mode}", stats)
    return 0


def _refresh(argv: list[str]) -> int:
    from .commands.refresh import cmd_diff, cmd_snapshot

    parser = argparse.ArgumentParser(prog="corpus refresh")
    parser.add_argument("operation", choices=["snapshot", "diff"])
    parser.add_argument("topic")
    args = parser.parse_args(argv)
    if args.operation == "snapshot":
        cmd_snapshot(args.topic)
        return 0
    changes = cmd_diff(args.topic)
    return 1 if changes["has_changes"] else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args.pop(0) if args else "help"
    if command in {"help", "-h", "--help"}:
        print(HELP)
        return 0
    if command == "research":
        return _research(args)
    if command == "acquire":
        from .commands.acquire import main as acquire_main
        return acquire_main(args)
    if command == "process":
        return _process(args)
    if command == "search":
        from .commands.search import main as search_main
        return int(search_main(args) or 0)
    if command in {"first-mention", "first"}:
        from .commands.first_mention import main as first_main
        return first_main(args)
    if command == "refresh":
        return _refresh(args)
    if command == "health":
        from .commands.health import main as health_main
        return health_main(args)
    if command in {"list", "ls"}:
        return _list_corpora()
    if command == "config":
        return _show_config()
    if command == "deduplicate":
        from .commands.deduplicate import dedup_raw
        parser = argparse.ArgumentParser(prog="corpus deduplicate")
        parser.add_argument("topic")
        parsed = parser.parse_args(args)
        dedup_raw(parsed.topic)
        return 0
    if command == "bridge-frontmatter":
        from .commands.bridge_frontmatter import main as bridge_main
        return bridge_main(args)
    if command == "strip-frontmatter":
        from .commands.strip_frontmatter import main as strip_main
        return strip_main(args)
    if command == "reembed":
        from .commands.reembed import main as reembed_main
        return reembed_main(args)
    if command == "import-labels":
        from .commands.import_labels import main as labels_main
        return labels_main(args)
    print(f"Unknown command: {command}\n", file=sys.stderr)
    print(HELP, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
