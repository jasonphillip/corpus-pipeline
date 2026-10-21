"""SQLite + FTS5 + sqlite-vec corpus database operations."""

import hashlib
import json
import logging
import os
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    corpus_path, SEARCH_VECTOR_WEIGHT, SEARCH_BM25_WEIGHT,
    SEARCH_PER_SOURCE_LIMIT, SEARCH_RECENCY_WEIGHT, SEARCH_CAPTION_PREFIX,
    SEARCH_FTS_MATCH, SEARCH_FTS_PHRASE, SEARCH_PHRASE_BONUS, SEARCH_PHRASE_RRF_WEIGHT,
)
from .embedder import get_dims
from .chunk_dates import chunk_ts, chunk_date_sql, date_bounds, in_date_bounds

# sqlite-vec stores fixed-dim vectors and silently accepts mismatched lengths
# at insert time, which would corrupt the index if the embedder ever fell back
# to a different model. Read the schema-declared dim once and validate.
_VEC_DIM_RE = re.compile(r"embedding\s+float\[(\d+)\]")
_METADATA_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

_FTS_COMMON_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "do", "does", "did",
    "how", "what", "when", "where", "which", "who", "why", "of", "in", "on",
    "at", "to", "for", "from", "by", "with", "and", "or", "not", "this",
    "that", "these", "those", "it", "its", "as", "than", "then", "there",
    "their", "can", "could", "should", "would", "will", "has", "have", "had",
})

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
            embedding float[{dims}] distance_metric=cosine
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


def ensure_context_schema(conn: sqlite3.Connection) -> None:
    """Create the optional hash-keyed contextual retrieval sidecar."""
    conn.execute("CREATE TABLE IF NOT EXISTS chunk_context ("
                 "chunk_hash TEXT PRIMARY KEY, chunk_id INTEGER, context TEXT NOT NULL, "
                 "model TEXT, prompt_version TEXT, created_at TEXT, applied_at TEXT)")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5("
                 "context, chunk_id UNINDEXED, tokenize = 'porter unicode61')")


def _context_fts_ready(conn: sqlite3.Connection) -> bool:
    if os.environ.get("SEARCH_CONTEXT_FTS", "1").lower() in {"0", "false", "no"}:
        return False
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='context_fts' ").fetchone()
                and conn.execute("SELECT 1 FROM context_fts LIMIT 1").fetchone())


def _context_fts_hits(conn: sqlite3.Connection, match: str, limit: int,
                      metadata_filters: list[tuple[str, str]] | None,
                      since: str | None, until: str | None,
                      caption_mode: str) -> list[dict]:
    if not _context_fts_ready(conn):
        return []
    meta_clause, meta_params = _metadata_filter(metadata_filters or [], "c")
    date_clause, date_params = _date_filter_sql("c", since, until)
    caption_clause, caption_params = _caption_filter(caption_mode, "c.content")
    try:
        rows = conn.execute(
            "SELECT c.id AS chunk_id, c.source_path, c.content, bm25(context_fts) AS score "
            "FROM context_fts JOIN chunks c ON c.id = context_fts.chunk_id "
            "JOIN chunk_context cc ON cc.chunk_hash = c.hash "
            "WHERE context_fts MATCH ?" + caption_clause + meta_clause + date_clause + " ORDER BY score LIMIT ?",
            (match, *caption_params, *meta_params, *date_params, limit)).fetchall()
    except sqlite3.OperationalError as error:
        logging.warning("context FTS channel failed: %s", error)
        return []
    return [dict(row) for row in rows]


