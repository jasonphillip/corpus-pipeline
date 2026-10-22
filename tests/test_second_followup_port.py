"""Fictional fixtures for structured cleaning and shadow evaluation."""

import json
import sqlite3

from corpus_pipeline.chunker import chunk_profiled_file
from corpus_pipeline.retrieval_eval import cli, run


def test_email_markup_is_cleaned_before_packing(tmp_path):
    path = tmp_path / "thread.md"
    css = "<style>td.tile { width: auto !important; }</style>" * 80
    path.write_text(
        "## 2025-02-03 09:10 — Pat <pat@example.com>\n" + css
        + "<!-- invisible --><p>The first useful sentence confirms the amber relay delivery.</p>\n"
        + "<script>window.track = true;</script>\n"
        + "## 2025-02-03 09:11 — Lee <lee@example.com>\n" + css
        + "<div>The second useful sentence confirms the cobalt relay delivery.</div>\n"
    )
    rows = chunk_profiled_file(path, "email_thread", {"subject": "Relay notice"}, 3000, 100)
    assert len(rows) == 2
    for row, sentence in zip(rows, ("first useful sentence", "second useful sentence")):
        assert sentence in row["content"]
        assert len(row["content"]) < 300
        assert "td.tile" not in row["content"]
        assert "<p>" not in row["content"]
        assert "window.track" not in row["content"]


def test_short_email_keeps_header_and_prefix(tmp_path):
    path = tmp_path / "short.md"
    path.write_text("## 2025-02-03 09:10 — Pat <pat@example.com>\n<p>OK.</p>\n")
    rows = chunk_profiled_file(path, "email_thread", {"subject": "Notice"}, 1000, 20)
    assert len(rows) == 1
    assert rows[0]["content"].endswith("## 2025-02-03 09:10 — Pat <pat@example.com>")
    assert rows[0]["metadata"]["prefix_len"] > 0


def test_shadow_scores_parent_and_records_alias(tmp_path, monkeypatch):
    question = {"qid": "q1", "kind": "whole", "corpus": "demo", "chunk_id": 3,
                "source_path": "fictional.md", "chunk_index": 0,
                "question": "Where is the amber relay?", "answer_span": "amber relay"}
    db = tmp_path / "demo--candidate" / "processed" / "chunks.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, source_path TEXT, "
                     "chunk_index INTEGER, content TEXT)")
        conn.execute("INSERT INTO chunks VALUES (3, 'fictional.md', 0, 'The amber relay arrived.')")
    monkeypatch.setattr(run, "corpus_path", lambda name: tmp_path / name)
    def search(*args, **kwargs):
        return [{"corpus": args[0][0], "chunk_id": 3,
                 "source_path": "fictional.md", "content": "The amber relay arrived."}]
    monkeypatch.setattr(run, "real_search", search)
    report = run.evaluate_questions([question], corpora=["demo--candidate"])
    assert (report["questions"][0]["strict_rank"], report["questions"][0]["source_rank"],
            report["questions"][0]["span_rank"]) == (1, 1, 1)
    assert run.parse_aliases(["candidate=demo"]) == {"candidate": "demo"}
    assert run.parent_mapping(["demo--candidate", "candidate"], {"candidate": "demo"}) == {
        "demo--candidate": "demo", "candidate": "demo"}

    questions = tmp_path / "questions.jsonl"
    questions.write_text(json.dumps(question) + "\n")
    output = tmp_path / "run.json"
    monkeypatch.setattr(cli, "read_manifest", lambda name: {})
    assert cli.main(["run", "--questions", str(questions), "--corpora", "candidate",
                     "--alias", "candidate=demo", "--out", str(output)]) == 0
    saved = json.loads(output.read_text())
    assert saved["config"]["parent_mapping"] == {"candidate": "demo"}
    assert (saved["questions"][0]["source_rank"], saved["questions"][0]["span_rank"]) == (1, 1)
