"""Source manifest operations — read, write, update, score."""

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import corpus_path


def manifest_path(topic: str) -> Path:
    return corpus_path(topic) / "manifest.json"


def read_manifest(topic: str) -> dict:
    """Read existing manifest or return empty structure."""
    p = manifest_path(topic)
    if p.exists():
        return json.loads(p.read_text())
    return {"topic": topic, "created": _now(), "sources": []}


def write_manifest(topic: str, manifest: dict):
    """Write manifest to disk."""
    p = manifest_path(topic)
    p.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated"] = _now()
    p.write_text(json.dumps(manifest, indent=2) + "\n")


def add_source(manifest: dict, url: str, source_type: str, method: str,
               priority: int = 5, quality_score: float = 0.5,
               quality_rationale: str = "", estimated_pages: int = 0,
               content_date: str = "") -> dict:
    """Add a source to the manifest, deduplicating by URL."""
    # Check for existing
    for s in manifest["sources"]:
        if s.get("url") == url:
            s.update({
                "type": source_type, "method": method, "priority": priority,
                "quality_score": quality_score, "quality_rationale": quality_rationale,
                "estimated_pages": estimated_pages, "content_date": content_date,
            })
            return manifest

    manifest["sources"].append({
        "url": url,
        "type": source_type,
        "method": method,
        "priority": priority,
        "quality_score": quality_score,
        "quality_rationale": quality_rationale,
        "estimated_pages": estimated_pages,
        "content_date": content_date,
        "status": "pending",
    })
    return manifest


def update_source_status(manifest: dict, url: str, status: str,
                         files_acquired: int = 0) -> dict:
    """Update acquisition status for a source."""
    for s in manifest["sources"]:
        if s.get("url") == url:
            s["status"] = status
            s["acquired_at"] = _now()
            if files_acquired:
                s["files_acquired"] = files_acquired
            break
    return manifest


def sources_by_priority(manifest: dict) -> list:
    """Return sources sorted by priority (lower = higher priority)."""
    return sorted(manifest.get("sources", []), key=lambda s: s.get("priority", 99))


def pending_sources(manifest: dict) -> list:
    """Return sources that haven't been acquired yet."""
    return [s for s in manifest.get("sources", [])
            if s.get("status", "pending") == "pending"]


def set_embedding_provenance(manifest: dict, model: str, dim: int, provider: str) -> dict:
    """Record which embedding model + dim a corpus's chunks.db was built with.

    Lets future tooling detect mismatches (e.g. searching a 4096-dim corpus
    with a 2560-dim query) and migrate corpora individually rather than
    treating the whole pipeline as a single global config.
    """
    manifest["embedding"] = {
        "model": model,
        "dim": dim,
        "provider": provider,
        "indexed_at": _now(),
    }
    return manifest


def get_embedding_provenance(manifest: dict) -> dict | None:
    """Return the recorded embedding model+dim for a corpus, or None if not set."""
    emb = manifest.get("embedding")
    return emb if isinstance(emb, dict) else None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

