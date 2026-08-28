"""Remove selected frontmatter keys from indexed content and rebuild FTS5."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import SNAPSHOT_ROOT, corpus_path


DEFAULT_KEYS = (
    "pillar",
    "subbucket",
    "cluster_id",
    "weight",
    "taxonomy_match",
    "concept_tags",
)


def clean_content(content: str, keys: tuple[str, ...] = DEFAULT_KEYS) -> str:
    """Remove selected keys from a leading YAML frontmatter block."""
    if not content.startswith("---"):
        return content
    lines = content.splitlines()
    close = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
        None,
    )
    if close is None:
        return content
    kept = [
        line
        for line in lines[1:close]
        if not any(line.lstrip().startswith(f"{key}:") for key in keys)
    ]
    return "\n".join(["---", *kept, "---", *lines[close + 1 :]])


def strip_keys(corpus: str, keys: tuple[str, ...], dry_run: bool = False) -> int:
    database = corpus_path(corpus) / "processed" / "chunks.db"
    if not database.is_file():
        print(f"No index at {database}")
        return 1
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id, content, metadata_json FROM chunks"
    ).fetchall()
    updates: list[tuple[str, int, str, int]] = []
    for row in rows:
        old_content = row["content"] or ""
        new_content = clean_content(old_content, keys)
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        changed_metadata = any(key in metadata for key in keys)
        for key in keys:
            metadata.pop(key, None)
        if new_content != old_content or changed_metadata:
            updates.append(
                (new_content, len(new_content), json.dumps(metadata), row["id"])
            )
    print(f"Chunks scanned: {len(rows)}; updates: {len(updates)}")
    if dry_run:
        connection.close()
        return 0
    snapshot = SNAPSHOT_ROOT / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    snapshot.mkdir(parents=True, exist_ok=True)
    connection.execute("VACUUM INTO ?", (str(snapshot / f"{corpus}-before-strip.db"),))
    connection.executemany(
        "UPDATE chunks SET content = ?, content_len = ?, metadata_json = ? WHERE id = ?",
        updates,
    )
    connection.execute("DELETE FROM chunks_fts")
    connection.execute(
        "INSERT INTO chunks_fts(content, source_path, content_len, chunk_id) "
        "SELECT content, source_path, content_len, id FROM chunks"
    )
    connection.commit()
    connection.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus")
    parser.add_argument("--keys", default=",".join(DEFAULT_KEYS))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    keys = tuple(key.strip() for key in args.keys.split(",") if key.strip())
    return strip_keys(args.corpus, keys, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

