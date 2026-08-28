"""Import record-level labels into chunk metadata."""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
from pathlib import Path

from ..config import corpus_path


def aggregate_labels(
    labels_path: Path,
    id_key: str = "record_id",
    label_key: str = "label",
    subtype_key: str = "subtype",
) -> dict[str, dict]:
    """Aggregate JSONL labels by record identifier."""
    label_counts: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    subtype_counts: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    with labels_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            record_id = row[id_key]
            if row.get(label_key):
                label_counts[record_id][row[label_key]] += 1
            if row.get(subtype_key):
                subtype_counts[record_id][row[subtype_key]] += 1
    return {
        record_id: {
            "label": counts.most_common(1)[0][0],
            "labels": sorted(counts),
            "subtype": (
                subtype_counts[record_id].most_common(1)[0][0]
                if subtype_counts.get(record_id)
                else None
            ),
        }
        for record_id, counts in label_counts.items()
    }


def import_labels(
    corpus: str,
    labels_path: Path,
    metadata_id_key: str = "record_id",
    dry_run: bool = False,
) -> int:
    database = corpus_path(corpus) / "processed" / "chunks.db"
    if not database.is_file() or not labels_path.is_file():
        print("Index or labels file is missing.")
        return 1
    labels = aggregate_labels(labels_path)
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    updates: list[tuple[str, int]] = []
    for row in connection.execute("SELECT id, metadata_json FROM chunks"):
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            continue
        record_id = metadata.get(metadata_id_key)
        if record_id not in labels:
            continue
        metadata.update(labels[record_id])
        updates.append((json.dumps(metadata, ensure_ascii=False), row["id"]))
    print(f"Chunks to label: {len(updates)}")
    if not dry_run:
        connection.executemany(
            "UPDATE chunks SET metadata_json = ? WHERE id = ?", updates
        )
        connection.commit()
    connection.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus")
    parser.add_argument("labels", type=Path)
    parser.add_argument("--metadata-id-key", default="record_id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return import_labels(
        args.corpus, args.labels, args.metadata_id_key, args.dry_run
    )


if __name__ == "__main__":
    raise SystemExit(main())

