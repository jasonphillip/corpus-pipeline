"""Acquire repository documentation and individual web pages."""

from __future__ import annotations

import argparse

from ..source_url import is_git_repo_url
from .acquire_git import acquire_repo
from .acquire_page import acquire_page
from .finalize import finalize_acquisition


def acquire(topic: str, urls: list[str]) -> tuple[int, int]:
    """Acquire URLs and return ``(repository_files, pages)``."""
    repository_files = 0
    pages = 0
    for url in urls:
        if is_git_repo_url(url):
            repository_files += acquire_repo(topic, url)
        elif acquire_page(topic, url) is not None:
            pages += 1
    finalize_acquisition(topic)
    return repository_files, pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic")
    parser.add_argument("urls", nargs="+")
    args = parser.parse_args(argv)
    repository_files, pages = acquire(args.topic, args.urls)
    print(f"Acquired {repository_files} repository files and {pages} pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

