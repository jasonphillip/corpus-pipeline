"""SQLite + FTS5 + sqlite-vec corpus database operations."""

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    corpus_path, SEARCH_VECTOR_WEIGHT, SEARCH_BM25_WEIGHT,
    SEARCH_PER_SOURCE_LIMIT, SEARCH_RECENCY_WEIGHT, SEARCH_CAPTION_PREFIX,
)
from .embedder import get_dims

# sqlite-vec stores fixed-dim vectors and silently accepts mismatched lengths
# at insert time, which would corrupt the index if the embedder ever fell back
# to a different model. Read the schema-declared dim once and validate.
_VEC_DIM_RE = re.compile(r"embedding\s+float\[(\d+)\]")

# sqlite-vec loaded at connection time
_VEC_EXTENSION = None


def _find_vec_extension() -> str:
    """Find the sqlite-vec shared library."""
    global _VEC_EXTENSION
    if _VEC_EXTENSION:
        return _VEC_EXTENSION
    import sqlite_vec
    _VEC_EXTENSION = sqlite_vec.loadable_path()
    return _VEC_EXTENSION


def connect(topic: str) -> sqlite3.Connection:
    """Open a connection to the topic's corpus database."""
    db_path = corpus_path(topic) / "processed" / "chunks.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # WAL pragma — idempotent; safe to run on already-WAL DBs.
    # Enables concurrent readers while a writer is active, which matters when
    # ingestors run alongside search sessions across multiple corpora.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.enable_load_extension(True)
    conn.load_extension(_find_vec_extension())
    conn.enable_load_extension(False)
    return conn


def init_schema(conn: sqlite3.Connection, dims: int):
    """Create tables if they don't exist."""
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_len INTEGER NOT NULL,
            hash TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{{}}',
            UNIQUE(source_path, chunk_index)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            content,
            source_path UNINDEXED,
            content_len UNINDEXED,
            chunk_id UNINDEXED,
            tokenize = 'porter unicode61'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
            id INTEGER PRIMARY KEY,
            embedding float[{dims}]
        );

        CREATE TABLE IF NOT EXISTS sources (
            source_path TEXT PRIMARY KEY,
            source_hash TEXT NOT NULL,
            processed_at TEXT NOT NULL,
            chunk_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_path);
        CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(hash);
    """)


def get_source_hash(conn: sqlite3.Connection, source_path: str) -> str | None:
    """Return the recorded SHA-256 of a source file, or None if never processed."""
    row = conn.execute(
        "SELECT source_hash FROM sources WHERE source_path = ?",
        (source_path,),
    ).fetchone()
    return row["source_hash"] if row else None


def upsert_source(conn: sqlite3.Connection, source_path: str,
                  source_hash: str, chunk_count: int) -> None:
    """Record that a source file was processed at the given content hash."""
    conn.execute(
        "INSERT INTO sources (source_path, source_hash, processed_at, chunk_count) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(source_path) DO UPDATE SET "
        "source_hash=excluded.source_hash, "
        "processed_at=excluded.processed_at, "
        "chunk_count=excluded.chunk_count",
        (source_path, source_hash,
         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         chunk_count),
    )


def content_hash(text: str) -> str:
    """Canonical content-dedup hash: sha256 of the chunk text, first 16 hex chars.

    Shared by the build's pre-embed dedup (process_chunks) and insert_chunk's
    safety-net check, so both agree on what counts as a duplicate. Dropping a duplicate before embedding avoids unnecessary service work on
    repeated content.
    """
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def get_existing_hashes(conn: sqlite3.Connection) -> set[str]:
    """Return every content hash already stored in this corpus (for pre-embed dedup).

    Empty set if the chunks table doesn't exist yet (fresh corpus).
    """
    try:
        return {r[0] for r in conn.execute("SELECT hash FROM chunks")}
    except sqlite3.OperationalError:
        return set()


def get_vec_dim(conn: sqlite3.Connection) -> int:
    """Return the embedding dimension declared in the chunks_vec schema.

    Not memoized: sqlite3.Connection on this build isn't weakly referenceable and
    an id(conn)-keyed cache risks stale reads across corpora with different dims.
    The sqlite_master lookup is a page-cached metadata read — cheap enough that
    the pre-embed dedup (process_chunks), not this, is where the real time is saved.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_vec'"
    ).fetchone()
    if not row or not row["sql"]:
        raise RuntimeError("chunks_vec table not initialized — call init_schema first")
    m = _VEC_DIM_RE.search(row["sql"])
    if not m:
        raise RuntimeError(f"could not parse vec dim from schema: {row['sql']}")
    return int(m.group(1))


