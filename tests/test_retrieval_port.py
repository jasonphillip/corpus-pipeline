"""Offline behavior checks for public retrieval features, using fictional records."""

import json
import sqlite3
import struct

import pytest

from corpus_pipeline.chunk_dates import chunk_date_sql, chunk_ts, date_bounds
from corpus_pipeline.chunker import chunk_profiled_file
from corpus_pipeline.commands import context_chunks
from corpus_pipeline.commands import first_mention, health
from corpus_pipeline.commands import process
from corpus_pipeline.commands.profiles import load_profiles, apply_profiles
from corpus_pipeline.commands.shadow import create_shadow
from corpus_pipeline.config import rerank_limits
from corpus_pipeline.corpus_db import (connect, init_schema, insert_chunk, ensure_context_schema,
                                       search_hybrid, search_rrf_variants, search_vector,
                                       search_bm25, search_phrase)
from corpus_pipeline.ollama_llm import best_rerank_window, _rerank_cross_encoder
from corpus_pipeline.retrieval_eval import build, compare, run


@pytest.fixture
def index(tmp_path, monkeypatch):
    import corpus_pipeline.corpus_db as db
    monkeypatch.setattr(db, "corpus_path", lambda _: tmp_path)
    conn = connect("fictional")
    init_schema(conn, 2)
    yield conn, tmp_path
    conn.close()


def vector(a=1, b=0):
    return struct.pack("2f", a, b)


def test_date_priority_sql_and_inclusive_period(index):
    conn, _ = index
    metadata = {"content_date": "2024-01", "ts": "2025-02-03T09:10:00"}
    assert chunk_ts(metadata).startswith("2025-02-03")
    insert_chunk(conn, "fictional-date.md", 0, "Fictional dated text.", vector(), metadata)
    assert conn.execute(f"SELECT {chunk_date_sql()} FROM chunks").fetchone()[0].startswith("2025-02-03")
    assert date_bounds("2025-02", "2025-02") == ("2025-02-01", "2025-02-28")
    with pytest.raises(ValueError):
        date_bounds("2025-04", "2025-03")


def test_first_mention_and_health_use_canonical_date(index, monkeypatch):
    conn, root = index
    insert_chunk(conn, "fictional-later.md", 0, "The amber relay was checked.", vector(),
                 {"ts": "2025-01-01", "content_date": "2020-01-01"})
    insert_chunk(conn, "fictional-earlier.md", 0, "The amber relay was checked again.", vector(),
                 {"date": "2024-01-01"})
    conn.commit()
    rows = first_mention.sql_chronological_scan(root / "processed" / "chunks.db", ["amber relay"])
    assert rows[0]["source_path"] == "fictional-earlier.md"
    monkeypatch.setattr(health, "CORPUS_ROOT", root.parent)
    (root.parent / "fictional").symlink_to(root, target_is_directory=True)
    report = health.audit_corpus("fictional")
    assert report["date_range"] == ["2024-01-01", "2025-01-01"]


