import sqlite3

from corpus_pipeline.commands import health


def test_report_has_frontmatter_and_remediation():
    metrics = [
        {
            "corpus": "my-docs",
            "status": "ok",
            "quick_check": "ok",
            "total_chunks": 2,
            "fts_rows": 1,
            "missing_vec_rows": 0,
            "dim_mismatch": False,
            "orphan_chunks_no_source": 0,
            "metadata_sample_size": 0,
            "metadata_keys": {},
        }
    ]
    report = health.render_md(metrics, "all", "2024-01-01T00:00:00+00:00")
    assert report.startswith("---\n")
    assert "Remediation" in report
    assert "FTS row count" in report


def test_same_day_persistence_replaces_prior_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "health"
    monkeypatch.setattr(health, "HEALTH_ROOT", root)
    monkeypatch.setattr(health, "HEALTH_DB", root / "corpus-health.db")
    metrics = [{"corpus": "my-docs", "status": "ok", "total_chunks": 1}]
    health.persist(metrics, dedupe_day=True)
    metrics[0]["total_chunks"] = 2
    health.persist(metrics, dedupe_day=True)
    connection = sqlite3.connect(health.HEALTH_DB)
    count = connection.execute(
        "SELECT COUNT(*) FROM metrics WHERE corpus='my-docs' AND key='total_chunks'"
    ).fetchone()[0]
    value = connection.execute(
        "SELECT value FROM metrics WHERE corpus='my-docs' AND key='total_chunks'"
    ).fetchone()[0]
    connection.close()
    assert count == 1
    assert value == "2"

