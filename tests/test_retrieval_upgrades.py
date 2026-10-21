"""Public, fictional fixtures for the retrieval upgrades."""

import json
import sqlite3
import struct

import pytest

from corpus_pipeline.chunk_dates import chunk_ts, date_bounds
from corpus_pipeline.chunker import chunk_profiled_file
from corpus_pipeline.corpus_db import (init_schema, insert_chunk, search_phrase,
                                       ensure_context_schema)


def test_canonical_chunk_date_and_bounds():
    assert chunk_ts({"content_date": "2024-01", "ts": "2025-03-04T12:00:00"}) == "2025-03-04T12:00:00"
    assert date_bounds("2025-02", "2025-02") == ("2025-02-01", "2025-02-28")
    with pytest.raises(ValueError):
        date_bounds("2025-03", "2025-02")


def test_profiled_email_keeps_date_and_prefix(tmp_path):
    path = tmp_path / "thread.md"
    path.write_text("## 2025-02-03 09:10 — Pat Example <pat@example.com>\nThe amber relay confirms receipt.\n")
    rows = chunk_profiled_file(path, "email_thread", {"subject": "Amber relay", "thread_id": "fictional-1"}, 1000, 20)
    assert rows[0]["metadata"]["ts"].startswith("2025-02-03")
    assert rows[0]["metadata"]["conversation_id"] == "fictional-1"
    assert rows[0]["content"].startswith("Email | Subject:")
    assert rows[0]["metadata"]["prefix_len"] > 0


def test_phrase_and_context_sidecar():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    import sqlite_vec
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)
    init_schema(conn, 2)
    chunk_id = insert_chunk(conn, "fictional.md", 0, "The amber relay confirms receipt.",
                            struct.pack("2f", 1, 0), {"date": "2025-02-03"})
    assert search_phrase(conn, 'Who said "amber relay confirms receipt"?', since="2025-02-01", until="2025-02-28")[0]["chunk_id"] == str(chunk_id)
    ensure_context_schema(conn)
    assert "chunk_context" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