def test_phrase_tier_date_filter_rrf_and_context_fts(index, monkeypatch):
    conn, _ = index
    target = insert_chunk(conn, "fictional-one.md", 0, "The amber relay confirms receipt on Tuesday.",
                          vector(), {"ts": "2025-02-03"})
    other = insert_chunk(conn, "fictional-two.md", 0, "The silver meter measures current on Tuesday.",
                         vector(0, 1), {"ts": "2024-02-03"})
    query = 'Which note says "amber relay confirms receipt"?'
    assert search_phrase(conn, query, caption_mode="exclude", since="2025-02",
                         until="2025-02")[0]["chunk_id"] == str(target)
    assert not search_bm25(conn, "silver meter", since="2025-02")
    assert [r["id"] for r in search_vector(conn, vector(0, 1), limit=1,
                                           since="2025-02", until="2025-02")] == [target]
    assert search_hybrid(conn, query, vector(0, 1), top_k=2)[0]["id"] == target
    assert search_rrf_variants(conn, [(query, vector(0, 1))], top_k=2)[0]["id"] == target
    ensure_context_schema(conn)
    hash_value = conn.execute("SELECT hash FROM chunks WHERE id=?", (target,)).fetchone()[0]
    conn.execute("INSERT INTO chunk_context VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (hash_value, target, "A cobalt dispatch confirmed delivery", "demo", "v1", "2025-02-03", "2025-02-03"))
    conn.execute("INSERT INTO context_fts(context, chunk_id) VALUES (?, ?)",
                 ("A cobalt dispatch confirmed delivery", target))
    assert search_bm25(conn, "cobalt dispatch")[0]["chunk_id"] == target
    monkeypatch.setenv("SEARCH_CONTEXT_FTS", "0")
    assert search_bm25(conn, "cobalt dispatch") == []
    assert other != target


def test_best_rerank_window_selects_late_evidence():
    content = "# Fictional report\n" + "irrelevant material " * 350 + "amber relay confirms receipt"
    selected = best_rerank_window("amber relay confirms receipt", content, 500)
    assert selected.startswith("# Fictional report")
    assert "amber relay confirms receipt" in selected


def test_rerank_limits_default(monkeypatch):
    monkeypatch.delenv("CORPUS_RERANK_DOC_CHARS", raising=False)
    monkeypatch.delenv("CORPUS_RERANK_BATCH_TOKENS", raising=False)
    assert rerank_limits() == (2000, 4096)


def test_rerank_limits_environment_override(monkeypatch):
    monkeypatch.setenv("CORPUS_RERANK_DOC_CHARS", "2500")
    monkeypatch.setenv("CORPUS_RERANK_BATCH_TOKENS", "8192")
    assert rerank_limits() == (2500, 8192)


def test_cross_encoder_receives_late_window(monkeypatch):
    import corpus_pipeline.ollama_llm as llm
    seen = {}
    class Response:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"results": [{"index": 0, "relevance_score": 0.9}]}
    def post(url, json, timeout):
        seen.update(json)
        return Response()
    monkeypatch.setattr(llm.httpx, "post", post)
    long_text = "# Fictional report\n" + "irrelevant material " * 350 + "amber relay confirms receipt"
    scored, error = _rerank_cross_encoder("amber relay confirms receipt", [{"content": long_text}])
    assert error is None and scored[0]["rerank_score"] == 0.9
    assert "amber relay confirms receipt" in seen["documents"][0]


def test_rrf_recency_contributes_before_source_cap(index, monkeypatch):
    import corpus_pipeline.corpus_db as db
    conn, _ = index
    old = insert_chunk(conn, "fictional-old.md", 0, "Unrelated first note.",
                       vector(), {"date": "2020-01-01"})
    recent = insert_chunk(conn, "fictional-recent.md", 0, "Unrelated second note.",
                          vector(), {"date": "2025-01-01"})
    monkeypatch.setattr(db, "SEARCH_RECENCY_WEIGHT", 20)
    rows = search_rrf_variants(conn, [("absent keyword", vector())], top_k=2)
    assert rows[0]["id"] == recent and {row["id"] for row in rows} == {old, recent}


def test_profile_modes_and_shadow(tmp_path):
    root = tmp_path / "corpora"
    original = root / "fictional"
    (original / "raw").mkdir(parents=True)
    (original / "manifest.json").write_text('{"topic":"fictional","sources":[]}\n')
    mapping = tmp_path / "profiles.yml"
    mapping.write_text("fictional:\n  chunking:\n    mode: document\n  search:\n    fusion: rrf\n")
    profiles = load_profiles(mapping)
    json_mapping = tmp_path / "profiles.json"
    json_mapping.write_text(json.dumps(profiles))
    assert load_profiles(json_mapping) == profiles
    assert apply_profiles(root, profiles, dry_run=True) == ["fictional"]
    assert "chunking" not in json.loads((original / "manifest.json").read_text())
    with pytest.raises(FileNotFoundError):
        apply_profiles(root, {**profiles, "absent": {"chunking": {"mode": "generic"}}})
    assert "chunking" not in json.loads((original / "manifest.json").read_text())
    assert apply_profiles(root, profiles) == ["fictional"]
    shadow = create_shadow(root, "fictional", "candidate", profiles)
    assert (shadow / "raw").is_symlink()
    assert json.loads((shadow / "manifest.json").read_text())["chunking"]["mode"] == "document"
    with pytest.raises(FileExistsError):
        create_shadow(root, "fictional", "candidate")