def insert_chunk(conn: sqlite3.Connection, source_path: str, chunk_index: int,
                 content: str, embedding: bytes, metadata: dict | None = None) -> int | None:
    """Insert a chunk with its FTS5 and vector entries. Returns chunk id or None if duplicate."""
    expected_bytes = get_vec_dim(conn) * 4  # float32 = 4 bytes
    if len(embedding) != expected_bytes:
        raise ValueError(
            f"embedding dim mismatch: got {len(embedding)} bytes "
            f"({len(embedding) // 4} floats), schema expects {expected_bytes} bytes "
            f"({expected_bytes // 4} floats). Aborting insert to prevent silent "
            f"corruption of chunks_vec — likely cause: embedder fell back to a "
            f"different model than the one this corpus was initialized with."
        )

    chash = content_hash(content)
    metadata_json = json.dumps(metadata or {})

    # Safety-net dedup: the build now drops known-duplicate chunks BEFORE embedding
    # (process_chunks), but keep this check so any caller that inserts without the
    # pre-filter still can't create duplicate rows.
    existing = conn.execute("SELECT id FROM chunks WHERE hash = ?", (chash,)).fetchone()
    if existing:
        return None

    try:
        cur = conn.execute(
            "INSERT OR REPLACE INTO chunks (source_path, chunk_index, content, content_len, hash, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source_path, chunk_index, content, len(content), chash, metadata_json)
        )
        chunk_id = cur.lastrowid

        # FTS5 entry
        conn.execute(
            "INSERT INTO chunks_fts (content, source_path, content_len, chunk_id) VALUES (?, ?, ?, ?)",
            (content, source_path, len(content), str(chunk_id))
        )

        # Vector entry
        conn.execute(
            "INSERT INTO chunks_vec (id, embedding) VALUES (?, ?)",
            (chunk_id, embedding)
        )

        return chunk_id
    except sqlite3.IntegrityError:
        return None


def delete_by_source(conn: sqlite3.Connection, source_path: str):
    """Delete all chunks from a source (for re-ingestion)."""
    rows = conn.execute("SELECT id FROM chunks WHERE source_path = ?", (source_path,)).fetchall()
    ids = [r["id"] for r in rows]
    conn.execute("DELETE FROM sources WHERE source_path = ?", (source_path,))
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM chunks_vec WHERE id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM chunks_fts WHERE chunk_id IN ({placeholders})",
                 [str(i) for i in ids])
    conn.execute(f"DELETE FROM chunks WHERE id IN ({placeholders})", ids)


def _caption_filter(caption_mode: str, column: str) -> tuple[str, list]:
    """SQL fragment restricting rows by the caption-sidecar prefix.

    caption_mode: "include" (no filter), "exclude" (body text only),
    "only" (caption sidecars only).
    """
    if caption_mode == "exclude":
        return f" AND {column} NOT LIKE ?", [SEARCH_CAPTION_PREFIX + "%"]
    if caption_mode == "only":
        return f" AND {column} LIKE ?", [SEARCH_CAPTION_PREFIX + "%"]
    return "", []


def search_bm25(conn: sqlite3.Connection, query: str, limit: int = 50,
                caption_mode: str = "include") -> list[dict]:
    """Full-text BM25 search."""
    fts_query = _build_fts_query(query)
    clause, extra = _caption_filter(caption_mode, "content")
    try:
        rows = conn.execute(
            "SELECT chunk_id, source_path, content, bm25(chunks_fts) as score "
            "FROM chunks_fts WHERE chunks_fts MATCH ?" + clause +
            " ORDER BY score LIMIT ?",
            (fts_query, *extra, limit)
        ).fetchall()
    except sqlite3.OperationalError:
        # FTS5 syntax error from malformed query — return empty results
        return []
    return [dict(r) for r in rows]


def search_vector(conn: sqlite3.Connection, query_embedding: bytes, limit: int = 50,
                  caption_mode: str = "include") -> list[dict]:
    """Vector similarity search via sqlite-vec."""
    clause, extra = _caption_filter(caption_mode, "c.content")
    # KNN returns k rows BEFORE the caption predicate is applied, so over-fetch
    # for post-filter slack when a filter is active.
    k = limit * 2 if clause else limit
    rows = conn.execute(
        "SELECT v.id, v.distance, c.source_path, c.content "
        "FROM chunks_vec v "
        "JOIN chunks c ON c.id = v.id "
        "WHERE v.embedding MATCH ? AND k = ?" + clause +
        " ORDER BY v.distance",
        (query_embedding, k, *extra)
    ).fetchall()
    return [dict(r) for r in rows[:limit]]


