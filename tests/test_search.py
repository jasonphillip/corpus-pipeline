import os
import sqlite3
import struct
import subprocess
import sys

import pytest

from corpus_pipeline.commands import search
from corpus_pipeline.commands.search import RRF_K, _parse_argv, _rrf_merge
from corpus_pipeline.config import SEARCH_TOP_K
from corpus_pipeline import corpus_db, ollama_llm
from corpus_pipeline.corpus_db import _build_fts_query, _caption_filter, search_bm25


@pytest.fixture
def fts_connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        try:
            conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5("
                         "chunk_id, source_path, content)")
        except sqlite3.OperationalError as exc:
            if "no such module: fts5" in str(exc).lower():
                pytest.skip("SQLite was built without FTS5")
            raise
        yield conn
    finally:
        conn.close()


def _result(corpus, chunk_id, score):
    return {
        "corpus": corpus,
        "id": chunk_id,
        "score": score,
        "vec_score": score,
        "bm25_score": score,
        "source_path": f"{corpus}/{chunk_id}.md",
        "content": "example",
    }


def test_rrf_preserves_single_list_order():
    merged = _rrf_merge([[_result("a", 1, 0.9), _result("a", 2, 0.8)]])
    assert [row["id"] for row in merged] == [1, 2]
    assert merged[0]["rrf_score"] == 1.0 / (RRF_K + 1)


@pytest.mark.parametrize(
    ("variants", "expected"),
    [(["alternative one", "alternative two"], "Query expansion: +2 variants"),
     ([], "Query expansion skipped: no variants returned")],
)
def test_search_reports_query_expansion_without_services(
    tmp_path, monkeypatch, capsys, variants, expected
):
    (tmp_path / "processed").mkdir()
    (tmp_path / "processed" / "chunks.db").touch()

    class Connection:
        def close(self):
            pass

    monkeypatch.setattr(search, "corpus_path", lambda topic: tmp_path)
    monkeypatch.setattr(search, "connect", lambda topic: Connection())
    monkeypatch.setattr(search, "get_stats", lambda conn: {"total_chunks": 1})
    monkeypatch.setattr(search, "get_label", lambda: "test embedder")
    monkeypatch.setattr(search, "_search_single_corpus", lambda *args, **kwargs: [])
    monkeypatch.setattr(search, "SEARCH_SMART_MIN_CHUNKS", 1)
    monkeypatch.setattr(search, "SEARCH_QUERY_EXPANSION", True)
    monkeypatch.setattr(search, "SEARCH_RERANK", False)
    monkeypatch.setattr(ollama_llm, "expand_query", lambda query, n: variants)

    search.search(["example"], "original query", top_k=3)

    assert expected in capsys.readouterr().out


def test_search_parser_accepts_flags_after_topic():
    args = _parse_argv(["my-docs", "--captions-only", "block", "diagram"])
    assert args.captions_mode == "only"
    assert args.rest == ["my-docs", "block", "diagram"]
    assert args.top_k == SEARCH_TOP_K


def test_fts_query_quotes_special_tokens():
    assert _build_fts_query("C++ vue.js") == '"C++" OR "vue.js"'
    assert _build_fts_query("the admin API") == '"admin" OR "API"'
    assert _build_fts_query("the-2 a.b a+b tHis") == (
        '"the-2" OR "a.b" OR "a+b" OR "tHis"'
    )


def test_fts_match_config_and_environment_override(tmp_path):
    config_path = tmp_path / "corpus.yml"
    env = {**os.environ, "CORPUS_CONFIG": str(config_path)}
    env.pop("CORPUS_FTS_MATCH", None)
    command = [sys.executable, "-c", "from corpus_pipeline.config import SEARCH_FTS_MATCH; print(SEARCH_FTS_MATCH)"]
    assert subprocess.check_output(command, env=env, text=True).strip() == "any"
    config_path.write_text("search:\n  fts_match: all\n", encoding="utf-8")
    assert subprocess.check_output(command, env=env, text=True).strip() == "all"
    env["CORPUS_FTS_MATCH"] = "any"
    assert subprocess.check_output(command, env=env, text=True).strip() == "any"


