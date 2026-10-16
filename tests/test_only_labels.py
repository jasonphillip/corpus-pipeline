"""Exact-file processing and durable imported labels."""

import hashlib
import json
import struct

import pytest

from corpus_pipeline import config, corpus_db, embedder, manifest, status
from corpus_pipeline.commands import import_labels, process
from corpus_pipeline.commands import context
from corpus_pipeline import cli


def _setup(tmp_path, monkeypatch):
    pytest.importorskip("sqlite_vec")
    topic = tmp_path / "sample"
    raw = topic / "raw"
    raw.mkdir(parents=True)
    for name in ("a", "b"):
        (raw / f"{name}.md").write_text(
            f"---\nrecord_id: {name}\n---\n\n" + f"Synthetic {name} content. " * 30)
    for module in (corpus_db, manifest, process, import_labels):
        monkeypatch.setattr(module, "corpus_path", lambda name: topic)
    monkeypatch.setattr(process, "get_embedding_config", lambda: {
        "chunk_size": 1000, "model": "fixture", "provider": "fixture"})
    monkeypatch.setattr(process, "get_dims", lambda: 2)
    monkeypatch.setattr(process, "get_label", lambda: "fixture")
    monkeypatch.setattr(process, "_embed_with_fallback", lambda texts: (
        [struct.pack("<2f", 1, 0)] * len(texts), 0))
    return topic


def _rows():
    conn = corpus_db.connect("sample")
    try:
        return {r["source_path"]: (r["id"], json.loads(r["metadata_json"]))
                for r in conn.execute("SELECT id, source_path, metadata_json FROM chunks")}
    finally:
        conn.close()


def test_only_reprocesses_exact_paths_and_validates_before_writes(tmp_path, monkeypatch):
    topic = _setup(tmp_path, monkeypatch)
    process.process_topic("sample")
    before = _rows()
    paths = tmp_path / "paths.txt"
    paths.write_text("a.md\n")
    process.process_topic("sample", only=paths)
    after = _rows()
    assert after["a.md"][0] != before["a.md"][0]
    assert after["b.md"] == before["b.md"]
    manifest_before = (topic / "manifest.json").read_bytes()
    log_before = (topic / "meta" / "processing.log").read_bytes()
    paths.write_text("a.md\nmissing.md\n")
    with pytest.raises(ValueError, match="missing"):
        process.process_topic("sample", only=paths)
    assert _rows() == after
    assert (topic / "manifest.json").read_bytes() == manifest_before
    assert (topic / "meta" / "processing.log").read_bytes() == log_before


def test_imported_labels_survive_reprocess_and_rebuild(tmp_path, monkeypatch):
    topic = _setup(tmp_path, monkeypatch)
    process.process_topic("sample")
    raw = topic / "raw" / "a.md"
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    labels_file = tmp_path / "labels.jsonl"
    labels_file.write_text(json.dumps({"record_id": "a", "label": "alpha",
                                       "source_hash": digest}) + "\n")
    assert import_labels.import_labels("sample", labels_file) == 0
    assert _rows()["a.md"][1]["label_provenance"] == "verified"
    paths = tmp_path / "paths.txt"
    paths.write_text("a.md\n")
    process.process_topic("sample", only=paths)
    assert _rows()["a.md"][1]["label"] == "alpha"
    assert _rows()["a.md"][1]["label_provenance"] == "verified"
    raw.write_text(raw.read_text() + "\nA changed sentence.")
    process.process_topic("sample", only=paths)
    assert _rows()["a.md"][1]["label_provenance"] == "unverified"
    (topic / "processed" / "chunks.db").unlink()
    process.process_topic("sample")
    assert _rows()["a.md"][1]["label"] == "alpha"
    assert _rows()["a.md"][1]["label_provenance"] == "unverified"


def test_import_without_source_digest_is_unverified(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    process.process_topic("sample")
    labels_file = tmp_path / "labels.jsonl"
    labels_file.write_text('{"record_id":"a","label":"beta"}\n')
    assert import_labels.import_labels("sample", labels_file) == 0
    assert _rows()["a.md"][1]["label_provenance"] == "unverified"


def test_cli_only_skips_global_context_build(tmp_path, monkeypatch):
    topic = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "corpus_path", lambda name: topic)
    monkeypatch.setattr(embedder, "get_label", lambda: "fixture")
    monkeypatch.setattr(status, "update_corpus_status", lambda *args: None)
    monkeypatch.setattr(context, "build_context", lambda *args: (_ for _ in ()).throw(
        AssertionError("global context must be skipped")))
    paths = tmp_path / "paths.txt"
    paths.write_text("a.md\n")
    assert cli.main(["process", "sample", "--only", str(paths)]) == 0
    assert set(_rows()) == {"a.md"}
