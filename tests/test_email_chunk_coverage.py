"""Fictional regression fixtures for email-thread chunk coverage."""

import importlib.util
from pathlib import Path

from corpus_pipeline.chunker import chunk_profiled_file
from corpus_pipeline.commands.process import prepare_chunks


def _email(tmp_path, body, stamp="1970-01-01 00:00"):
    path = tmp_path / "thread.md"
    path.write_text(f"## {stamp} — Ada Example\n{body}\n")
    return path


def test_email_mode_falls_back_for_attachment_and_keeps_metadata(tmp_path):
    path = tmp_path / "invented_attachment.md"
    path.write_text("# Fixture notes\n\n" +
                    "The orchard group planned a fictional inventory review in spring. " * 4)
    rows = chunk_profiled_file(path, "email_thread", {}, 2500, 250)
    assert rows
    assert rows[0]["metadata"]["chunk_mode"] == "document"
    assert "fictional inventory review" in rows[0]["content"]


def test_email_mode_keeps_short_unstructured_text(tmp_path):
    path = tmp_path / "short.md"
    path.write_text("Invented note.")
    rows = chunk_profiled_file(path, "email_thread", {}, 2500, 250)
    assert rows[0]["metadata"]["chunk_mode"] == "document"
    assert "Invented note." in rows[0]["content"]


def test_email_mode_last_resort_keeps_nonblank_source(tmp_path):
    path = tmp_path / "symbols.md"
    path.write_text("<style>.card { color: red; }</style>")
    rows = chunk_profiled_file(path, "email_thread", {}, 2500, 250)
    assert rows[0]["metadata"]["chunk_mode"] == "generic"
    assert rows[0]["content"] == "<style>.card { color: red; }</style>"


def test_email_message_prose_survives_boilerplate_and_1970_date(tmp_path):
    prose = ("The fictional orchard group will deliver its report on Thursday. "
             "The report lists each completed repair and the next meeting time.")
    rows = chunk_profiled_file(_email(tmp_path, "Privacy Policy | " + prose),
                               "email_thread", {}, 3000, 300)
    assert rows and prose in rows[0]["content"]
    assert rows[0]["metadata"]["ts"] == "1970-01-01T00:00:00"
    assert rows[0]["metadata"]["chunk_mode"] == "email_thread"


def test_email_link_dense_text_retains_anchor_and_prose(tmp_path):
    body = "\n".join(
        f"[Invented article {i}: orchard planning details](https://example.test/read/{i}?"
        f"campaign={'x' * 25}) explains the next step for our fictional group."
        for i in range(8))
    rows = chunk_profiled_file(_email(tmp_path, body), "email_thread", {}, 3000, 300)
    combined = "\n".join(row["content"] for row in rows)
    assert "Invented article 0: orchard planning details" in combined
    assert "Invented article 7: orchard planning details" in combined
    assert "explains the next step" in combined
    assert "campaign=" not in combined


def test_email_light_clean_guardrail_keeps_text_when_aggressive_clean_loses_it(tmp_path):
    prose = "The fictional village approved the new garden schedule and shared it with every volunteer."
    body = ("<style>.card { color: red; }</style>\n" +
            "[Village update](https://example.test/read?campaign=" + "x" * 300 + ") " + prose)
    rows = chunk_profiled_file(_email(tmp_path, body), "email_thread", {}, 3000, 300)
    combined = "\n".join(row["content"] for row in rows)
    assert prose in combined
    assert "campaign=" in combined
    assert ".card" not in combined


def test_index_preparation_keeps_cleaned_email_prose(tmp_path):
    prose = "The fictional orchard group will deliver its detailed report on Thursday."
    rows, noise = prepare_chunks(_email(tmp_path, "Privacy Policy | " + prose),
                                 "email_thread", 3000, 300, {"mode": "email_thread"})
    assert noise == 0
    assert rows and prose in rows[0]["content"]


def test_coverage_script_reports_fictional_sample_without_index(tmp_path, monkeypatch, capsys):
    script = Path(__file__).resolve().parents[1] / "scripts" / "chunk_coverage.py"
    spec = importlib.util.spec_from_file_location("chunk_coverage", script)
    coverage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(coverage)
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "note.md").write_text("# Note\n\n" +
                                 "The fictional village recorded its garden schedule. " * 3)
    (raw / "thread.md").write_text(
        "## 1970-01-01 00:00 — Ada Example\n" +
        "The fictional orchard group completed its inventory and filed a report.\n")
    monkeypatch.setattr(coverage, "corpus_path", lambda _: tmp_path)
    coverage.measure("fictional", "email_thread", limit=2, seed=1)
    output = capsys.readouterr().out
    assert "files sampled: 2" in output
    assert "files with zero chunks: 0" in output
    assert "body alphabetic chars kept vs raw:" in output
    assert "five worst files by chars lost" in output
    assert "note.md" not in output and "thread.md" not in output
