"""Import record-level labels into chunk metadata."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sqlite3
from pathlib import Path

from ..config import corpus_path
from ..labels import load_registry, save_registry


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
    declared: dict[str, set[str]] = collections.defaultdict(set)
    with labels_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                item = json.loads(line)
                declared[str(item["record_id"])].add(str(item.get("source_hash") or ""))
    registry = load_registry(corpus, corpus_path(corpus))
    records = {(row["source_path"], row["metadata_id_key"], str(row["record_id"])): row
               for row in registry["records"]}
    connection = sqlite3.connect(str(database))
    connection.row_factory = sqlite3.Row
    updates: list[tuple[str, int]] = []
    source_hashes = dict(connection.execute("SELECT source_path, source_hash FROM sources"))
    raw_root = corpus_path(corpus) / "raw"
    current_hashes: dict[str, str] = {}
    for row in connection.execute("SELECT id, source_path, metadata_json FROM chunks"):
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            continue
        record_id = metadata.get(metadata_id_key)
        if record_id not in labels:
            continue
        source = row["source_path"]
        if source not in current_hashes:
            raw = raw_root / source
            current_hashes[source] = (
                hashlib.sha256(raw.read_bytes()).hexdigest()
                if raw.resolve().is_relative_to(raw_root.resolve()) and raw.is_file()
                else "")
        current_hash = current_hashes[source]
        supplied = declared.get(str(record_id), set())
        verified = (len(supplied) == 1 and current_hash in supplied and
                    current_hash == source_hashes.get(source))
        metadata.update(labels[record_id])
        metadata["label_provenance"] = "verified" if verified else "unverified"
        updates.append((json.dumps(metadata, ensure_ascii=False), row["id"]))
        records[(source, metadata_id_key, str(record_id))] = {
            "source_path": source, "metadata_id_key": metadata_id_key,
            "record_id": record_id, "labels": labels[record_id],
            "source_hash": current_hash,
            "provenance": metadata["label_provenance"]}
    print(f"Chunks to label: {len(updates)}")
    if not dry_run:
        registry["records"] = list(records.values())
        save_registry(corpus, registry, corpus_path(corpus))
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
