"""Measure raw-file chunk coverage without embeddings or index writes."""

import argparse
import hashlib
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corpus_pipeline.config import corpus_path
from corpus_pipeline.commands.process import prepare_chunks


def _body(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            text = text[end + 5:]
    return re.sub(r"^## \d{4}-\d{2}-\d{2} \d{2}:\d{2} — [^\n]*\n?", "", text,
                  flags=re.MULTILINE)


def alpha_count(text: str) -> int:
    return sum(char.isalpha() for char in text)


def measure(corpus: str, mode: str, limit: int | None = None, seed: int = 1) -> None:
    raw_dir = corpus_path(corpus) / "raw"
    if not raw_dir.is_dir():
        raise FileNotFoundError(raw_dir)
    files = sorted(path for path in raw_dir.rglob("*") if path.is_file() and
                   path.suffix.lower() in {".md", ".markdown", ".txt"})
    if limit is not None:
        files = random.Random(seed).sample(files, min(limit, len(files)))
    zero = raw_total = kept_total = 0
    losses = []
    for path in files:
        raw = alpha_count(_body(path.read_text(encoding="utf-8", errors="replace")))
        sizes = {"email_thread": (3000, 300), "chat_sessions": (1500, 150),
                 "document": (2500, 250)}
        chunk_size, overlap = sizes.get(mode, (6000, 600))
        rows, _ = prepare_chunks(path, mode, chunk_size, overlap, {"mode": mode})
        kept = sum(alpha_count(_body(row["content"][row["metadata"].get("prefix_len", 0):]))
                   for row in rows)
        zero += not bool(rows)
        raw_total += raw
        kept_total += kept
        path_id = hashlib.sha256(str(path.relative_to(raw_dir)).encode()).hexdigest()[:12]
        losses.append((raw - kept, path_id, raw, kept))
    print(f"files sampled: {len(files)}")
    print(f"files with zero chunks: {zero}")
    print(f"body alphabetic chars kept vs raw: {kept_total} / {raw_total}")
    print("five worst files by chars lost (path SHA-256 prefix, lost, raw, kept):")
    for lost, path_id, raw, kept in sorted(losses, reverse=True)[:5]:
        print(f"  {path_id} {lost} {raw} {kept}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus")
    parser.add_argument("--mode", required=True,
                        choices=("email_thread", "chat_sessions", "document", "transcript", "generic"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be nonnegative")
    measure(args.corpus, args.mode, args.limit, args.seed)


if __name__ == "__main__":
    main()
