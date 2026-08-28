#!/usr/bin/env python3
"""Acquire documentation from a Git repository."""

import subprocess
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse


from ..config import corpus_path
from ..content_filter import is_target_language, is_valid_content
from ..source_url import is_git_repo_url

# File extensions to extract
DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".txt", ".adoc"}
# Directories to look in for docs
DOC_DIRS = {"docs", "doc", "documentation", "guide", "guides", "wiki", "examples", "tutorials"}
# Always include these files
ALWAYS_INCLUDE = {"README.md", "README.rst", "CHANGELOG.md", "CONTRIBUTING.md"}
# Minimum file size in bytes — skip tiny files (requirements.txt, empty stubs, etc.)
MIN_FILE_BYTES = 200


def acquire_repo(topic: str, repo_url: str) -> int:
    """Clone a repo (shallow) and extract documentation files."""
    if not is_git_repo_url(repo_url):
        print(f"  FAIL: {repo_url} — not a repository-root URL", file=sys.stderr)
        return 0

    parsed = urlparse(repo_url.rstrip("/"))
    domain = (parsed.hostname or "unknown").lower()
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) >= 2:
        repo_name = path_parts[1][:-4] if path_parts[1].endswith(".git") else path_parts[1]
        owner = path_parts[0]
    else:
        repo_name = path_parts[0] if path_parts else "repo"
        owner = "unknown"

    out_dir = corpus_path(topic) / "raw" / domain / owner / repo_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # Shallow clone to temp dir
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_url = repo_url.rstrip("/")
        clone_url = clone_url if clone_url.endswith(".git") else clone_url + ".git"
        try:
            subprocess.run(
                ["git", "clone", "--depth=1", "--single-branch", clone_url, tmpdir],
                capture_output=True, text=True, timeout=120, check=True
            )
        except subprocess.CalledProcessError as e:
            print(f"  FAIL: git clone {repo_url} — {e.stderr[:200]}", file=sys.stderr)
            return 0
        except subprocess.TimeoutExpired:
            print(f"  FAIL: git clone {repo_url} — timeout", file=sys.stderr)
            return 0

        # Extract documentation files
        count = 0
        tmp = Path(tmpdir)
        for f in tmp.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix not in DOC_EXTENSIONS:
                continue

            rel = f.relative_to(tmp)
            # Skip hidden dirs and common noise
            if any(p.startswith(".") for p in rel.parts):
                continue
            if any(p in {"node_modules", "vendor", "__pycache__", ".git"} for p in rel.parts):
                continue

            # Include if: in a doc directory, or a root-level doc file, or always-include
            in_doc_dir = any(p.lower() in DOC_DIRS for p in rel.parts)
            is_root = len(rel.parts) == 1
            is_always = rel.name in ALWAYS_INCLUDE

            if in_doc_dir or is_root or is_always:
                if f.stat().st_size < MIN_FILE_BYTES:
                    continue
                # Content quality gate
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if not is_target_language(content):
                    continue
                if not is_valid_content(content):
                    continue
                dest = out_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
                count += 1

    print(f"  OK: {repo_url} → {count} doc files extracted to {out_dir.relative_to(corpus_path(topic))}")
    return count


def main():
    if len(sys.argv) < 3:
        print("Usage: acquire_git.py <topic> <repo_url> [repo_url2] ...", file=sys.stderr)
        sys.exit(1)

    topic = sys.argv[1]
    repos = sys.argv[2:]

    total = 0
    for repo in repos:
        count = acquire_repo(topic, repo)
        total += count

    print(f"\nDone: {total} doc files from {len(repos)} repo(s)")


if __name__ == "__main__":
    main()

