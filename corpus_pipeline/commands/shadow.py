"""Create an isolated A/B corpus whose raw directory links to the source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from ..config import CORPUS_ROOT
from .profiles import load_profiles

_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")


def create_shadow(root: Path, topic: str, suffix: str,
                  mapping: dict[str, dict] | None = None) -> Path:
    if not all(_NAME.fullmatch(value) for value in (topic, suffix)):
        raise ValueError("topic and suffix must use lowercase letters, digits, and hyphens")
    source = root / topic
    target = root / f"{topic}--{suffix}"
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    raw = source / "raw"
    manifest_path = source / "manifest.json"
    if not raw.is_dir() or not manifest_path.is_file():
        raise FileNotFoundError(f"source raw or manifest missing: {source}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["topic"] = target.name
    for field, value in (mapping or {}).get(topic, {}).items():
        manifest[field] = value
    target.mkdir()
    try:
        (target / "raw").symlink_to(raw.resolve(), target_is_directory=True)
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    except BaseException:
        (target / "raw").unlink(missing_ok=True)
        (target / "manifest.json").unlink(missing_ok=True)
        target.rmdir()
        raise
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corpus shadow")
    parser.add_argument("topic")
    parser.add_argument("suffix")
    parser.add_argument("--root", type=Path, default=CORPUS_ROOT)
    parser.add_argument("--mapping", type=Path)
    args = parser.parse_args(argv)
    mapping = load_profiles(args.mapping) if args.mapping else None
    print(create_shadow(args.root, args.topic, args.suffix, mapping))
    return 0
