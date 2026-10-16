"""Metadata predicates must reduce both candidate sets before ranking."""

import struct

import pytest

from corpus_pipeline import corpus_db
from corpus_pipeline.commands.search import _parse_argv, _parse_metadata_filters


def _vec(x):
    return struct.pack("<2f", x, 1 - x)


def test_parser_combines_repeated_keys_and_imported_labels():
    args = _parse_argv(["sample", "--meta", "group=red", "--meta", "group=blue",
                        "--meta", "kind=note", "--label", "alpha,beta", "query"])
    assert _parse_metadata_filters(args) == [
        ("group", "red"), ("group", "blue"), ("kind", "note"),
        ("label", "alpha"), ("label", "beta")]
    args = _parse_argv(["sample", "--meta", "bad-key=value", "query"])
    with pytest.raises(ValueError, match="invalid metadata"):
        _parse_metadata_filters(args)


def test_rare_metadata_is_filtered_before_knn_and_bm25(tmp_path, monkeypatch):
    pytest.importorskip("sqlite_vec")
    monkeypatch.setattr(corpus_db, "corpus_path", lambda topic: tmp_path)
    conn = corpus_db.connect("sample")
    try:
        corpus_db.init_schema(conn, 2)
        for i in range(80):
            rare = i >= 77
            corpus_db.insert_chunk(
                conn, f"item_{i}.md", 0, f"sample retrieval item {i}",
                _vec(0.01 if rare else 0.99),
                {"group": "rare" if rare else "common", "kind": "note",
                 "label": "alpha" if rare else "other",
                 "labels": ["alpha", "beta"] if rare else ["other"]},
            )
        conn.commit()
        filters = [("group", "rare"), ("kind", "note"), ("label", "beta")]
        vector = corpus_db.search_vector(conn, _vec(1), limit=3, metadata_filters=filters)
        keyword = corpus_db.search_bm25(conn, "sample", limit=3, metadata_filters=filters)
        assert len(vector) == len(keyword) == 3
        assert {row["source_path"] for row in vector} == {
            "item_77.md", "item_78.md", "item_79.md"}
        assert {row["source_path"] for row in keyword} == {
            "item_77.md", "item_78.md", "item_79.md"}
        alternatives = corpus_db.search_vector(
            conn, _vec(1), limit=3,
            metadata_filters=[("group", "rare"), ("group", "common"), ("kind", "note")])
        assert len(alternatives) == 3
        assert corpus_db.search_bm25(
            conn, "sample", metadata_filters=[("group", "rare"), ("kind", "other")]) == []
    finally:
        conn.close()
