"""Markdown-aware chunking with section-based splitting for large files."""

import re
from pathlib import Path

from .config import CHUNK_SIZE, CHUNK_OVERLAP, get_embedding_config

# Files under this size (bytes) are skipped as noise
MIN_CHUNK_CHARS = 100
# Files over this size get section-aware splitting as first pass
LARGE_FILE_THRESHOLD = 50_000  # ~50KB


def chunk_file(file_path: str | Path, chunk_size: int | None = None,
               overlap: int | None = None) -> list[dict]:
    """Chunk a markdown file, returning list of {content, index, metadata}.

    For large files (>50KB), splits on markdown headers first to preserve
    document structure, then applies recursive chunking within each section.
    """
    text = Path(file_path).read_text(encoding="utf-8", errors="replace")
    if not text.strip() or len(text.strip()) < MIN_CHUNK_CHARS:
        return []

    if chunk_size is None or overlap is None:
        cfg = get_embedding_config()
        if chunk_size is None:
            chunk_size = cfg["chunk_size"]
        if overlap is None:
            overlap = cfg.get("chunk_overlap", CHUNK_OVERLAP)

    if len(text) > LARGE_FILE_THRESHOLD:
        chunks = _chunk_by_sections(text, chunk_size, overlap)
    else:
        chunks = _chunk_recursive(text, chunk_size, overlap)

    return [
        {
            "content": c,
            "index": i,
            "metadata": {
                "source": str(file_path),
                "chunk_size": len(c),
            },
        }
        for i, c in enumerate(chunks)
        if len(c.strip()) >= MIN_CHUNK_CHARS
    ]


def chunk_text(text: str, chunk_size: int | None = None,
               overlap: int | None = None) -> list[str]:
    """Chunk raw text, returning list of strings."""
    if not text.strip() or len(text.strip()) < MIN_CHUNK_CHARS:
        return []

    if chunk_size is None or overlap is None:
        cfg = get_embedding_config()
        if chunk_size is None:
            chunk_size = cfg["chunk_size"]
        if overlap is None:
            overlap = cfg.get("chunk_overlap", CHUNK_OVERLAP)

    if len(text) > LARGE_FILE_THRESHOLD:
        chunks = _chunk_by_sections(text, chunk_size, overlap)
    else:
        chunks = _chunk_recursive(text, chunk_size, overlap)

    return [c for c in chunks if len(c.strip()) >= MIN_CHUNK_CHARS]


def _chunk_by_sections(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split large files on markdown headers first, then chunk each section.

    Splits on # and ## headers, preserving the header line as part of each
    section. Sections that fit within chunk_size are kept intact. Larger
    sections get recursive chunking applied.
    """
    # Split on lines that start with # or ## (but not ### which is too granular)
    section_pattern = re.compile(r'^(#{1,2}\s+.+)$', re.MULTILINE)
    parts = section_pattern.split(text)

    # Reassemble into sections: each header + its body
    sections = []
    current = ""
    for part in parts:
        if section_pattern.match(part):
            if current.strip():
                sections.append(current)
            current = part
        else:
            current += part
    if current.strip():
        sections.append(current)

    # Chunk each section independently
    chunks = []
    for section in sections:
        if len(section) <= chunk_size:
            if section.strip():
                chunks.append(section)
        else:
            sub_chunks = _chunk_recursive(section, chunk_size, overlap)
            chunks.extend(sub_chunks)

    return chunks


def _chunk_recursive(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Recursive character-based chunking that respects markdown structure.

    Splits on these separators in order of preference:
    1. Double newline (paragraph boundary)
    2. Markdown headers (# ## ### etc.)
    3. Single newline
    4. Sentence boundary (. ! ?)
    5. Word boundary (space)
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    separators = ["\n\n", "\n# ", "\n## ", "\n### ", "\n", ". ", "! ", "? ", " "]
    return _split_recursive(text, separators, chunk_size, overlap)


def _split_recursive(text: str, separators: list[str],
                     chunk_size: int, overlap: int) -> list[str]:
    """Split text recursively using separator hierarchy."""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    # Try each separator
    for sep in separators:
        parts = text.split(sep)
        if len(parts) <= 1:
            continue

        chunks = []
        current = ""
        for part in parts:
            candidate = current + sep + part if current else part
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                # If single part exceeds chunk_size, try next separator
                if len(part) > chunk_size:
                    remaining_seps = separators[separators.index(sep) + 1:]
                    if remaining_seps:
                        sub_chunks = _split_recursive(part, remaining_seps, chunk_size, overlap)
                        chunks.extend(sub_chunks)
                        current = ""
                    else:
                        # Hard split at chunk_size
                        for i in range(0, len(part), chunk_size - overlap):
                            chunks.append(part[i:i + chunk_size])
                        current = ""
                else:
                    current = part

        if current:
            chunks.append(current)

        # Add overlap between chunks
        if overlap > 0 and len(chunks) > 1:
            overlapped = [chunks[0]]
            for i in range(1, len(chunks)):
                prev_tail = chunks[i - 1][-overlap:] if len(chunks[i - 1]) > overlap else ""
                overlapped.append(prev_tail + chunks[i])
            chunks = overlapped

        return [c for c in chunks if c.strip()]

    # No separator worked — hard split
    chunks = []
    for i in range(0, len(text), chunk_size - overlap):
        chunks.append(text[i:i + chunk_size])
    return [c for c in chunks if c.strip()]

