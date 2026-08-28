import json
import sqlite3

from corpus_pipeline.commands.first_mention import merge_and_rank, sql_chronological_scan


def test_chronological_scan_returns_oldest_matching_source(tmp_path):
    database = tmp_path / "chunks.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        "CREATE TABLE chunks(id INTEGER PRIMARY KEY, source_path TEXT, chunk_index INTEGER, content TEXT, metadata_json TEXT);"
        "CREATE VIRTUAL TABLE chunks_fts USING fts5(content, source_path UNINDEXED, chunk_id UNINDEXED);"
    )
    rows = [
        (1, "older.md", 0, "The vector index starts here.", json.dumps({"ts": "2024-01-01"})),
        (2, "newer.md", 0, "The vector index changes later.", json.dumps({"ts": "2024-06-01"})),
    ]
    connection.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)", rows)
    connection.executemany(
        "INSERT INTO chunks_fts(content, source_path, chunk_id) VALUES (?, ?, ?)",
        [(row[3], row[1], row[0]) for row in rows],
    )
    connection.commit()
    connection.close()
    results = sql_chronological_scan(database, ["vector index"])
    assert [result["source_path"] for result in results[:2]] == ["older.md", "newer.md"]


def test_merge_deduplicates_sources_and_sorts_dates():
    text = [{"source_path": "a.md", "ts": "2024-02", "match_method": "text"}]
    semantic = [
        {"source_path": "a.md", "ts": "2024-02", "match_method": "semantic"},
        {"source_path": "b.md", "ts": "2024-01", "match_method": "semantic"},
    ]
    merged = merge_and_rank(text, semantic, 10)
    assert [row["source_path"] for row in merged] == ["b.md", "a.md"]
    assert merged[1]["match_method"] == "both"

