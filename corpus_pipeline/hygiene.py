"""Apply optional per-corpus exclusion and metadata policies during indexing.

A corpus can define ``meta/hygiene.json``. Exclusion rules act only on the
search index. Raw files remain unchanged.

Example version 1 manifest::

    {
      "version": 1,
      "exclude": [{"glob": "generated/**", "reason": "generated"}],
      "flags": {
        "link_table": "source-links.csv",
        "id_column": "record_id",
        "source_column": "source_group",
        "target_column": "canonical_path",
        "set": {"duplicate": true}
      }
    }
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from fnmatch import fnmatchcase
from pathlib import Path


TURN_RE = re.compile(r"^(?P<stem>.+?)_t\d{4}$")
HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


class Hygiene:
    """Compiled hygiene policy for one corpus."""

    def __init__(self, manifest: dict, meta_dir: Path):
        self.exclude = manifest.get("exclude", [])
        flags = manifest.get("flags") or {}
        self.flag_set: dict = flags.get("set", {})
        self.links: dict[tuple[str, str], str] = {}
        table = flags.get("link_table")
        id_column = flags.get("id_column", "record_id")
        source_column = flags.get("source_column", "source_group")
        target_column = flags.get("target_column", "canonical_path")
        if table and (meta_dir / table).is_file():
            with (meta_dir / table).open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    record_id = row.get(id_column, "").replace("-", "")
                    source_group = row.get(source_column, "")
                    target = row.get(target_column, "")
                    if record_id and source_group and target:
                        self.links.setdefault((source_group, record_id), target)

    def exclusion_reason(self, relative_path: str) -> str | None:
        """Return the reason when a path matches an exclusion rule."""
        for rule in self.exclude:
            if fnmatchcase(relative_path, rule["glob"]):
                return rule.get("reason", "excluded")
        return None

    def excluded_sources(self, connection: sqlite3.Connection) -> list[str]:
        """Return indexed sources that now match an exclusion rule."""
        rows = connection.execute("SELECT source_path FROM sources").fetchall()
        return sorted(row[0] for row in rows if self.exclusion_reason(row[0]))

    def flags_for(self, relative_path: str) -> dict:
        """Return metadata flags for a raw-relative source path."""
        parts = Path(relative_path).parts
        if len(parts) < 2:
            return {}
        match = TURN_RE.match(Path(relative_path).stem)
        stem = match.group("stem") if match else Path(relative_path).stem
        if not HEX32_RE.match(stem):
            return {}
        canonical = self.links.get((parts[0], stem))
        if canonical is None:
            return {}
        return {**self.flag_set, "canonical_source": canonical}

    def apply_flags(
        self, connection: sqlite3.Connection, source_path: str, flags: dict
    ) -> int:
        """Merge flags into existing chunk metadata and return update count."""
        updated = 0
        rows = connection.execute(
            "SELECT id, metadata_json FROM chunks WHERE source_path = ?",
            (source_path,),
        ).fetchall()
        for chunk_id, metadata_json in rows:
            metadata = json.loads(metadata_json or "{}")
            if all(metadata.get(key) == value for key, value in flags.items()):
                continue
            metadata.update(flags)
            connection.execute(
                "UPDATE chunks SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata), chunk_id),
            )
            updated += 1
        return updated


def load_from_meta(meta_dir: Path) -> Hygiene | None:
    """Load ``hygiene.json`` from a corpus metadata directory."""
    path = meta_dir / "hygiene.json"
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return Hygiene(manifest, meta_dir)


def load_hygiene(topic: str) -> Hygiene | None:
    """Load the policy for a topic, when present."""
    from .config import corpus_path

    return load_from_meta(corpus_path(topic) / "meta")