@pytest.mark.parametrize("mode,text,frontmatter", [
    ("chat_sessions", "## 2025-02-03 09:10 session\nPat Example sent the amber relay.",
     {"contact": "Pat Example", "contact_key": "pat@example.com"}),
    ("transcript", "## Prompt\nWhere is the amber relay?\n## Response\nIt is here.",
     {"title": "Fictional chat", "date": "2025-02-03", "id": "chat-1"}),
    ("document", "# Fictional report\n" + "amber relay " * 40,
     {"title": "Fictional report", "date": "2025-02-03"}),
])
def test_structural_chunk_modes(tmp_path, mode, text, frontmatter):
    path = tmp_path / "fictional.md"
    path.write_text(text)
    rows = chunk_profiled_file(path, mode, frontmatter, 500, 20)
    assert rows
    assert rows[0]["metadata"]["ts"]
    assert rows[0]["metadata"]["prefix_len"] > 0
    assert "amber relay" in rows[0]["content"]
    if mode != "document":
        assert rows[0]["metadata"]["conversation_id"]


def test_process_stores_profiled_chunk_metadata(tmp_path, monkeypatch):
    import corpus_pipeline.corpus_db as db
    topic_dir = tmp_path / "fictional"
    raw = topic_dir / "raw"
    raw.mkdir(parents=True)
    css = "<style>td.tile { width: auto; }</style>" * 60
    (raw / "thread.md").write_text(
        "---\nsubject: Privacy Policy\nthread_id: fictional-thread\n---\n"
        "## 2025-02-03 09:10 — Pat Example <pat@example.com>\n" + css +
        "<p>The amber relay confirmed receipt of the fictional package.</p>\n"
        "## 2025-02-03 09:11 — Lee Example <lee@example.com>\n"
        "<script>window.track = true;</script>"
        "<div>The cobalt relay confirmed receipt of the fictional package.</div>\n")
    monkeypatch.setattr(process, "corpus_path", lambda _: topic_dir)
    monkeypatch.setattr(db, "corpus_path", lambda _: topic_dir)
    monkeypatch.setattr(process, "read_manifest", lambda _: {"sources": [], "chunking": {"mode": "email_thread"}})
    monkeypatch.setattr(process, "write_manifest", lambda *args: None)
    monkeypatch.setattr(process, "get_embedding_config", lambda: {
        "provider": "fake", "model": "fake", "chunk_size": 1000, "chunk_overlap": 100})
    monkeypatch.setattr(process, "get_dims", lambda: 2)
    monkeypatch.setattr(process, "get_label", lambda: "fake")
    monkeypatch.setattr(process, "load_hygiene", lambda _: None)
    monkeypatch.setattr(process, "load_registry", lambda *args: {})
    monkeypatch.setattr(process, "apply_imported_labels", lambda *args: None)
    monkeypatch.setattr(process, "_embed_with_fallback", lambda texts: ([vector()] * len(texts), 0))
    process.process_topic("fictional")
    conn = db.connect("fictional")
    try:
        rows = conn.execute("SELECT content, metadata_json FROM chunks ORDER BY chunk_index").fetchall()
        assert len(rows) == 2
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            assert row["content"].startswith("Email | Subject: Privacy Policy")
            assert "<style>" not in row["content"] and "window.track" not in row["content"]
            assert len(row["content"]) < 300
            assert metadata["prefix_len"] > 0
            assert metadata["chunk_size"] == len(row["content"])
            assert metadata["ts"].startswith("2025-02-03")
            assert metadata["conversation_id"] == "fictional-thread"
    finally:
        conn.close()


