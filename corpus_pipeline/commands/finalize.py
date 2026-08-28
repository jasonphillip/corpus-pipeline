#!/usr/bin/env python3
"""Finalize acquisition — update manifest status, write acquisition log."""


import re
import sys
from datetime import datetime, timezone
from pathlib import Path


from ..config import corpus_path
from ..manifest import read_manifest, write_manifest
from ..status import update_corpus_status
from ..source_url import is_git_repo_url


_DOC_EXTS = {".md", ".mdx", ".txt", ".rst", ".pdf", ".xlsm", ".xlsx", ".csv", ".html"}


def _significant_tokens(s: str) -> set[str]:
    """Tokens 4+ chars, lower-cased, non-numeric. Used to attribute on-disk
    files to manifest sources when filenames differ from URL slugs (e.g. when
    an ad-hoc downloader chose a hand-picked stem)."""
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) >= 4 and not t.isdigit()}


def _attribute_files(url: str, domain_dir: Path, token_df: dict[str, int],
                     total_files: int) -> list[Path]:
    """Best-effort per-URL file attribution within a shared domain directory.

    Scoring: for each candidate file, sum IDF weights of tokens shared with
    the URL path stem, where IDF(t) = log((N+1)/(df(t)+1)). Common tokens such as 'guide' get small weights; rare tokens such as
    'packaging' or 'errata' dominate. Return files within 75% of the top
    score, provided the top score exceeds an absolute IDF threshold."""
    import math
    if not domain_dir.exists() or total_files == 0:
        return []
    from urllib.parse import urlparse
    url_stem = Path(urlparse(url).path).stem
    url_tokens = _significant_tokens(url_stem)
    if not url_tokens:
        return []

    def idf(t: str) -> float:
        return math.log((total_files + 1) / (token_df.get(t, 0) + 1))

    # Rank by (max IDF among shared tokens, sum IDF among shared tokens).
    # Lexicographic ordering: a file sharing the URL's *rarest* token wins
    # over a file sharing many common tokens, even if the latter's sum is
    # higher. The sum acts as a tiebreaker between candidates that share an
    # equally-rare token.
    scored: list[tuple[tuple[float, float], Path]] = []
    for f in domain_dir.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in _DOC_EXTS:
            continue
        shared = url_tokens & _significant_tokens(f.stem)
        if not shared:
            continue
        idfs = [idf(t) for t in shared]
        scored.append(((max(idfs), sum(idfs)), f))
    if not scored:
        return []
    top_score = max(s for s, _ in scored)
    # Absolute floor on the rarest-token IDF: the marker token must carry
    # meaningful discriminative signal.
    if top_score[0] < 1.0:
        return []
    # Return files matching the top tuple exactly. This naturally groups
    # .md + .pdf sidecars (identical stems, identical scores) and excludes
    # partial matches that share fewer or more-common tokens.
    return [f for s, f in scored if s == top_score]


def _build_token_df(domain_dir: Path) -> tuple[dict[str, int], int]:
    """Document-frequency map: how many files in domain_dir contain each token."""
    df: dict[str, int] = {}
    n = 0
    if not domain_dir.exists():
        return df, 0
    for f in domain_dir.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in _DOC_EXTS:
            continue
        n += 1
        for t in _significant_tokens(f.stem):
            df[t] = df.get(t, 0) + 1
    return df, n


