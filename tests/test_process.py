from datetime import date

from corpus_pipeline.commands.process import _coerce_yaml, _file_hash, parse_frontmatter


def test_frontmatter_is_loaded_and_dates_are_serializable(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("---\nts: 2024-01-02\ntags: [one, two]\n---\nBody")
    metadata = parse_frontmatter(path)
    assert metadata["ts"] == "2024-01-02"
    assert metadata["tags"] == ["one", "two"]


def test_yaml_coercion_is_recursive():
    assert _coerce_yaml({"when": date(2024, 2, 3)}) == {"when": "2024-02-03"}


def test_file_hash_changes_with_content(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("first")
    before = _file_hash(path)
    path.write_text("second")
    assert _file_hash(path) != before

