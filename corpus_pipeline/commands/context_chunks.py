#!/usr/bin/env python3
"""Write and apply resumable, hash-keyed context for existing corpus chunks."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from itertools import islice
import os
from pathlib import Path
import random
import re
import sys
import time

import httpx

from ..config import corpus_path
from ..corpus_db import connect, ensure_context_schema, get_vec_dim
from ..embedder import embed_texts_parallel
from ..manifest import read_manifest, write_manifest

CONTEXT_PROMPT_VERSION = "context-v2-entities"
SYSTEM_PROMPT = ("Return exactly two lines for this text chunk. Line 1 is one plain-English "
                 "sentence under 40 words that situates the chunk: people, topic, time, and what "
                 "happened. Line 2 is Entities: followed by comma-separated people, organisations, "
                 "places, products, projects, and dates mentioned in the chunk. Use Entities: none "
                 "when there are none. Preserve proper names and dates. No preamble.")
LLM_URL = os.environ.get("CONTEXT_LLM_URL", "http://127.0.0.1:11434/v1").rstrip("/")
LLM_MODEL = os.environ.get("CONTEXT_LLM_MODEL", "qwen3:14b")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_prompt(mode: str, path: str, content: str, meta: dict,
                 previous: str = "", opening: str = "") -> str:
    def value(*keys):
        return next((meta[k] for k in keys if meta.get(k)), "")
    if mode == "email_thread":
        participants = value("participants")
        if isinstance(participants, list):
            participants = ", ".join(map(str, participants))
        details = (f"Subject: {value('subject', 'title')}\nParticipants: {participants}\n"
                   f"Date: {value('ts', 'date')}\nMessage {value('message_index')} of {value('message_count')}")
    elif mode == "chat_sessions":
        details = (f"Channel: {value('channel', 'source')}\nContact: {value('contact')}\n"
                   f"Date range: {value('ts', 'date')} to {value('ts_end', 'date')}")
    elif mode == "transcript":
        details = f"Recording or chat title: {value('title', 'name') or Path(path).stem}\nDate: {value('ts', 'date', 'recording_start')}"
    else:
        parts = Path(path).parts[-2:]
        details = (f"Title: {value('title', 'name') or Path(path).stem}\nPath: {'/'.join(parts)}\n"
                   f"Created: {value('created_at', 'date', 'ts')}\n"
                   f"Raw opening: {opening[:800]}\nPrevious chunk ending: {previous[-300:]}")
    return f"{details}\n\nChunk:\n{content}"


def generate_context(prompt: str) -> str:
    headers = {}
    if key := os.environ.get("CONTEXT_LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    response = httpx.post(
        f"{LLM_URL}/chat/completions",
        json={"model": LLM_MODEL, "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                             {"role": "user", "content": prompt}],
              "temperature": 0.2, "max_tokens": 160},
        headers=headers,
        timeout=90.0)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"] or ""


def _clean_output(value: str) -> str:
    value = re.sub(r"<think>.*?</think>", "", value, flags=re.S | re.I).strip()
    lines = [" ".join(line.strip(' \t\r\n"“”\'').split()) for line in value.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    first = lines[0]
    entity_line = next((line for line in lines[1:] if line.casefold().startswith("entities:")),
                       "Entities: none")
    entities = entity_line.split(":", 1)[1].strip() or "none"
    second = f"Entities: {entities}"[:499]
    return f"{first[:500 - len(second) - 1]}\n{second}"


def _write_one(prompt: str) -> str | None:
    for _ in range(2):
        try:
            result = _clean_output(generate_context(prompt))
            if result:
                return result
        except (httpx.HTTPError, KeyError, ValueError, TypeError, IndexError):
            pass
    return None


def _deadline(max_minutes: float | None) -> float:
    return time.monotonic() + max_minutes * 60 if max_minutes is not None else float("inf")


def _mode(topic: str) -> str:
    chunking = read_manifest(topic).get("chunking") or {}
    return chunking.get("mode") or ("transcript" if chunking.get("transcript") else "document")


def _unwritten(conn, sample: int | None, seed: int):
    cursor = conn.execute(
        "SELECT c.id, c.hash, c.source_path, c.chunk_index, c.content, c.metadata_json, "
        "(SELECT substr(p.content, -300) FROM chunks p WHERE p.source_path=c.source_path "
        "AND p.chunk_index<c.chunk_index ORDER BY p.chunk_index DESC LIMIT 1) AS previous, "
        "x.chunk_hash AS written_hash "
        "FROM chunks c LEFT JOIN chunk_context x ON x.chunk_hash=c.hash "
        + ("WHERE x.chunk_hash IS NULL " if sample is None else "") +
        "GROUP BY c.hash ORDER BY c.source_path, c.chunk_index")
    if sample is None:
        return cursor
    rng = random.Random(seed)
    chosen = []
    for count, row in enumerate(cursor, 1):
        if count <= sample:
            chosen.append(row)
        else:
            spot = rng.randrange(count)
            if spot < sample:
                chosen[spot] = row
    return sorted((row for row in chosen if row["written_hash"] is None),
                  key=lambda row: (row["source_path"], row["chunk_index"]))


def write(topic: str, limit: int | None = None, sample: int | None = None,
          seed: int = 0, max_minutes: float | None = None, parallel: int = 8) -> int:
    deadline = _deadline(max_minutes)
    conn = connect(topic)
    try:
        ensure_context_schema(conn)
        rows = _unwritten(conn, sample, seed)
        total = (len(rows) if sample is not None else conn.execute(
            "SELECT count(DISTINCT c.hash) FROM chunks c LEFT JOIN chunk_context x ON x.chunk_hash=c.hash "
            "WHERE x.chunk_hash IS NULL").fetchone()[0])
        if limit is not None:
            total = min(total, limit)
            rows = islice(rows, limit)
        rows = iter(rows)
        mode = _mode(topic)
        current_path, opening = None, ""
        done, started = 0, time.monotonic()
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            while True:
                if time.monotonic() >= deadline:
                    break
                batch = []
                for row in islice(rows, parallel * 2):
                    try:
                        meta = json.loads(row["metadata_json"] or "{}")
                    except (ValueError, TypeError):
                        meta = {}
                    if row["source_path"] != current_path:
                        current_path = row["source_path"]
                        source = Path(str(meta.get("source") or ""))
                        if source.is_file():
                            with source.open(encoding="utf-8", errors="replace") as stream:
                                opening = stream.read(800)
                        else:
                            opening = ""
                    batch.append((row, build_prompt(mode, row["source_path"], row["content"], meta,
                                                    row["previous"] or "", opening)))
                if not batch:
                    break
                futures = {pool.submit(_write_one, prompt): row for row, prompt in batch}
                for future in as_completed(futures):
                    row, context = futures[future], future.result()
                    if context:
                        conn.execute("INSERT OR IGNORE INTO chunk_context VALUES (?, ?, ?, ?, ?, ?, NULL)",
                                     (row["hash"], row["id"], context, LLM_MODEL, CONTEXT_PROMPT_VERSION, now()))
                        done += 1
                        if done % 64 == 0:
                            conn.commit()
                        if done % 500 == 0:
                            rate = done * 3600 / max(1, time.monotonic() - started)
                            print(f"done={done} remaining={total-done} rows_per_hour={rate:.0f}", flush=True)
        conn.commit()
        return done
    finally:
        conn.close()


def _sync_ids(conn) -> None:
    moved = conn.execute("SELECT x.chunk_hash, x.chunk_id, MIN(c.id) AS id FROM chunk_context x "
                         "JOIN chunks c ON c.hash=x.chunk_hash GROUP BY x.chunk_hash "
                         "HAVING NOT EXISTS (SELECT 1 FROM chunks old WHERE old.id=x.chunk_id)").fetchall()
    for row in moved:
        conn.execute("DELETE FROM context_fts WHERE chunk_id=?", (row["chunk_id"],))
        conn.execute("UPDATE chunk_context SET chunk_id=?, applied_at=NULL WHERE chunk_hash=?",
                     (row["id"], row["chunk_hash"]))
    conn.commit()


def apply(topic: str, max_minutes: float | None = None, batch: int = 32) -> int:
    deadline = _deadline(max_minutes)
    conn = connect(topic)
    try:
        ensure_context_schema(conn)
        _sync_ids(conn)
        completed = 0
        while time.monotonic() < deadline:
            rows = conn.execute("SELECT x.chunk_hash, c.id AS chunk_id, x.context, c.content "
                                "FROM chunk_context x JOIN chunks c ON c.hash=x.chunk_hash "
                                "WHERE NOT EXISTS (SELECT 1 FROM context_fts f WHERE f.chunk_id=c.id) "
                                "ORDER BY c.source_path, c.chunk_index LIMIT ?",
                                (batch,)).fetchall()
            if not rows:
                break
            texts = [f"{r['context']}\n\n{r['content']}" for r in rows]
            embeddings = embed_texts_parallel(texts)
            expected = get_vec_dim(conn) * 4
            if len(embeddings) != len(rows) or any(len(v) != expected for v in embeddings):
                raise ValueError(f"embedding dim mismatch: expected {expected} bytes")
            for row, vector in zip(rows, embeddings):
                conn.execute("DELETE FROM context_fts WHERE chunk_id=?", (row["chunk_id"],))
                conn.execute("INSERT INTO context_fts(context, chunk_id) VALUES (?, ?)",
                             (row["context"], row["chunk_id"]))
                conn.execute("DELETE FROM chunks_vec WHERE id=?", (row["chunk_id"],))
                conn.execute("INSERT INTO chunks_vec(id, embedding) VALUES (?, ?)",
                             (row["chunk_id"], vector))
                conn.execute("UPDATE chunk_context SET applied_at=? WHERE chunk_hash=?",
                             (now(), row["chunk_hash"]))
            conn.commit()
            completed += len(rows)
            manifest = read_manifest(topic)
            manifest.setdefault("embedding", {})["input"] = "context+content"
            manifest["embedding"]["prompt_version"] = CONTEXT_PROMPT_VERSION
            write_manifest(topic, manifest)
        return completed
    finally:
        conn.close()


def prune(topic: str) -> int:
    conn = connect(topic)
    try:
        ensure_context_schema(conn)
        stale = conn.execute("SELECT chunk_hash, chunk_id FROM chunk_context x "
                             "WHERE NOT EXISTS (SELECT 1 FROM chunks c WHERE c.hash=x.chunk_hash)").fetchall()
        for row in stale:
            conn.execute("DELETE FROM context_fts WHERE chunk_id=?", (row["chunk_id"],))
            conn.execute("DELETE FROM chunk_context WHERE chunk_hash=?", (row["chunk_hash"],))
        conn.execute("DELETE FROM context_fts WHERE NOT EXISTS "
                     "(SELECT 1 FROM chunks c WHERE c.id=context_fts.chunk_id)")
        conn.commit()
        return len(stale)
    finally:
        conn.close()


def status(topic: str) -> dict:
    conn = connect(topic)
    try:
        ensure_context_schema(conn)
        row = conn.execute("SELECT count(*) AS written, count(applied_at) AS applied, "
                           "max(created_at) AS newest FROM chunk_context").fetchone()
        result = {"written": row["written"], "applied": row["applied"],
                  "pending": row["written"] - row["applied"], "newest_created_at": row["newest"]}
        print(" ".join(f"{key}: {value}" for key, value in result.items()))
        return result
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corpus context", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("write", "apply", "status", "prune"):
        p = sub.add_parser(name)
        p.add_argument("--corpus", required=True)
        if name in {"write", "apply"}:
            p.add_argument("--max-minutes", type=float)
        if name == "write":
            p.add_argument("--limit", type=int)
            p.add_argument("--sample", type=int)
            p.add_argument("--seed", type=int, default=0)
            p.add_argument("--parallel", type=int, default=8)
        if name == "apply":
            p.add_argument("--batch", type=int, default=32)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.corpus) or ".." in args.corpus:
        parser.error("invalid corpus name")
    if not (corpus_path(args.corpus) / "processed" / "chunks.db").is_file():
        parser.error("corpus chunks.db does not exist")
    if getattr(args, "max_minutes", None) is not None and args.max_minutes < 0:
        parser.error("max-minutes must be nonnegative")
    if args.command == "write":
        if args.parallel < 1 or (args.limit is not None and args.limit < 0) or (args.sample is not None and args.sample < 1):
            parser.error("parallel and sample must be positive; limit must be nonnegative")
        print(f"written: {write(args.corpus, args.limit, args.sample, args.seed, args.max_minutes, args.parallel)}")
    elif args.command == "apply":
        if args.batch < 1:
            parser.error("batch must be positive")
        try:
            print(f"applied: {apply(args.corpus, args.max_minutes, args.batch)}")
        except (httpx.HTTPError, ConnectionError) as error:
            print(f"embedder unavailable: {error}", file=sys.stderr)
            return 4
    elif args.command == "prune":
        print(f"pruned: {prune(args.corpus)}")
    else:
        status(args.corpus)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