def finalize_acquisition(topic: str) -> None:
    """Reconcile acquired files with the manifest and write status metadata."""
    corpus_dir = corpus_path(topic)
    raw_dir = corpus_dir / "raw"
    meta_dir = corpus_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)

    # Count acquired files
    file_count = 0
    file_list = []
    if raw_dir.exists():
        for f in raw_dir.rglob("*"):
            if f.is_file() and f.suffix in {".md", ".mdx", ".txt", ".rst"}:
                file_count += 1
                file_list.append(str(f.relative_to(raw_dir)))

    # Update manifest source statuses based on what's on disk
    manifest = read_manifest(topic)
    acquired_count = 0
    from urllib.parse import urlparse
    # Cache per-domain token document-frequency tables for IDF-style matching.
    domain_df_cache: dict[Path, tuple[dict[str, int], int]] = {}
    for src in manifest.get("sources", []):
        url = src.get("url")
        if not url:
            local = src.get("local")
            local_path = corpus_dir / local if isinstance(local, str) and local else None
            if local_path and local_path.is_file():
                local_file_count = 1
            elif local_path and local_path.is_dir():
                local_file_count = sum(1 for f in local_path.rglob("*") if f.is_file())
            else:
                local_file_count = 0

            if local_file_count:
                src["status"] = "acquired"
                src["files_acquired"] = local_file_count
                acquired_count += 1
            else:
                if src.get("status") != "acquired":
                    src["status"] = "failed"
                location = local if isinstance(local, str) and local else "<missing>"
                print(
                    f"  WARN: source has no URL and no existing local content: {location}",
                    file=sys.stderr,
                )
            continue

        parsed = urlparse(url.rstrip("/"))
        domain = parsed.netloc.replace(":", "_")
        url_path = parsed.path.strip("/")

        # Git repos clone into a unique per-repo dir, so a directory check is
        # already per-source and accurate.
        if is_git_repo_url(url):
            repo_domain = (parsed.hostname or "").lower()
            path_parts = url_path.split("/")
            if len(path_parts) >= 2:
                repo_name = path_parts[1][:-4] if path_parts[1].endswith(".git") else path_parts[1]
                repo_dir = raw_dir / repo_domain / path_parts[0] / repo_name
            else:
                repo_dir = raw_dir / repo_domain / url_path
            if repo_dir.exists() and any(repo_dir.rglob("*")):
                src["status"] = "acquired"
                src["files_acquired"] = sum(1 for f in repo_dir.rglob("*") if f.is_file())
                acquired_count += 1
            elif src.get("status") != "acquired":
                src["status"] = "failed"
            continue

        # Single-page sources from the same domain share `raw/<domain>/`, so
        # we can't infer per-source file counts from the directory alone.
        # Attribute by token overlap between the URL path stem and on-disk
        # filename stems (handles both acquire_jina slug naming and ad-hoc
        # downloaders that chose a different filename).
        domain_dir = raw_dir / domain
        if domain_dir not in domain_df_cache:
            domain_df_cache[domain_dir] = _build_token_df(domain_dir)
        token_df, total_files = domain_df_cache[domain_dir]
        attributed = _attribute_files(url, domain_dir, token_df, total_files)
        if attributed:
            src["status"] = "acquired"
            src["files_acquired"] = len(attributed)
            acquired_count += 1
        elif domain_dir.exists() and any(domain_dir.rglob("*")):
            # Domain has content but we can't attribute this URL specifically;
            # don't fabricate a count.
            src["status"] = "acquired"
            src.pop("files_acquired", None)
            acquired_count += 1
        elif src.get("status") != "acquired":
            src["status"] = "failed"

    write_manifest(topic, manifest)

    # Write acquisition log
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_lines = [
        f"# Acquisition Log — {topic}",
        f"Date: {now}",
        f"Total files: {file_count}",
        f"Sources acquired: {acquired_count}/{len(manifest.get('sources', []))}",
        "",
        "## Files:",
    ]
    for f in sorted(file_list):
        log_lines.append(f"- {f}")

    (meta_dir / "acquisition.log").write_text("\n".join(log_lines) + "\n")

    # Update corpus index
    update_corpus_status(topic, f"acquire-corpus {topic}", {
        "sources": acquired_count,
        "raw_files": file_count,
    })

    print(f"Acquisition finalized for '{topic}':")
    print(f"  Files: {file_count}")
    print(f"  Sources: {acquired_count}/{len(manifest.get('sources', []))}")
    print(f"  Log: {meta_dir / 'acquisition.log'}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: finalize_acquisition.py <topic>", file=sys.stderr)
        raise SystemExit(1)
    finalize_acquisition(sys.argv[1])


if __name__ == "__main__":
    main()