def test_bm25_question_uses_any_term_by_default(fts_connection, monkeypatch):
    fts_connection.execute("INSERT INTO chunks_fts VALUES (?, ?, ?)",
                           (1, "admin.md", "The admin API authenticates requests with a bearer token."))
    monkeypatch.setattr(corpus_db, "SEARCH_FTS_MATCH", "any")
    assert [row["source_path"] for row in search_bm25(
        fts_connection, "How does the admin API authenticate requests?"
    )] == ["admin.md"]
    monkeypatch.setattr(corpus_db, "SEARCH_FTS_MATCH", "all")
    assert search_bm25(fts_connection, "How does the admin API authenticate requests?") == []


def test_bm25_preserves_identifiers_and_dotted_terms(fts_connection, monkeypatch):
    monkeypatch.setattr(corpus_db, "SEARCH_FTS_MATCH", "all")
    fts_connection.execute("INSERT INTO chunks_fts VALUES (?, ?, ?)",
                           (2, "identifiers.md", "C++ vue.js older-than v2.0 APIKey 7"))
    assert _build_fts_query("C++ vue.js older-than v2.0 APIKey 7") == (
        '"C++" AND "vue.js" AND "older-than" AND "v2.0" AND "APIKey" AND "7"'
    )
    assert [row["source_path"] for row in search_bm25(
        fts_connection, "C++ vue.js older-than v2.0 APIKey 7"
    )] == ["identifiers.md"]


@pytest.mark.parametrize("query", ["", "!?.,"])
def test_bm25_empty_or_punctuation_only_returns_empty(fts_connection, query):
    assert search_bm25(fts_connection, query) == []


def test_bm25_common_words_fall_back_to_full_query(fts_connection, monkeypatch):
    monkeypatch.setattr(corpus_db, "SEARCH_FTS_MATCH", "any")
    fts_connection.execute("INSERT INTO chunks_fts VALUES (?, ?, ?)",
                           (3, "common.md", "This is the answer."))
    assert _build_fts_query("this is the") == '"this" OR "is" OR "the"'
    assert [row["source_path"] for row in search_bm25(
        fts_connection, "this is the"
    )] == ["common.md"]


def test_bm25_all_requires_every_filtered_term(fts_connection, monkeypatch):
    monkeypatch.setattr(corpus_db, "SEARCH_FTS_MATCH", "all")
    fts_connection.executemany("INSERT INTO chunks_fts VALUES (?, ?, ?)", [
        (4, "one.md", "admin API only"),
        (5, "both.md", "admin API token"),
    ])
    assert [row["source_path"] for row in search_bm25(
        fts_connection, "the admin token"
    )] == ["both.md"]


def test_caption_filter_modes():
    assert _caption_filter("include", "content") == ("", [])
    clause, values = _caption_filter("exclude", "content")
    assert "NOT LIKE" in clause and values


def test_search_bm25_malformed_match_returns_empty(fts_connection):
    fts_connection.execute(
        "INSERT INTO chunks_fts VALUES (?, ?, ?)",
        ("1", "example.md", "example content"),
    )
    assert search_bm25(fts_connection, '"') == []


def test_search_bm25_missing_fts_table_raises(fts_connection):
    fts_connection.execute("DROP TABLE chunks_fts")
    with pytest.raises(sqlite3.OperationalError, match="no such table: chunks_fts"):
        search_bm25(fts_connection, "example")


def test_vector_search_uses_cosine_for_unnormalized_vectors(tmp_path, monkeypatch):
    pytest.importorskip("sqlite_vec")
    monkeypatch.setattr(corpus_db, "corpus_path", lambda topic: tmp_path)
    conn = corpus_db.connect("topic")
    try:
        corpus_db.init_schema(conn, 2)
        aligned = corpus_db.insert_chunk(conn, "aligned.md", 0, "aligned", struct.pack("<2f", 10, 0))
        nearby = corpus_db.insert_chunk(conn, "nearby.md", 0, "nearby", struct.pack("<2f", 0.9, 0.1))
        conn.commit()
        results = corpus_db.search_vector(conn, struct.pack("<2f", 1, 0), limit=2)
        assert [row["id"] for row in results] == [aligned, nearby]
    finally:
        conn.close()
