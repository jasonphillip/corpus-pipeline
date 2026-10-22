"""Fictional regression tests for shadow-corpus evaluation."""

import json
import sqlite3

from corpus_pipeline.retrieval_eval import build, run


def test_shadow_build_writes_parent_and_run_scores_legacy_shadow_question(tmp_path):
    db = tmp_path / "chunks.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, source_path TEXT, "
                 "chunk_index INTEGER, content TEXT, metadata_json TEXT)")
    passage = ("The fictional orchard group finished its inventory and filed the report. " * 8)
    conn.execute("INSERT INTO chunks VALUES (1, 'fictional.md', 0, ?, ?)",
                 (passage, json.dumps({"date": "2026-01-01"})))
    conn.commit()
    conn.close()
    regression = tmp_path / "regression.jsonl"
    regression.write_text(json.dumps({"question": "Which fictional report?", "chunk_id": 1}) + "\n")
    records, _ = build.build_questions("orchard--shadow", n=1, db_path=db,
                                        llm_client=lambda _: "What did the orchard group file?",
                                        append_regression=regression)
    assert len(records) == 2
    assert {record["corpus"] for record in records} == {"orchard"}

    legacy = {**records[0], "corpus": "orchard--shadow"}

    def fake_search(*_):
        return [{"corpus": "orchard--shadow", "id": 1,
                 "source_path": "fictional.md", "content": passage}]

    report = run.evaluate_questions([legacy], fake_search)
    row = report["questions"][0]
    assert (row["strict_rank"], row["source_rank"], row["span_rank"]) == (1, 1, 1)
