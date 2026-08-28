import json
import sqlite3
from pathlib import Path

from corpus_pipeline.hygiene import Hygiene, load_from_meta


def _policy(tmp_path: Path) -> Hygiene:
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "links.csv").write_text(
        "record_id,source_group,canonical_path\n"
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee,imports,records/original.md\n"
    )
    (meta / "hygiene.json").write_text(
        json.dumps(
            {
                "version": 1,
                "exclude": [{"glob": "generated/**", "reason": "generated"}],
                "flags": {
                    "link_table": "links.csv",
                    "id_column": "record_id",
                    "source_column": "source_group",
                    "target_column": "canonical_path",
                    "set": {"duplicate": True},
                },
            }
        )
    )
    policy = load_from_meta(meta)
    assert policy is not None
    return policy


def test_exclusions_and_link_flags(tmp_path):
    policy = _policy(tmp_path)
    assert policy.exclusion_reason("generated/output.md") == "generated"
    assert policy.exclusion_reason("notes/output.md") is None
    flags = policy.flags_for("imports/aaaaaaaabbbbccccddddeeeeeeeeeeee_t0001.md")
    assert flags == {"duplicate": True, "canonical_source": "records/original.md"}


def test_flags_apply_idempotently(tmp_path):
    policy = _policy(tmp_path)
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE chunks(id INTEGER, source_path TEXT, metadata_json TEXT)")
    connection.executemany(
        "INSERT INTO chunks VALUES (?, ?, ?)",
        [(1, "a.md", '{"ts": "2024-01-01"}'), (2, "a.md", "{}")],
    )
    assert policy.apply_flags(connection, "a.md", {"reviewed": True}) == 2
    assert policy.apply_flags(connection, "a.md", {"reviewed": True}) == 0

