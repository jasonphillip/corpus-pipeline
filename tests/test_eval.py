"""Evaluation arithmetic and passage validation on a tiny fake-index run."""

import json
import math

import pytest

from eval.run_eval import load_judgments, run_evaluation, score_ranking


def _tiny_dataset(tmp_path):
    dataset = tmp_path / "dataset"
    docs = dataset / "docs"
    docs.mkdir(parents=True)
    for name, word in (("a.md", "alpha"), ("b.md", "bravo"), ("c.md", "charlie")):
        (docs / name).write_text(f"# {word}\n\n" + f"{word} information. " * 12)
    rows = [{"id": "q1", "query": "alpha information", "split": "eval",
             "relevant": [{"doc": "a.md", "passage": "alpha information."},
                          {"doc": "b.md", "passage": "bravo information."}]},
            {"id": "q2", "query": "charlie", "split": "tune",
             "relevant": [{"doc": "c.md", "passage": "charlie information."}]}]
    (dataset / "queries.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    return dataset


def test_document_metrics_hand_computed():
    score = score_ranking(["a.md", "a.md", "c.md", "b.md"], {"a.md", "b.md"})
    expected_ndcg = (1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
    assert score["recall_at_5"] == score["recall_at_10"] == 1
    assert score["ndcg_at_10"] == pytest.approx(expected_ndcg)
    assert score["relevant_document_count"] == 2
    assert score["relevant_doc_ranks"] == {"a.md": 1, "b.md": 3}


def test_fake_run_three_documents(tmp_path):
    result, path = run_evaluation(_tiny_dataset(tmp_path), tmp_path / "results", True)
    assert result["corpus"] == {"documents": 3, "chunks": 3}
    assert result["split"]["eval_queries"] == 1
    assert result["split"]["tune_queries"] == 1
    assert result["modes"]["bm25"]["query_count"] == 1
    assert result["modes"]["bm25"]["recall_at_5"] == 1.0
    assert result["modes"]["bm25"]["ndcg_at_10"] == 1.0
    assert result["modes"]["hybrid_rerank"]["status"] == "unrun"
    assert result["embedder"] == "fake (mechanics only)"
    assert "not retrieval quality" in path.with_suffix(".md").read_text()


def test_passage_check_rejects_mismatch(tmp_path):
    dataset = _tiny_dataset(tmp_path)
    query_file = dataset / "queries.jsonl"
    query_file.write_text(query_file.read_text().replace("bravo information.", "absent passage"))
    with pytest.raises(ValueError, match="judged passage absent: q1 b.md"):
        load_judgments(dataset)
