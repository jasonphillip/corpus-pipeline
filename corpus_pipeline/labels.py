"""Durable imported labels kept outside the rebuildable chunk index."""

import json
from pathlib import Path

from .config import corpus_path


def registry_path(topic: str, base_dir: Path | None = None) -> Path:
    return (base_dir or corpus_path(topic)) / "meta" / "imported-labels.json"


def load_registry(topic: str, base_dir: Path | None = None) -> dict:
    path = registry_path(topic, base_dir)
    if not path.is_file():
        return {"version": 1, "records": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("records"), list):
        raise ValueError(f"invalid imported-label registry: {path}")
    return data


def save_registry(topic: str, data: dict, base_dir: Path | None = None) -> None:
    path = registry_path(topic, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def apply_imported_labels(registry: dict, source_path: str, metadata: dict,
                          source_hash: str) -> dict:
    """Overlay a matching record and mark unverifiable provenance explicitly."""
    for record in registry["records"]:
        if record["source_path"] != source_path:
            continue
        key = record["metadata_id_key"]
        if str(metadata.get(key)) != str(record["record_id"]):
            continue
        metadata.update(record["labels"])
        verified = (record.get("provenance") == "verified" and
                    record.get("source_hash") == source_hash)
        metadata["label_provenance"] = "verified" if verified else "unverified"
    return metadata