def search_hybrid(conn: sqlite3.Connection, query: str, query_embedding: bytes,
                  top_k: int = 10, per_source_limit: int = SEARCH_PER_SOURCE_LIMIT,
                  caption_mode: str = "include") -> list[dict]:
    """Hybrid BM25 + vector search with score fusion and per-source dedup."""
    bm25_results = search_bm25(conn, query, limit=top_k * 3, caption_mode=caption_mode)
    vec_results = search_vector(conn, query_embedding, limit=top_k * 3, caption_mode=caption_mode)

    # Normalize BM25 scores (they're negative, lower = better)
    if bm25_results:
        bm25_min = min(r["score"] for r in bm25_results)
        bm25_max = max(r["score"] for r in bm25_results)
        bm25_range = bm25_max - bm25_min if bm25_max != bm25_min else 1.0
        bm25_by_id = {}
        for r in bm25_results:
            cid = _normalize_chunk_id(r)
            bm25_by_id[cid] = 1.0 - (r["score"] - bm25_min) / bm25_range
    else:
        bm25_by_id = {}

    # Normalize vector distances (cosine distance, lower = better)
    if vec_results:
        vec_min = min(r["distance"] for r in vec_results)
        vec_max = max(r["distance"] for r in vec_results)
        vec_range = vec_max - vec_min if vec_max != vec_min else 1.0
        vec_by_id = {}
        for r in vec_results:
            vec_by_id[r["id"]] = 1.0 - (r["distance"] - vec_min) / vec_range
    else:
        vec_by_id = {}

    # Merge all candidate IDs
    all_ids = set(bm25_by_id.keys()) | set(vec_by_id.keys())

    # Build content lookup
    content_lookup = {}
    for r in bm25_results:
        cid = _normalize_chunk_id(r)
        content_lookup[cid] = {"source_path": r["source_path"], "content": r["content"]}
    for r in vec_results:
        content_lookup[r["id"]] = {"source_path": r["source_path"], "content": r["content"]}

    # Score fusion
    scored = []
    for cid in all_ids:
        vec_score = vec_by_id.get(cid, 0.0)
        bm25_score = bm25_by_id.get(cid, 0.0)
        hybrid = (SEARCH_VECTOR_WEIGHT * vec_score) + (SEARCH_BM25_WEIGHT * bm25_score)
        info = content_lookup.get(cid, {})
        scored.append({
            "id": cid,
            "score": hybrid,
            "vec_score": vec_score,
            "bm25_score": bm25_score,
            "source_path": info.get("source_path", ""),
            "content": info.get("content", ""),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Recency boost — look up content_date from chunk metadata
    if SEARCH_RECENCY_WEIGHT > 0:
        candidate_ids = [s["id"] for s in scored if s["id"]]
        if candidate_ids:
            placeholders = ",".join("?" * len(candidate_ids))
            rows = conn.execute(
                f"SELECT id, metadata_json FROM chunks WHERE id IN ({placeholders})",
                candidate_ids
            ).fetchall()
            meta_by_id = {r["id"]: r["metadata_json"] for r in rows}
            today = datetime.now(timezone.utc)
            for s in scored:
                meta_str = meta_by_id.get(s["id"], "{}")
                try:
                    meta = json.loads(meta_str)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
                cd = meta.get("content_date", "")
                if cd:
                    try:
                        dt = datetime.strptime(cd, "%Y-%m").replace(tzinfo=timezone.utc)
                        age_days = max((today - dt).days, 0)
                        recency = math.exp(-0.002 * age_days)
                    except ValueError:
                        recency = 0.0
                else:
                    recency = 0.0
                s["score"] = (1 - SEARCH_RECENCY_WEIGHT) * s["score"] + SEARCH_RECENCY_WEIGHT * recency
            scored.sort(key=lambda x: x["score"], reverse=True)

    # Per-source limit
    source_counts: dict[str, int] = {}
    filtered = []
    for r in scored:
        sp = r["source_path"]
        source_counts[sp] = source_counts.get(sp, 0) + 1
        if source_counts[sp] <= per_source_limit:
            filtered.append(r)
        if len(filtered) >= top_k:
            break

    # Attach parsed metadata for provenance display (heading, original file).
    ids = [r["id"] for r in filtered if r["id"]]
    meta_by_id: dict[int, dict] = {}
    if ids:
        placeholders = ",".join("?" * len(ids))
        for row in conn.execute(
            f"SELECT id, metadata_json FROM chunks WHERE id IN ({placeholders})", ids
        ):
            try:
                meta_by_id[row["id"]] = json.loads(row["metadata_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                meta_by_id[row["id"]] = {}
    for r in filtered:
        r["metadata"] = meta_by_id.get(r["id"], {})

    return filtered


def get_stats(conn: sqlite3.Connection) -> dict:
    """Get corpus statistics."""
    total = conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()["c"]
    sources = conn.execute("SELECT COUNT(DISTINCT source_path) as c FROM chunks").fetchone()["c"]
    return {"total_chunks": total, "unique_sources": sources}


def _normalize_chunk_id(row: dict) -> int:
    """Extract a consistent int chunk ID from BM25 or vector result rows."""
    cid = row.get("chunk_id", row.get("id"))
    return int(cid) if cid is not None else 0


def _build_fts_query(query: str) -> str:
    """Convert natural language query to FTS5 MATCH expression.

    Preserves hyphens, dots, and special chars within tokens (e.g. "C++", "vue.js").
    Quotes each token to avoid FTS5 syntax errors from special chars.
    """
    import re
    # Match word-like tokens including hyphens, dots, plus signs, and non-ASCII
    tokens = re.findall(r'[\w+][\w.+\-]*', query, re.UNICODE)
    tokens = [t for t in tokens if len(t) > 1]
    if not tokens:
        # Fall back to the raw query quoted to prevent syntax errors
        return f'"{query}"'
    # Quote each token to escape FTS5 special characters
    quoted = [f'"{t}"' for t in tokens]
    return " AND ".join(quoted)

