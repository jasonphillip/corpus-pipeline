#!/usr/bin/env python3
"""Diff a corpus against its previous state for refresh operations.

Usage:
    diff_corpus.py snapshot <topic>   — save current raw/ state before re-acquisition
    diff_corpus.py diff <topic>       — compare current raw/ against saved snapshot
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


from ..config import corpus_path


def file_hash(path: Path) -> str:
    """SHA256 of file content."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_hashes(raw_dir: Path) -> dict[str, str]:
    """Build a dict of relative_path -> content_hash for all files."""
    hashes = {}
    if raw_dir.exists():
        for f in raw_dir.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(raw_dir))
                hashes[rel] = file_hash(f)
    return hashes


def cmd_snapshot(topic: str):
    """Save a snapshot of the current raw/ state."""
    raw_dir = corpus_path(topic) / "raw"
    meta_dir = corpus_path(topic) / "meta"
    history_dir = meta_dir / "refresh-history"
    history_dir.mkdir(parents=True, exist_ok=True)

    print(f"Snapshotting current state of '{topic}'...")
    before = snapshot_hashes(raw_dir)
    print(f"  Current files: {len(before)}")

    snapshot_file = history_dir / "pre-refresh-snapshot.json"
    snapshot_file.write_text(json.dumps(before, indent=2))
    print(f"  Snapshot saved: {snapshot_file}")


def cmd_diff(topic: str) -> dict:
    """Compare current raw/ against saved snapshot. Returns change summary."""
    raw_dir = corpus_path(topic) / "raw"
    meta_dir = corpus_path(topic) / "meta"
    history_dir = meta_dir / "refresh-history"

    # Load pre-refresh snapshot
    snapshot_file = history_dir / "pre-refresh-snapshot.json"
    if not snapshot_file.exists():
        print(f"Error: no snapshot found at {snapshot_file}", file=sys.stderr)
        print("Run 'diff_corpus.py snapshot <topic>' before re-acquisition.", file=sys.stderr)
        sys.exit(1)

    before = json.loads(snapshot_file.read_text())
    print(f"Loaded snapshot: {len(before)} files")

    # Current state after re-acquisition
    after = snapshot_hashes(raw_dir)
    print(f"Current state: {len(after)} files")

    # Compute diff
    before_set = set(before.keys())
    after_set = set(after.keys())

    added = after_set - before_set
    removed = before_set - after_set
    common = before_set & after_set
    modified = {f for f in common if before[f] != after[f]}
    unchanged = common - modified

    changes = {
        "added": sorted(added),
        "removed": sorted(removed),
        "modified": sorted(modified),
        "unchanged": len(unchanged),
        "total_before": len(before),
        "total_after": len(after),
        "has_changes": bool(added or removed or modified),
    }

    # Write diff report
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    report_file = history_dir / f"{date_str}.diff.md"

    report = [
        f"# Refresh Diff — {topic}",
        f"Date: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"",
        f"## Summary",
        f"- Files before: {len(before)}",
        f"- Files after: {len(after)}",
        f"- **Added:** {len(added)}",
        f"- **Removed:** {len(removed)}",
        f"- **Modified:** {len(modified)}",
        f"- Unchanged: {len(unchanged)}",
        f"",
    ]

    if added:
        report.append("## Added Files")
        for f in sorted(added):
            report.append(f"- `{f}`")
        report.append("")

    if removed:
        report.append("## Removed Files")
        for f in sorted(removed):
            report.append(f"- `{f}`")
        report.append("")

    if modified:
        report.append("## Modified Files")
        for f in sorted(modified):
            report.append(f"- `{f}`")
        report.append("")

    if not changes["has_changes"]:
        report.append("**No changes detected.** Corpus is up to date.")

    report_file.write_text("\n".join(report) + "\n")

    # Print summary
    print(f"\n{'='*50}")
    print(f"Diff report: {report_file}")
    print(f"  Added: {len(added)}")
    print(f"  Removed: {len(removed)}")
    print(f"  Modified: {len(modified)}")
    print(f"  Unchanged: {len(unchanged)}")

    if changes["has_changes"]:
        print("\nChanges detected — re-processing recommended.")
    else:
        print("\nNo changes — corpus is current.")

    return changes


def main():
    if len(sys.argv) < 3:
        print("Usage: diff_corpus.py <snapshot|diff> <topic>", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    topic = sys.argv[2]

    if cmd == "snapshot":
        cmd_snapshot(topic)
    elif cmd == "diff":
        changes = cmd_diff(topic)
        sys.exit(0 if not changes["has_changes"] else 1)
    else:
        print(f"Unknown command: {cmd}. Use 'snapshot' or 'diff'.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

