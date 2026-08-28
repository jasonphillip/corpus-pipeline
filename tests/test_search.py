from corpus_pipeline.commands.search import RRF_K, _parse_argv, _rrf_merge
from corpus_pipeline.config import SEARCH_TOP_K
from corpus_pipeline.corpus_db import _build_fts_query, _caption_filter


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


def test_search_parser_accepts_flags_after_topic():
    args = _parse_argv(["my-docs", "--captions-only", "block", "diagram"])
    assert args.captions_mode == "only"
    assert args.rest == ["my-docs", "block", "diagram"]
    assert args.top_k == SEARCH_TOP_K


def test_fts_query_quotes_special_tokens():
    assert _build_fts_query("C++ vue.js") == '"C++" AND "vue.js"'


def test_caption_filter_modes():
    assert _caption_filter("include", "content") == ("", [])
    clause, values = _caption_filter("exclude", "content")
    assert "NOT LIKE" in clause and values

