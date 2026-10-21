"""Apply corpus chunking and search profiles from a YAML or JSON mapping."""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import re

import yaml

from ..config import CORPUS_ROOT

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_MODES = {"generic", "transcript", "email_thread", "chat_sessions", "document"}


def load_profiles(path: Path) -> dict[str, dict]:
    mapping = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("profile mapping must be a nonempty object")
    for topic, profile in mapping.items():
        if not isinstance(topic, str) or not _NAME.fullmatch(topic) or ".." in topic:
            raise ValueError(f"invalid corpus name: {topic!r}")
        if not isinstance(profile, dict) or not profile:
            raise ValueError(f"invalid profile for {topic}")
        if set(profile) - {"chunking", "search"}:
            raise ValueError(f"unsupported profile fields for {topic}")
        chunking = profile.get("chunking", {})
        if not isinstance(chunking, dict) or chunking.get("mode", "generic") not in _MODES:
            raise ValueError(f"invalid chunking profile for {topic}")
        if "search" in profile and not isinstance(profile["search"], dict):
            raise ValueError(f"invalid search profile for {topic}")
    return mapping


def apply_profiles(root: Path, mapping: dict[str, dict], dry_run: bool = False) -> list[str]:
    paths = {topic: root / topic / "manifest.json" for topic in mapping}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    changed = []
    for topic, profile in mapping.items():
        path = paths[topic]
        before = path.read_text(encoding="utf-8")
        manifest = json.loads(before)
        for field in ("chunking", "search"):
            if field in profile:
                manifest[field] = profile[field]
        after = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        if after == before:
            continue
        changed.append(topic)
        if dry_run:
            print("".join(difflib.unified_diff(before.splitlines(keepends=True),
                                               after.splitlines(keepends=True),
                                               fromfile=str(path), tofile=str(path))))
        else:
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(after, encoding="utf-8")
            temporary.replace(path)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corpus profiles apply")
    parser.add_argument("action", choices=["apply"])
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=CORPUS_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    print(f"changed: {', '.join(apply_profiles(args.root, load_profiles(args.mapping), args.dry_run)) or 'none'}")
    return 0