def test_eval_build_run_compare(index, monkeypatch, tmp_path):
    conn, root = index
    for number in range(8):
        insert_chunk(conn, f"fictional-{number}.md", 0,
                     (f"The amber relay {number} confirmed delivery on Tuesday. " * 10),
                     vector(), {"date": "2025-02-03"})
    conn.commit()
    regression = tmp_path / "regressions.jsonl"
    regression.write_text(json.dumps({"question": "amber relay", "chunk_id":
                                      conn.execute("SELECT MIN(id) FROM chunks").fetchone()[0]}) + "\n")
    questions, _ = build.build_questions("fictional", n=8, seed=7,
                                          db_path=root / "processed" / "chunks.db",
                                          llm_client=lambda prompt: ("What confirmed delivery?", None),
                                          append_regression=regression)
    assert {"whole", "late", "phrase", "temporal", "regression"} <= {q["kind"] for q in questions}
    question = questions[0]
    hit = {"corpus": "fictional", "id": question["chunk_id"],
           "source_path": question["source_path"], "content": question["answer_span"]}
    report = run.evaluate_questions([question], search_fn=lambda *args, **kwargs: [hit])
    assert report["metrics"]["overall"]["strict"]["R@1"] == 1
    assert report["metrics"]["overall"]["span"]["R@1"] == 1
    assert report["metrics"]["overall"]["source"]["nDCG@10"] == 1
    assert report["metrics"]["overall"]["span"]["nDCG@10"] == 1
    baseline = run.evaluate_questions([question], search_fn=lambda *args, **kwargs: [])
    comparison = compare.compare_reports(baseline, report)
    assert comparison["overall"]["delta"]["strict_MRR"] == 1


def test_context_write_apply_status_prune(index, monkeypatch):
    conn, root = index
    chunk_id = insert_chunk(conn, "fictional.md", 0, "Amber relay confirmed receipt.",
                            vector(), {"source": str(root / "raw" / "fictional.md")})
    conn.commit()
    monkeypatch.setattr(context_chunks, "connect", lambda _: connect("fictional"))
    monkeypatch.setattr(context_chunks, "read_manifest", lambda _: {"chunking": {"mode": "document"}})
    saved = {}
    monkeypatch.setattr(context_chunks, "write_manifest", lambda topic, data: saved.update(data))
    monkeypatch.setattr(context_chunks, "generate_context", lambda prompt: "Cobalt dispatch confirmed delivery.")
    monkeypatch.setattr(context_chunks, "embed_texts_parallel", lambda texts: [vector(0, 1) for _ in texts])
    assert context_chunks.write("fictional", parallel=1) == 1
    assert context_chunks.write("fictional", parallel=1) == 0
    assert context_chunks.apply("fictional", batch=1) == 1
    assert context_chunks.status("fictional")["applied"] == 1
    assert saved["embedding"]["input"] == "context+content"
    assert search_bm25(conn, "cobalt dispatch")[0]["chunk_id"] == chunk_id
    conn.execute("DELETE FROM chunks WHERE id=?", (chunk_id,))
    conn.commit()
    assert context_chunks.prune("fictional") == 1


def test_context_hash_shared_by_two_sources(index, monkeypatch):
    conn, root = index
    text = "The fictional cobalt dispatch arrived."
    first = insert_chunk(conn, "fictional-a.md", 0, text, vector())
    second = insert_chunk(conn, "fictional-b.md", 0, text, vector())
    conn.commit()
    monkeypatch.setattr(context_chunks, "connect", lambda _: connect("fictional"))
    monkeypatch.setattr(context_chunks, "read_manifest", lambda _: {})
    monkeypatch.setattr(context_chunks, "write_manifest", lambda *args: None)
    monkeypatch.setattr(context_chunks, "generate_context", lambda prompt: "An ultraviolet shipment was recorded.")
    monkeypatch.setattr(context_chunks, "embed_texts_parallel", lambda texts: [vector(0, 1) for _ in texts])
    assert context_chunks.write("fictional", parallel=1) == 1
    assert context_chunks.apply("fictional", batch=1) == 2
    assert [row["chunk_id"] for row in search_bm25(conn, "ultraviolet shipment")] == [first, second]
