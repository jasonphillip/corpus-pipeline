#!/usr/bin/env python3
"""Process raw markdown files into chunked, embedded, indexed corpus."""


import argparse
import hashlib
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml


# Per-batch retry budget for transient embedding-service failures.
_BATCH_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
_BATCH_RETRIES = 3
_BATCH_BACKOFF_SECONDS = (1, 4, 10)


def _coerce_yaml(v: Any) -> Any:
    """Coerce YAML-parsed values into JSON-serializable forms.

    YAML auto-parses ISO timestamps into datetime objects; json.dumps in the
    insert path rejects them. Frontmatter routinely carries last_processed_at
    and similar fields, so coerce recursively before handing meta downstream.
    """
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _coerce_yaml(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_coerce_yaml(x) for x in v]
    return v


def _file_hash(path: Path) -> str:
    """SHA-256 of file contents, used to skip unchanged sources on re-runs."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _embed_with_fallback(texts: list[str]) -> tuple[list[bytes | None], int]:
    """Embed a batch, retrying transient failures and falling back per-chunk on 4xx.

    Returns (embeddings_or_None_for_dropped, dropped_count). The caller aborts
    the document if any embedding came back None.

    Strategy mirrors OpenAI's published guidance: 5xx/429/408 are transient
    (whole-batch retry with backoff); other 4xx mean a specific input is bad
    (drop into per-chunk mode to identify offenders before aborting the file).

    Uses concurrent HTTP fan-out while preserving input order.
    """
    from ..embedder import embed_texts, embed_texts_parallel

    for attempt in range(_BATCH_RETRIES):
        try:
            return list(embed_texts_parallel(texts)), 0
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in _BATCH_RETRY_STATUSES and attempt < _BATCH_RETRIES - 1:
                time.sleep(_BATCH_BACKOFF_SECONDS[attempt])
                continue
            # Non-transient or out of retries — fall through to per-chunk
            break
        except httpx.RequestError:
            if attempt < _BATCH_RETRIES - 1:
                time.sleep(_BATCH_BACKOFF_SECONDS[attempt])
                continue
            break

    # Per-chunk fallback: isolate the offender(s). Stays serial / simple —
    # this is the slow path that runs only after batch retries exhausted.
    out: list[bytes | None] = []
    dropped = 0
    for t in texts:
        try:
            out.append(embed_texts([t])[0])
        except (httpx.HTTPStatusError, httpx.RequestError):
            out.append(None)
            dropped += 1
    return out, dropped


from ..config import corpus_path, get_embedding_config
from ..content_filter import clean_chunk, information_density
from ..corpus_db import (
    connect, init_schema, insert_chunk, delete_by_source, get_stats,
    get_source_hash, upsert_source, content_hash,
)
from ..chunker import chunk_file, chunk_profiled_file
from ..embedder import get_dims, get_label
from ..hygiene import load_hygiene
from ..labels import apply_imported_labels, load_registry
from ..manifest import read_manifest, write_manifest, set_embedding_provenance


def parse_frontmatter(path: Path) -> dict[str, Any]:
    """Return YAML frontmatter as JSON-serializable metadata."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
            if first.rstrip("\n") != "---":
                return {}
            buf: list[str] = []
            for line in f:
                if line.rstrip("\n") == "---":
                    break
                buf.append(line)
            else:
                return {}
        data = yaml.safe_load("".join(buf)) or {}
        if not isinstance(data, dict):
            return {}
        return _coerce_yaml(data)
    except (OSError, yaml.YAMLError):
        return {}


def prepare_chunks(filepath: Path, mode: str, chunk_size: int, chunk_overlap: int,
                   chunking: dict | None = None, transcript: bool = False) -> tuple[list[dict], int]:
    """Prepare raw chunks with the same filtering used by the indexer."""
    chunking = chunking or {}
    if mode != "generic" and not (transcript and "mode" not in chunking):
        chunks = chunk_profiled_file(
            filepath, mode, parse_frontmatter(filepath), chunk_size, chunk_overlap,
            prefix=chunking.get("prefix", True), channel=chunking.get("channel"))
    else:
        chunks = chunk_file(filepath, chunk_size=chunk_size,
                            **({"overlap": chunk_overlap} if "overlap_chars" in chunking else {}),
                            **({"transcript": True} if transcript else {}))
    cleaned_chunks = []
    noise_filtered = 0
    for chunk in chunks:
        if mode == "generic":
            cleaned_text = clean_chunk(chunk["content"])
        elif transcript and "mode" not in chunking:
            cleaned_text = clean_chunk(chunk["content"], transcript=True)
        elif mode == "email_thread":
            cleaned_text = chunk["content"].strip()
        else:
            prefix_len = chunk.get("metadata", {}).get("prefix_len", 0)
            lead, body = chunk["content"][:prefix_len], chunk["content"][prefix_len:]
            cleaned_body = clean_chunk(body)
            if (cleaned_body is None and 0 < len(body.strip()) < 50 and
                    information_density(body) >= 0.3):
                cleaned_body = body.strip()
            cleaned_text = lead + cleaned_body if cleaned_body is not None else None
        if cleaned_text is not None:
            chunk["content"] = cleaned_text
            chunk["metadata"]["chunk_size"] = len(cleaned_text)
            cleaned_chunks.append(chunk)
        else:
            noise_filtered += 1
    return cleaned_chunks, noise_filtered


