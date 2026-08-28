#!/usr/bin/env python3
"""Build a source manifest from discovered sources.

Usage:
    echo '[{...sources...}]' | python3 build_manifest.py <topic>
    python3 build_manifest.py <topic> <<'EOF'
    [{...sources...}]
    EOF
"""

import json
import sys
from pathlib import Path

# parents[3]: scripts/ → skill/ → skills/ → project root (where lib/ lives)

from ..manifest import read_manifest, write_manifest, add_source
from ..config import corpus_path


def main():
    if len(sys.argv) < 2:
        print("Usage: build_manifest.py <topic>", file=sys.stderr)
        sys.exit(1)

    topic = sys.argv[1]

    # Read sources from stdin
    sources_json = sys.stdin.read().strip()
    if not sources_json:
        print("Error: no sources provided on stdin", file=sys.stderr)
        sys.exit(1)

    try:
        sources = json.loads(sources_json)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON on stdin — {e}", file=sys.stderr)
        print(f"Received ({len(sources_json)} chars): {sources_json[:200]}...", file=sys.stderr)
        sys.exit(1)
    manifest = read_manifest(topic)

    for src in sources:
        add_source(
            manifest,
            url=src["url"],
            source_type=src.get("type", "single_page"),
            method=src.get("method", "jina_reader"),
            priority=src.get("priority", 5),
            quality_score=src.get("quality_score", 0.5),
            quality_rationale=src.get("quality_rationale", ""),
            estimated_pages=src.get("estimated_pages", 10),
            content_date=src.get("content_date", ""),
        )

    # Sort by priority
    manifest["sources"].sort(key=lambda s: (s.get("priority", 99), -s.get("quality_score", 0)))

    # Ensure corpus directory exists
    corpus_path(topic).mkdir(parents=True, exist_ok=True)

    write_manifest(topic, manifest)

    # Print summary
    print(f"\nManifest written: {corpus_path(topic) / 'manifest.json'}")
    print(f"Total sources: {len(manifest['sources'])}")
    print(f"Estimated pages: {sum(s.get('estimated_pages', 0) for s in manifest['sources'])}")
    print("\nSources by priority:")
    for s in manifest["sources"]:
        score = s.get("quality_score", 0)
        print(f"  [{s.get('priority', '?')}] {s['url'][:70]}  (score: {score:.2f}, method: {s.get('method', '?')})")


if __name__ == "__main__":
    main()

