"""Re-embed chunks whose stored content hash is stale."""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone

from ..config import SNAPSHOT_ROOT
from ..corpus_db import connect, get_vec_dim
from ..embedder import embed_texts_parallel, get_dims


def reembed_changed(corpus: str, batch_size: int = 1000, dry_run: bool = False) -> int:
    connection = connect(corpus)
    index_dim = get_vec_dim(connection)
    live_dim = get_dims()
    if live_dim != index_dim:
        print(f"Embedding dimension mismatch: service={live_dim}, index={index_dim}")
        connection.close()
        return 1
    rows = connection.execute("SELECT id, content, hash FROM chunks").fetchall()
    changed = [
        (row["id"], row["content"] or "")
        for row in rows
        if hashlib.sha256((row["content"] or "").encode()).hexdigest()[:16]
        != row["hash"]
    ]
    print(f"Chunks: {len(rows)} total; {len(changed)} need re-embedding")
    if dry_run or not changed:
        connection.close()
        return 0
    snapshot = SNAPSHOT_ROOT / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    snapshot.mkdir(parents=True, exist_ok=True)
    connection.execute("VACUUM INTO ?", (str(snapshot / f"{corpus}-before-reembed.db"),))
    expected_bytes = index_dim * 4
    for start in range(0, len(changed), batch_size):
        batch = changed[start : start + batch_size]
        embeddings = embed_texts_parallel([content for _, content in batch])
        for (chunk_id, content), embedding in zip(batch, embeddings):
            if len(embedding) != expected_bytes:
                connection.rollback()
                connection.close()
                raise ValueError("embedding dimension changed during the operation")
            connection.execute("DELETE FROM chunks_vec WHERE id = ?", (chunk_id,))
            connection.execute(
                "INSERT INTO chunks_vec (id, embedding) VALUES (?, ?)",
                (chunk_id, embedding),
            )
            digest = hashlib.sha256(content.encode()).hexdigest()[:16]
            connection.execute(
                "UPDATE chunks SET hash = ? WHERE id = ?", (digest, chunk_id)
            )
        connection.commit()
        print(f"Re-embedded {min(start + batch_size, len(changed))}/{len(changed)}")
    connection.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return reembed_changed(args.corpus, args.batch_size, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

