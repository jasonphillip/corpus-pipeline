"""Re-embed chunks whose stored content hash is stale."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import datetime, timezone

from ..config import SNAPSHOT_ROOT, corpus_path
from ..corpus_db import connect, get_vec_dim
from ..embedder import embed_texts_parallel, get_dims


def _rebuild_vectors(connection: sqlite3.Connection, corpus: str,
                     rows: list[sqlite3.Row], index_dim: int,
                     batch_size: int) -> None:
    """Back up the index, then replace only its generated vector table."""
    database = corpus_path(corpus) / "processed" / "chunks.db"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = database.with_name(f"{database.name}.backup-{stamp}.db")
    destination = sqlite3.connect(backup)
    try:
        connection.backup(destination)
    finally:
        destination.close()
    print(f"Backup: {backup}")

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DROP TABLE chunks_vec")
        connection.execute("CREATE VIRTUAL TABLE chunks_vec USING vec0("
                           f"id INTEGER PRIMARY KEY, embedding float[{index_dim}] "
                           "distance_metric=cosine)")
        expected_bytes = index_dim * 4
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            embeddings = list(embed_texts_parallel([row["content"] for row in batch]))
            if len(embeddings) != len(batch) or any(
                    len(embedding) != expected_bytes for embedding in embeddings):
                raise ValueError("embedding count or dimension changed during rebuild")
            connection.executemany(
                "INSERT INTO chunks_vec (id, embedding) VALUES (?, ?)",
                [(row["id"], embedding) for row, embedding in zip(batch, embeddings)],
            )
        metric = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='chunks_vec'").fetchone()[0]
        vector_count = connection.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0]
        missing = connection.execute(
            "SELECT id FROM chunks EXCEPT SELECT id FROM chunks_vec").fetchone()
        if "distance_metric=cosine" not in metric or vector_count != len(rows) or missing:
            raise RuntimeError("rebuilt vector coverage or metric verification failed")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    print(f"Rebuilt {vector_count} vectors; schema: distance_metric=cosine")


def reembed_changed(corpus: str, batch_size: int = 1000, dry_run: bool = False,
                    rebuild_vectors: bool = False) -> int:
    if batch_size < 1:
        raise ValueError("batch size must be positive")
    connection = connect(corpus)
    index_dim = get_vec_dim(connection)
    live_dim = get_dims()
    if live_dim != index_dim:
        print(f"Embedding dimension mismatch: service={live_dim}, index={index_dim}")
        connection.close()
        return 1
    rows = connection.execute("SELECT id, content, hash FROM chunks").fetchall()
    if rebuild_vectors:
        if not dry_run:
            try:
                _rebuild_vectors(connection, corpus, rows, index_dim, batch_size)
            finally:
                connection.close()
        else:
            print(f"Would rebuild {len(rows)} vectors with distance_metric=cosine")
            connection.close()
        return 0
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
    parser.add_argument("--rebuild-vectors", action="store_true",
                        help="Back up the index and rebuild all vectors with cosine distance")
    args = parser.parse_args(argv)
    return reembed_changed(args.corpus, args.batch_size, args.dry_run,
                           args.rebuild_vectors)


if __name__ == "__main__":
    raise SystemExit(main())