def _merge_fts_hits(primary: list[dict], contextual: list[dict], limit: int) -> list[dict]:
    by_id = {_normalize_chunk_id(row): row for row in primary}
    for row in contextual:
        cid = _normalize_chunk_id(row)
        if cid not in by_id or row["score"] < by_id[cid]["score"]:
            by_id[cid] = row
    return sorted(by_id.values(), key=lambda row: row["score"])[:limit]


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
    """Content hash used to deduplicate chunks within one source."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def get_vec_dim(conn: sqlite3.Connection) -> int:
    """Return the embedding dimension declared in the chunks_vec schema.

    Not memoized: sqlite3.Connection on this build isn't weakly referenceable and
    an id(conn)-keyed cache risks stale reads across corpora with different dims.
    The sqlite_master lookup is a page-cached metadata read.
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
    """Insert a chunk with its FTS5 and vector entries. Deduplicate within its source."""
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

    # Safety net for callers that bypass process's pre-embed source-local filter.
    existing = conn.execute(
        "SELECT id FROM chunks WHERE source_path = ? AND hash = ?",
        (source_path, chash),
    ).fetchone()
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


def _metadata_filter(filters: list[tuple[str, str]], alias: str) -> tuple[str, list[str]]:
    """Build a bound predicate with OR within a key and AND between keys."""
    groups: dict[str, list[tuple[str, list[str]]]] = {}
    for key, value in filters:
        if not _METADATA_KEY_RE.fullmatch(key):
            raise ValueError(f"invalid metadata key: {key!r}")
        paths = [f"$.{key}"]
        if key == "label":
            paths.append("$.labels")
        predicates = []
        bound = []
        for path in paths:
            predicates.append(
                f"EXISTS (SELECT 1 FROM json_each({alias}.metadata_json, ?) "
                "WHERE CAST(value AS TEXT) = ?)"
            )
            bound.extend((path, value))
        groups.setdefault(key, []).append(("(" + " OR ".join(predicates) + ")", bound))
    if not groups:
        return "", []
    clause = " AND ".join("(" + " OR ".join(part for part, _ in values) + ")"
                          for values in groups.values())
    params = [param for values in groups.values() for _, bound in values for param in bound]
    return " AND " + clause, params


def search_bm25(conn: sqlite3.Connection, query: str, limit: int = 50,
                caption_mode: str = "include",
                metadata_filters: list[tuple[str, str]] | None = None,
                since: str | None = None, until: str | None = None) -> list[dict]:
    """Full-text BM25 search."""
    token_query = _unquoted_query(query)
    if not token_query.strip() and '"' in query:
        return []
    fts_query = _build_fts_query(token_query)
    if not fts_query:
        return []
    clause, extra = _caption_filter(caption_mode, "chunks_fts.content")
    meta_clause, meta_params = _metadata_filter(metadata_filters or [], "c")
    date_clause, date_params = _date_filter_sql("c", since, until)
    join = " JOIN chunks c ON c.id = chunks_fts.chunk_id" if meta_clause or date_clause else ""
    columns = ("chunks_fts.chunk_id, chunks_fts.source_path, chunks_fts.content"
               if join else "chunk_id, source_path, content")
    try:
        rows = conn.execute(
            "SELECT " + columns + ", bm25(chunks_fts) as score "
            "FROM chunks_fts" + join + " WHERE chunks_fts MATCH ?" + clause + meta_clause + date_clause +
            " ORDER BY score LIMIT ?",
            (fts_query, *extra, *meta_params, *date_params, limit)
        ).fetchall()
    except sqlite3.OperationalError as error:
        message = str(error).lower()
        if any(term in message for term in ("fts5: syntax error near", "unknown special query", "unterminated string")):
            return []
        raise
    return _merge_fts_hits([dict(r) for r in rows],
                           _context_fts_hits(conn, fts_query, limit, metadata_filters, since, until,
                                             caption_mode), limit)


def _unquoted_query(query: str) -> str:
    return _split_quoted(query)[0]


def _split_quoted(query: str) -> tuple[str, list[str]]:
    """Split explicit phrases; doubled quotes are literal quotes inside a phrase."""
    outside, phrases, current = [], [], []
    quoted = False
    index = 0
    while index < len(query):
        char = query[index]
        if char == '"':
            if quoted and index + 1 < len(query) and query[index + 1] == '"':
                current.append('"')
                index += 2
                continue
            if quoted:
                phrases.append("".join(current))
                current = []
                quoted = False
            else:
                outside.append(" ")
                quoted = True
        elif quoted:
            current.append(char)
        else:
            outside.append(char)
        index += 1
    if quoted:
        outside.extend(['"', *current])
    return "".join(outside), phrases


