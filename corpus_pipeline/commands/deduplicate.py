#!/usr/bin/env python3
"""Deduplicate and quality-filter raw files in a corpus."""

import hashlib
import sys
from pathlib import Path


from ..config import corpus_path
from ..content_filter import is_target_language, is_valid_content

DOC_EXTENSIONS = {".md", ".mdx", ".txt", ".rst"}


def content_hash(path: Path) -> str:
    """SHA256 hash of file content (stripped whitespace, original case)."""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return hashlib.sha256(text.encode()).hexdigest()


def dedup_raw(topic: str) -> tuple[int, int, int]:
    """Remove duplicate and low-quality files from raw/. Returns (total, deduped, filtered)."""
    raw_dir = corpus_path(topic) / "raw"
    if not raw_dir.exists():
        print(f"No raw directory for '{topic}'")
        return 0, 0, 0

    seen_hashes: dict[str, Path] = {}
    deduped = 0
    filtered = 0
    total = 0

    # Single pass over all doc extensions
    all_files = sorted(
        f for f in raw_dir.rglob("*")
        if f.is_file() and f.suffix in DOC_EXTENSIONS
    )

    for f in all_files:
        total += 1

        # Quality filter: language and content validation
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            f.unlink()
            filtered += 1
            continue

        if not is_target_language(content):
            print(f"  FILTER: {f.relative_to(raw_dir)} — non-target language")
            f.unlink()
            filtered += 1
            continue

        if not is_valid_content(content):
            print(f"  FILTER: {f.relative_to(raw_dir)} — low quality content")
            f.unlink()
            filtered += 1
            continue

        # Dedup by content hash (original case, full SHA256)
        h = hashlib.sha256(content.strip().encode()).hexdigest()
        if h in seen_hashes:
            print(f"  DUP: {f.relative_to(raw_dir)} == {seen_hashes[h].relative_to(raw_dir)}")
            f.unlink()
            deduped += 1
        else:
            seen_hashes[h] = f

    kept = total - deduped - filtered
    print(f"\nClean: {total} files scanned, {deduped} duplicates, {filtered} filtered, {kept} kept")
    return total, deduped, filtered


def main():
    if len(sys.argv) < 2:
        print("Usage: dedup_raw.py <topic>", file=sys.stderr)
        sys.exit(1)
    dedup_raw(sys.argv[1])


if __name__ == "__main__":
    main()