def process_topic(topic: str, force: bool = False, only: Path | None = None):
    """Process all raw files for a topic into chunks.db.

    Idempotent by source-file SHA-256: unchanged files are skipped without
    re-chunking or re-embedding. Pass force=True to re-process everything
    (e.g. after swapping embedding models or changing the chunker config).
    """
    raw_dir = corpus_path(topic) / "raw"
    meta_dir = corpus_path(topic) / "meta"

    # Resolve every selected path before opening the index or writing metadata.
    selected = None
    if only is not None:
        paths = [line.strip() for line in only.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        if not paths:
            raise ValueError("empty --only list")
        selected = []
        for rel_path in dict.fromkeys(paths):
            candidate = Path(rel_path)
            filepath = raw_dir / candidate
            if (candidate.is_absolute() or ".." in candidate.parts or
                    not filepath.resolve().is_relative_to(raw_dir.resolve()) or
                    not filepath.is_file() or
                    filepath.suffix not in {".md", ".mdx", ".txt", ".rst"}):
                raise ValueError(f"invalid or missing --only path: {rel_path}")
            selected.append(filepath)

    # Build source URL -> content_date mapping from manifest
    manifest = read_manifest(topic)
    chunking = manifest.get("chunking")
    chunking = chunking if isinstance(chunking, dict) else {}
    mode = chunking.get("mode") or ("transcript" if chunking.get("transcript") is True else "generic")
    if mode not in {"generic", "transcript", "email_thread", "chat_sessions", "document"}:
        raise ValueError(f"invalid chunking mode: {mode}")
    transcript = mode == "transcript"
    source_dates: dict[str, str] = {}
    for s in manifest.get("sources", []):
        cd = s.get("content_date", "")
        if cd:
            source_dates[s["url"]] = cd

    if not raw_dir.exists():
        print(f"Error: no raw directory at {raw_dir}", file=sys.stderr)
        sys.exit(1)
    meta_dir.mkdir(parents=True, exist_ok=True)

    # Get embedding config
    embed_cfg = get_embedding_config()
    dims = get_dims()
    chunk_size = embed_cfg["chunk_size"]
    chunk_overlap = embed_cfg.get("chunk_overlap", 200)
    if mode == "generic":
        chunk_size = chunking.get("chunk_chars", chunk_size)
        chunk_overlap = chunking.get("overlap_chars", chunk_overlap)
    if transcript and chunking.get("transcript_chunk_chars") is not None:
        chunk_size = int(chunking["transcript_chunk_chars"])
        if chunk_size < 1:
            raise ValueError("chunking.transcript_chunk_chars must be positive")
    if mode != "generic" and not (transcript and "mode" not in chunking):
        default_size, default_overlap = {
            "email_thread": (3000, 300), "chat_sessions": (1500, 150),
            "transcript": (3000, 300), "document": (2500, 250),
        }[mode]
        chunk_size = chunking.get("chunk_chars", default_size)
        chunk_overlap = chunking.get("overlap_chars", default_overlap)
    if (type(chunk_size) is not int or chunk_size < 100 or
            type(chunk_overlap) is not int or chunk_overlap < 0 or
            chunk_overlap >= chunk_size):
        raise ValueError("invalid chunk_chars or overlap_chars")
    print(f"Embedding: {get_label()}")
    print(f"Dimensions: {dims}")
    print(f"Chunk size: {chunk_size} chars")

    # Collect files
    files = selected if selected is not None else sorted(
        f for f in raw_dir.rglob("*")
        if f.is_file() and f.suffix in {".md", ".mdx", ".txt", ".rst"})
    print(f"\nFiles to process: {len(files)}")

    if not files:
        print("No files found to process.")
        return

    # Initialize database
    conn = connect(topic)
    init_schema(conn, dims)
    label_registry = load_registry(topic, corpus_path(topic))

    # Apply optional exclusions and metadata flags without changing raw files.
    hygiene = load_hygiene(topic)
    excluded_files = 0
    purged_sources = 0
    flagged_sources = 0
    if hygiene:
        kept = []
        for f in files:
            if hygiene.exclusion_reason(str(f.relative_to(raw_dir))):
                excluded_files += 1
            else:
                kept.append(f)
        files = kept
        selected_paths = {str(path.relative_to(raw_dir)) for path in files}
        for sp in hygiene.excluded_sources(conn):
            if selected is not None and sp not in selected_paths:
                continue
            delete_by_source(conn, sp)
            purged_sources += 1
        if purged_sources:
            conn.commit()
        if excluded_files or purged_sources:
            print(f"Hygiene: {excluded_files} excluded files skipped, "
                  f"{purged_sources} previously-indexed sources purged")

    # Stamp the manifest with the embedding model + dim that built this index.
    # Lets future tooling (search, migration) detect mismatches and migrate
    # corpora individually rather than relying on a single global config.
    set_embedding_provenance(
        manifest,
        model=embed_cfg["model"],
        dim=dims,
        provider=embed_cfg["provider"],
    )
    write_manifest(topic, manifest)

    start = time.time()
    total_chunks = 0
    total_dupes = 0
    total_predup = 0
    total_noise = 0
    total_dropped_chunks = 0
    processed_files = 0
    skipped_unchanged = 0
    failed_files = 0

    for i, filepath in enumerate(files, 1):
        rel_path = str(filepath.relative_to(raw_dir))
        try:
            # Skip if source file content is unchanged since last successful run.
            # Fast O(1) DB lookup + a SHA-256 of the file; lets re-runs converge
            # in seconds when nothing has changed upstream.
            src_hash = _file_hash(filepath)
            if not force and only is None and get_source_hash(conn, rel_path) == src_hash:
                skipped_unchanged += 1
                # Hygiene flags can arrive after a source was indexed (e.g. a
                # link table installed later). Apply them here as a pure
                # metadata_json update — no re-chunking or re-embedding.
                if hygiene:
                    hflags = hygiene.flags_for(rel_path)
                    if hflags and hygiene.apply_flags(conn, rel_path, hflags):
                        flagged_sources += 1
                conn.commit()
                continue

            # Pull YAML frontmatter (empty {} for files without it). Used to
            # carry per-source metadata (e.g. session_uuid, machine) into
            # chunks.metadata_json without schema changes.
            frontmatter = parse_frontmatter(filepath)
            hflags = hygiene.flags_for(rel_path) if hygiene else {}
            if hflags:
                flagged_sources += 1

            chunks, noise_filtered = prepare_chunks(filepath, mode, chunk_size,
                                                     chunk_overlap, chunking, transcript)

            # Replace this source in one transaction, including an empty result.
            delete_by_source(conn, rel_path)
            if not chunks:
                upsert_source(conn, rel_path, src_hash, 0)
                conn.commit()
                total_noise += noise_filtered
                processed_files += 1
                continue

            # Deduplicate within this source, preserving independent owners.
            deduped = []
            predup = 0
            seen_hashes = set()
            for c in chunks:
                h = content_hash(c["content"])
                if h in seen_hashes:
                    predup += 1
                    continue
                seen_hashes.add(h)
                deduped.append(c)
            chunks = deduped

            # Generate embeddings with batch retry + per-chunk fallback.
            texts = [c["content"] for c in chunks]
            embeddings, dropped = _embed_with_fallback(texts)
            if dropped:
                total_dropped_chunks += dropped
                raise RuntimeError(f"{dropped} embedding(s) dropped")

            # Match rel_path to a source URL for content_date
            matched_date = ""
            for src_url, cd in source_dates.items():
                # rel_path is relative to raw/, source URLs map to subdirs
                if rel_path in src_url or src_url.rstrip("/").split("/")[-1] in rel_path:
                    matched_date = cd
                    break

            # Insert chunks
            inserted = 0
            dupes = 0
            for chunk, emb in zip(chunks, embeddings):
                if emb is None:
                    continue
                meta = chunk.get("metadata") or {}
                # Merge frontmatter into chunk metadata. Frontmatter values
                # win over manifest-derived ones (e.g. content_date), since
                # frontmatter reflects the actual source.
                if frontmatter:
                    for k, v in frontmatter.items():
                        meta.setdefault(k, v)
                # Hygiene flags are authoritative — they overwrite, not
                # setdefault (e.g. cross_corpus_duplicate + canonical_source).
                if hflags:
                    meta.update(hflags)
                if matched_date and "content_date" not in meta:
                    meta["content_date"] = matched_date
                # Promote known timestamp aliases to canonical 'ts' so chronological
                # tools (find-first-mention) work uniformly across corpora whose
                # source frontmatter uses different field names.
                if "ts" not in meta:
                    for alias in ("started_at", "created_at", "content_date", "timestamp"):
                        v = meta.get(alias)
                        if v:
                            meta["ts"] = v
                            break
                apply_imported_labels(label_registry, rel_path, meta, src_hash)
                result = insert_chunk(
                    conn, rel_path, chunk["index"],
                    chunk["content"], emb, meta
                )
                if result:
                    inserted += 1
                else:
                    dupes += 1

            upsert_source(conn, rel_path, src_hash, inserted)
            conn.commit()
            total_chunks += inserted
            total_dupes += dupes
            total_predup += predup
            total_noise += noise_filtered
            total_dropped_chunks += dropped
            processed_files += 1

            if i % 10 == 0 or i == len(files):
                elapsed = time.time() - start
                print(f"  [{i}/{len(files)}] {rel_path}: {inserted} chunks "
                      f"({dupes} dupes, {noise_filtered} noise, "
                      f"{dropped} dropped) — {elapsed:.1f}s elapsed")

        except Exception as e:
            conn.rollback()
            failed_files += 1
            print(f"  FAIL [{i}/{len(files)}] {rel_path}: {e}", file=sys.stderr)
            continue

    conn.close()
    elapsed = time.time() - start

    # Get final stats
    conn = connect(topic)
    stats = get_stats(conn)
    conn.close()

    # Write processing log
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = [
        f"# Processing Log — {topic}",
        f"Date: {now}",
        f"Embedding model: {get_label()}",
        f"Dimensions: {dims}",
        f"Chunk size: {chunk_size} chars",
        f"",
        f"Files processed: {processed_files}/{len(files)}",
        f"Files skipped (unchanged): {skipped_unchanged}",
        f"Files failed: {failed_files}",
        f"Hygiene: {excluded_files} excluded, {purged_sources} purged, "
        f"{flagged_sources} flagged",
        f"Chunks created: {total_chunks}",
        f"Noise filtered: {total_noise}",
        f"Duplicates skipped pre-embed: {total_predup}",
        f"Duplicates skipped at insert: {total_dupes}",
        f"Chunks dropped (embed failed): {total_dropped_chunks}",
        f"Total chunks in DB: {stats['total_chunks']}",
        f"Unique sources: {stats['unique_sources']}",
        f"Processing time: {elapsed:.1f}s",
    ]
    (meta_dir / "processing.log").write_text("\n".join(log) + "\n")

    print(f"\n{'='*50}")
    print(f"Processing complete for '{topic}'")
    print(f"  Files: {processed_files} processed, {skipped_unchanged} unchanged, "
          f"{failed_files} failed (of {len(files)})")
    print(f"  Chunks: {total_chunks} new ({total_predup} pre-embed dupes, "
          f"{total_dupes} insert dupes, {total_noise} noise filtered, "
          f"{total_dropped_chunks} embed-dropped)")
    if hygiene:
        print(f"  Hygiene: {excluded_files} excluded, {purged_sources} purged, "
              f"{flagged_sources} flagged")
    print(f"  Total in DB: {stats['total_chunks']}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"  DB: {corpus_path(topic) / 'processed' / 'chunks.db'}")


def main():
    # Line-buffer stdout/stderr so progress shows up live in tee'd logs and tail
    # invocations — block buffering on file output is what made earlier failed
    # background runs look like silent crashes.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

    parser = argparse.ArgumentParser(description="Chunk + embed + index a corpus.")
    parser.add_argument("topic", help="Corpus topic name (subdir of ~/.corpus/)")
    parser.add_argument("--force", action="store_true",
                        help="Re-process every file even if its hash is unchanged")
    parser.add_argument("--only", type=Path, metavar="PATHS_FILE",
                        help="Reprocess only listed paths relative to raw/")
    args = parser.parse_args()
    try:
        process_topic(args.topic, force=args.force, only=args.only)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
