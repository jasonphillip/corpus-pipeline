"""URL classification helpers for corpus acquisition."""

import sys
from urllib.parse import urlparse


GIT_REPO_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org"})


def is_git_repo_url(url: str) -> bool:
    """Return True only for a repository-root URL on a supported Git host."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in GIT_REPO_HOSTS:
        return False
    if parsed.params or parsed.query or parsed.fragment:
        return False

    path = parsed.path.rstrip("/")
    parts = path.split("/")
    if len(parts) != 3 or parts[0] or not parts[1] or not parts[2]:
        return False

    repo_name = parts[2][:-4] if parts[2].endswith(".git") else parts[2]
    return bool(repo_name)


def jina_reader_url(url: str, prefix: str) -> str:
    """Add the Jina Reader prefix unless the URL is already wrapped."""
    if urlparse(url).hostname == urlparse(prefix).hostname:
        return url
    return prefix + url


def main() -> None:
    """Exit successfully when the argument is a supported repository URL."""
    if len(sys.argv) != 2:
        print("Usage: source_url.py <url>", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if is_git_repo_url(sys.argv[1]) else 1)


if __name__ == "__main__":
    main()

