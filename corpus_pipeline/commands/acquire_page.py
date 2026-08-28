#!/usr/bin/env python3
"""Acquire single pages via Jina Reader (r.jina.ai)."""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx


from ..config import corpus_path, JINA_READER_PREFIX
from ..content_filter import is_target_language, is_valid_content
from ..source_url import jina_reader_url


def slugify(text: str) -> str:
    """Convert text to filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    return text[:80] or "page"


def acquire_page(topic: str, url: str) -> Path | None:
    """Download a single page via Jina Reader, save as markdown."""
    jina_url = jina_reader_url(url, JINA_READER_PREFIX)
    try:
        r = httpx.get(jina_url, timeout=30.0, follow_redirects=True,
                      headers={"Accept": "text/markdown"})
        r.raise_for_status()
        content = r.text
    except Exception as e:
        print(f"  FAIL: {url} — {e}", file=sys.stderr)
        return None

    if not content.strip():
        print(f"  SKIP: {url} — empty response", file=sys.stderr)
        return None

    if not is_target_language(content):
        print(f"  SKIP: {url} — non-target language", file=sys.stderr)
        return None

    if not is_valid_content(content):
        print(f"  SKIP: {url} — insufficient content quality", file=sys.stderr)
        return None

    # Determine output path
    parsed = urlparse(url)
    domain = parsed.netloc.replace(":", "_")
    path_slug = slugify(parsed.path.strip("/")) or "index"
    out_dir = corpus_path(topic) / "raw" / domain
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{path_slug}.md"

    out_file.write_text(content, encoding="utf-8")
    print(f"  OK: {url} → {out_file.relative_to(corpus_path(topic))}")
    return out_file


def main():
    if len(sys.argv) < 3:
        print("Usage: acquire_jina.py <topic> <url1> [url2] ...", file=sys.stderr)
        sys.exit(1)

    topic = sys.argv[1]
    urls = sys.argv[2:]

    print(f"Acquiring {len(urls)} page(s) via Jina Reader for '{topic}'...")
    acquired = 0
    for url in urls:
        result = acquire_page(topic, url)
        if result:
            acquired += 1

    print(f"\nDone: {acquired}/{len(urls)} pages acquired")


if __name__ == "__main__":
    main()

