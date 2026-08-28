"""Copy Markdown files while adding normalized timestamp metadata."""

from __future__ import annotations

import argparse
from pathlib import Path


def bridge_one(
    markdown: str, source_label: str, timestamp_key: str = "create_time"
) -> str | None:
    """Add ``source_tool``, ``created_at``, and ``ts`` to frontmatter."""
    if not markdown.startswith("---"):
        return None
    end = markdown.find("\n---", 3)
    if end == -1:
        return None
    block = markdown[3:end]
    rest = markdown[end:]
    lines = block.splitlines()
    existing = {line.split(":", 1)[0].strip() for line in lines if ":" in line}
    timestamp = ""
    for line in lines:
        if line.strip().startswith(f"{timestamp_key}:"):
            timestamp = line.split(":", 1)[1].strip().strip('"')
            break
    additions: list[str] = []
    if "source_tool" not in existing:
        additions.append(f'source_tool: "{source_label}"')
    if timestamp:
        for key in ("created_at", "ts"):
            if key not in existing:
                additions.append(f'{key}: "{timestamp}"')
    if not additions:
        return markdown
    return "---" + block + "\n" + "\n".join(additions) + rest


def bridge_directory(
    source: Path,
    destination: Path,
    source_label: str,
    timestamp_key: str,
    file_list: Path | None = None,
) -> tuple[int, int]:
    """Bridge selected Markdown files and return ``(written, skipped)``."""
    names = (
        [line.strip() for line in file_list.read_text().splitlines() if line.strip()]
        if file_list
        else [str(path.relative_to(source)) for path in sorted(source.rglob("*.md"))]
    )
    destination.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for name in names:
        input_path = source / name
        if not input_path.is_file():
            skipped += 1
            continue
        bridged = bridge_one(
            input_path.read_text(encoding="utf-8", errors="replace"),
            source_label,
            timestamp_key,
        )
        if bridged is None:
            skipped += 1
            continue
        output_path = destination / name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(bridged, encoding="utf-8")
        written += 1
    return written, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--timestamp-key", default="create_time")
    parser.add_argument("--file-list", type=Path)
    args = parser.parse_args(argv)
    written, skipped = bridge_directory(
        args.source,
        args.destination,
        args.source_label,
        args.timestamp_key,
        args.file_list,
    )
    print(f"Wrote {written} files; skipped {skipped}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

