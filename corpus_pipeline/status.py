"""Render a compact corpus status index."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .config import CORPUS_ROOT, STATUS_FILE


STATUSES = ["researched", "acquired", "processed", "searchable"]


def update_corpus_status(topic: str, action: str, stats: dict | None = None) -> None:
    """Update the configured status file for one corpus."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    topics = _parse_frontmatter(STATUS_FILE.read_text()) if STATUS_FILE.is_file() else {}
    entry = topics.get(topic, {})
    entry.update(
        {
            "status": _status_from_action(action),
            "corpus_path": str(CORPUS_ROOT / topic),
            "last_updated": now,
            **(stats or {}),
        }
    )
    topics[topic] = entry
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(_render(topics, action, now), encoding="utf-8")


def _status_from_action(action: str) -> str:
    first = action.lower().split()[0] if action else ""
    if "research" in first:
        return "researched"
    if "acquire" in first:
        return "acquired"
    if "process" in first:
        return "processed"
    if "search" in first or "refresh" in first:
        return "searchable"
    return "researched"


def _parse_frontmatter(text: str) -> dict:
    topics: dict = {}
    match = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return topics
    current_topic: str | None = None
    for line in match.group(1).splitlines():
        topic_match = re.match(r"^  (\S+):\s*$", line)
        if topic_match:
            current_topic = topic_match.group(1)
            topics[current_topic] = {}
            continue
        property_match = re.match(r"^    (\S+):\s*(.+)$", line)
        if property_match and current_topic:
            key, value = property_match.groups()
            value = value.strip().strip('"')
            try:
                value = int(value)
            except ValueError:
                pass
            topics[current_topic][key] = value
    return topics


def _render(topics: dict, last_action: str, now: str) -> str:
    lines = ["---", f"updated: {now}", f'last_action: "{last_action}"', "topics:"]
    for topic, entry in sorted(topics.items()):
        lines.append(f"  {topic}:")
        for key, value in entry.items():
            rendered = f'"{value}"' if isinstance(value, str) else value
            lines.append(f"    {key}: {rendered}")
    lines.extend(["---", "", "# Corpus Pipeline Status", ""])
    for topic, entry in sorted(topics.items()):
        lines.append(f"## {topic} — {str(entry.get('status', 'unknown')).capitalize()}")
        for key, label in (
            ("sources", "Sources"),
            ("raw_files", "Raw files"),
            ("chunks", "Chunks"),
            ("context_tokens", "Context tokens"),
            ("embed_model", "Embedding model"),
            ("last_updated", "Last updated"),
        ):
            if entry.get(key):
                lines.append(f"- **{label}:** {entry[key]}")
        lines.append("")
    return "\n".join(lines) + "\n"