def _phrase_queries(query: str) -> list[str]:
    _, phrases = _split_quoted(query)
    cleaned = " ".join(re.findall(r'[\w+][\w.+\-]*', query, re.UNICODE))
    if len(cleaned.split()) >= 2:
        phrases.insert(0, cleaned)
    return list(dict.fromkeys(phrase for phrase in phrases if phrase.strip()))


def _date_filter_sql(alias: str, since: str | None, until: str | None) -> tuple[str, list[str]]:
    since, until = date_bounds(since, until)
    if not since and not until:
        return "", []
    day = f"substr({chunk_date_sql(alias + '.metadata_json')}, 1, 10)"
    clauses, params = [], []
    if since:
        clauses.append(f"{day} >= ?")
        params.append(since)
    if until:
        clauses.append(f"{day} <= ?")
        params.append(until)
    return " AND " + " AND ".join(clauses), params


def search_phrase(conn: sqlite3.Connection, query: str, limit: int = 50,
                  caption_mode: str = "include",
                  metadata_filters: list[tuple[str, str]] | None = None,
                  since: str | None = None, until: str | None = None) -> list[dict]:
    """Search the whole query and each explicit quoted phrase as separate MATCH channels."""
    if not SEARCH_FTS_PHRASE:
        return []
    clause, extra = _caption_filter(caption_mode, "chunks_fts.content")
    meta_clause, meta_params = _metadata_filter(metadata_filters or [], "c")
    date_clause, date_params = _date_filter_sql("c", since, until)
    join = " JOIN chunks c ON c.id = chunks_fts.chunk_id" if meta_clause or date_clause else ""
    columns = ("chunks_fts.chunk_id, chunks_fts.source_path, chunks_fts.content"
               if join else "chunk_id, source_path, content")
    hits = {}
    for phrase in _phrase_queries(query):
        match = '"' + phrase.replace('"', '""') + '"'
        try:
            rows = conn.execute(
                "SELECT " + columns + ", bm25(chunks_fts) as score FROM chunks_fts" + join +
                " WHERE chunks_fts MATCH ?" + clause + meta_clause + date_clause +
                " ORDER BY score LIMIT ?",
                (match, *extra, *meta_params, *date_params, limit)).fetchall()
        except sqlite3.OperationalError as error:
            logging.warning("FTS phrase channel failed for %r: %s", phrase, error)
            continue
        for row in rows:
            hit = dict(row)
            cid = _normalize_chunk_id(hit)
            if cid not in hits or hit["score"] < hits[cid]["score"]:
                hits[cid] = hit
        for hit in _context_fts_hits(conn, match, limit, metadata_filters, since, until, caption_mode):
            cid = _normalize_chunk_id(hit)
            if cid not in hits or hit["score"] < hits[cid]["score"]:
                hits[cid] = hit
    return sorted(hits.values(), key=lambda row: row["score"])[:limit]


def search_vector(conn: sqlite3.Connection, query_embedding: bytes, limit: int = 50,
                  caption_mode: str = "include",
                  metadata_filters: list[tuple[str, str]] | None = None,
                  since: str | None = None, until: str | None = None) -> list[dict]:
    """Vector similarity search via sqlite-vec."""
    since, until = date_bounds(since, until)
    clause, extra = _caption_filter(caption_mode, "c.content")
    meta_clause, meta_params = _metadata_filter(metadata_filters or [], "c")
    # KNN returns k rows BEFORE the caption predicate is applied, so over-fetch
    # for post-filter slack when a filter is active.
    k = limit * 10 if since or until else (limit * 2 if clause and not meta_clause else limit)
    knn_filter = (" AND v.id IN (SELECT id FROM chunks c WHERE 1=1" + meta_clause + ")"
                  if meta_clause else "")
    rows = conn.execute(
        "SELECT v.id, v.distance, c.source_path, c.content, c.metadata_json "
        "FROM chunks_vec v "
        "JOIN chunks c ON c.id = v.id "
        "WHERE v.embedding MATCH ? AND k = ?" + knn_filter + clause +
        " ORDER BY v.distance",
        (query_embedding, k, *meta_params, *extra)
    ).fetchall()
    results = [dict(r) for r in rows]
    if since or until:
        results = [r for r in results if in_date_bounds(_parse_metadata(r["metadata_json"]), since, until)]
    return results[:limit]


def _parse_metadata(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def search_hybrid(conn: sqlite3.Connection, query: str, query_embedding: bytes,
                  top_k: int = 10, per_source_limit: int = SEARCH_PER_SOURCE_LIMIT,
                  caption_mode: str = "include",
                  metadata_filters: list[tuple[str, str]] | None = None,
                  since: str | None = None, until: str | None = None,
                  phrase_tier: bool = True) -> list[dict]:
    """Hybrid BM25 + vector search with score fusion and per-source dedup."""
    bm25_results = search_bm25(conn, query, limit=top_k * 3, caption_mode=caption_mode,
                               metadata_filters=metadata_filters, since=since, until=until)
    phrase_results = search_phrase(conn, query, limit=top_k * 3, caption_mode=caption_mode,
                                   metadata_filters=metadata_filters, since=since, until=until)
    phrase_ids = {_normalize_chunk_id(row) for row in phrase_results} if phrase_tier and SEARCH_PHRASE_BONUS else set()
    vec_results = search_vector(conn, query_embedding, limit=top_k * 3, caption_mode=caption_mode,
                                metadata_filters=metadata_filters, since=since, until=until)

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
    for row in phrase_results:
        bm25_by_id[_normalize_chunk_id(row)] = 1.0

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
    for r in bm25_results + phrase_results:
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
        if cid in phrase_ids:
            hybrid += SEARCH_PHRASE_BONUS
        info = content_lookup.get(cid, {})
        hit = {
            "id": cid,
            "score": hybrid,
            "vec_score": vec_score,
            "bm25_score": bm25_score,
            "source_path": info.get("source_path", ""),
            "content": info.get("content", ""),
        }
        if cid in phrase_ids:
            hit["phrase_hit"] = True
        scored.append(hit)

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
                cd = chunk_ts(meta)
                if cd:
                    try:
                        dt = datetime.fromisoformat((cd + "-01" if len(cd) == 7 else cd).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
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
    chunk_index_by_id: dict[int, int] = {}
    if ids:
        placeholders = ",".join("?" * len(ids))
        for row in conn.execute(
            f"SELECT id, chunk_index, metadata_json FROM chunks WHERE id IN ({placeholders})", ids
        ):
            chunk_index_by_id[row["id"]] = row["chunk_index"]
            try:
                meta_by_id[row["id"]] = json.loads(row["metadata_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                meta_by_id[row["id"]] = {}
    for r in filtered:
        r["metadata"] = meta_by_id.get(r["id"], {})
        r["chunk_index"] = chunk_index_by_id.get(r["id"])

    return filtered


def search_rrf(conn: sqlite3.Connection, query: str, query_embedding: bytes,
               top_k: int = 10, per_source_limit: int = SEARCH_PER_SOURCE_LIMIT,
               caption_mode: str = "include",
               metadata_filters: list[tuple[str, str]] | None = None,
               since: str | None = None, until: str | None = None) -> list[dict]:
    """Fuse BM25 and vector ranks, with the same source and metadata contract as hybrid."""
    return search_rrf_variants(conn, [(query, query_embedding)], top_k,
                               per_source_limit, caption_mode, metadata_filters, since, until)


def search_rrf_variants(conn: sqlite3.Connection, queries: list[tuple[str, bytes]],
                        top_k: int = 10, per_source_limit: int = SEARCH_PER_SOURCE_LIMIT,
                        caption_mode: str = "include",
                        metadata_filters: list[tuple[str, str]] | None = None,
                        since: str | None = None, until: str | None = None) -> list[dict]:
    """Fuse every variant's BM25 and vector list before applying source limits."""
    fused: dict[int, dict] = {}
    for variant_index, (query, embedding) in enumerate(queries):
        bm25 = search_bm25(conn, query, limit=top_k * 3, caption_mode=caption_mode,
                           metadata_filters=metadata_filters, since=since, until=until)
        phrase = search_phrase(conn, query, limit=top_k * 3, caption_mode=caption_mode,
                               metadata_filters=metadata_filters, since=since, until=until)
        tiered = variant_index == 0 and SEARCH_PHRASE_RRF_WEIGHT > 1
        vector = search_vector(conn, embedding, limit=top_k * 3, caption_mode=caption_mode,
                               metadata_filters=metadata_filters, since=since, until=until)
        for kind, rows in (("bm25", bm25), ("phrase", phrase), ("vec", vector)):
            for rank, row in enumerate(rows, 1):
                cid = _normalize_chunk_id(row)
                if cid not in fused:
                    fused[cid] = {"id": cid, "score": 0.0, "vec_score": 0.0,
                                  "bm25_score": 0.0, "bm25_rank": None, "vec_rank": None,
                                  "source_path": row["source_path"], "content": row["content"]}
                hit = fused[cid]
                contribution = (SEARCH_PHRASE_RRF_WEIGHT if tiered and kind == "phrase" else 1.0) / (60 + rank)
                hit["score"] += contribution
                hit["vec_score" if kind == "vec" else "bm25_score"] += contribution
                if tiered and kind == "phrase":
                    hit["phrase_hit"] = True
                if kind != "phrase":
                    rank_key = kind + "_rank"
                    if hit[rank_key] is None or rank < hit[rank_key]:
                        hit[rank_key] = rank
    if fused and SEARCH_RECENCY_WEIGHT > 0:
        ids = list(fused)
        placeholders = ",".join("?" * len(ids))
        dated = []
        for row in conn.execute(f"SELECT id, metadata_json FROM chunks WHERE id IN ({placeholders})", ids):
            stamp = chunk_ts(_parse_metadata(row["metadata_json"]))
            if stamp:
                try:
                    dt = datetime.fromisoformat((stamp + "-01" if len(stamp) == 7 else stamp).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    dated.append((dt.astimezone(timezone.utc), row["id"]))
                except ValueError:
                    pass
        for rank, (_, cid) in enumerate(sorted(dated, reverse=True), 1):
            fused[cid]["score"] += SEARCH_RECENCY_WEIGHT / (60 + rank)
    scored = sorted(fused.values(), key=lambda r: r["score"], reverse=True)
    filtered = []
    source_counts: dict[str, int] = {}
    for row in scored:
        source = row["source_path"]
        source_counts[source] = source_counts.get(source, 0) + 1
        if source_counts[source] <= per_source_limit:
            filtered.append(row)
        if len(filtered) >= top_k:
            break
    ids = [row["id"] for row in filtered]
    metadata = {}
    chunk_indexes = {}
    if ids:
        placeholders = ",".join("?" * len(ids))
        for row in conn.execute(
            f"SELECT id, chunk_index, metadata_json FROM chunks WHERE id IN ({placeholders})", ids
        ):
            chunk_indexes[row["id"]] = row["chunk_index"]
            try:
                metadata[row["id"]] = json.loads(row["metadata_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                metadata[row["id"]] = {}
    for row in filtered:
        row["metadata"] = metadata.get(row["id"], {})
        row["chunk_index"] = chunk_indexes.get(row["id"])
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
    # Match word-like tokens including hyphens, dots, plus signs, and non-ASCII
    tokens = re.findall(r'[\w+][\w.+\-]*', query, re.UNICODE)
    if not tokens:
        return ""
    filtered = [t for t in tokens if not (
        t.lower() in _FTS_COMMON_WORDS
        and not any(c.isdigit() or c in ".-+" for c in t)
        and not any(c.isupper() for c in t[1:])
    )]
    tokens = filtered or tokens
    # Quote each token to escape FTS5 special characters
    quoted = [f'"{t}"' for t in tokens]
    return (" AND " if SEARCH_FTS_MATCH == "all" else " OR ").join(quoted)


def _fts_tokens(query: str) -> list[str]:
    """Extract search terms for passage window selection."""
    return [token for token in re.findall(r'[\w+][\w.+\-]*', query, re.UNICODE)
            if len(token) > 1]
