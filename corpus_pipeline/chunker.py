"""Markdown-aware chunking with section-based splitting for large files."""

import re
from pathlib import Path

from .config import CHUNK_SIZE, CHUNK_OVERLAP, get_embedding_config

# Files under this size (bytes) are skipped as noise
MIN_CHUNK_CHARS = 100
# Files over this size get section-aware splitting as first pass
LARGE_FILE_THRESHOLD = 50_000  # ~50KB
TRANSCRIPT_PLACEHOLDERS = frozenset({"", "the output of this plugin was redacted."})
# A USER-led exchange joins the previous chunk only while that chunk is this small, so substantive
# exchanges (and the subject changes between them) start their own chunk; tiny back-and-forths group.
TRANSCRIPT_PACK_CHARS = 1500
_ROLE_HEADER = re.compile(
    r"^## (?:(?:Turn (\d+) · (USER|ASSISTANT|TOOL|SYSTEM)[^\n]*)|"
    r"(?:(USER|ASSISTANT|TOOL|SYSTEM) \(turn (\d+)\)))$", re.MULTILINE)


def chunk_file(file_path: str | Path, chunk_size: int | None = None,
               overlap: int | None = None, transcript: bool = False) -> list[dict]:
    """Chunk a markdown file, returning list of {content, index, metadata}.

    For large files (>50KB), splits on markdown headers first to preserve
    document structure, then applies recursive chunking within each section.
    """
    text = Path(file_path).read_text(encoding="utf-8", errors="replace")
    if not text.strip() or (len(text.strip()) < MIN_CHUNK_CHARS and
                           not (transcript and _ROLE_HEADER.search(text))):
        return []

    if chunk_size is None or overlap is None:
        cfg = get_embedding_config()
        if chunk_size is None:
            chunk_size = cfg["chunk_size"]
        if overlap is None:
            overlap = cfg.get("chunk_overlap", CHUNK_OVERLAP)

    if transcript and _ROLE_HEADER.search(text):
        return _chunk_transcript(text, file_path, chunk_size, overlap)

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


def _chunk_transcript(text: str, file_path: str | Path,
                      chunk_size: int, overlap: int) -> list[dict]:
    """Keep message headers and short turns when packing transcript exchanges."""
    matches = list(_ROLE_HEADER.finditer(text))
    preamble = text[:matches[0].start()]
    messages = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end():end]
        if " ".join(body.split()).casefold() in TRANSCRIPT_PLACEHOLDERS:
            continue
        role = match.group(2) or match.group(3)
        messages.append((role, text[match.start():end]))
    if not messages:
        return []
    header_lines = {match.group(0) for match in matches}

    blocks = []
    current = preamble
    for role, message in messages:
        if role == "USER" and current != preamble:
            blocks.append(current)
            current = ""
        current += message
    if current.strip():
        blocks.append(current)

    chunks = []
    packed = ""
    for block in blocks:
        if packed and (len(packed) + len(block) > chunk_size or len(packed) >= TRANSCRIPT_PACK_CHARS):
            chunks.append(packed)
            packed = ""
        if len(block) > chunk_size:
            pieces = _chunk_recursive(block, chunk_size, overlap, keep_sep=True)
            # Recursive splitting can leave a header or a short prompt alone.
            # Join these with the next piece, or the prior piece at the end.
            pending = ""
            for piece in pieces:
                for header in header_lines:
                    offset = 0
                    while (at := piece.find(header, offset)) >= 0:
                        if at and piece[at - 1] != "\n":
                            piece = piece[:at] + "\n\n" + piece[at:]
                            at += 2
                        offset = at + len(header)
                if pending:
                    # _chunk_recursive omits the separator at a boundary.
                    # Restore a line break after a header before joining its body.
                    trailing = list(_ROLE_HEADER.finditer(pending))
                    separator = ("\n\n" if trailing and
                                 not pending[trailing[-1].end():].strip() else "")
                    piece = pending + separator + piece
                    pending = ""
                last_header = list(_ROLE_HEADER.finditer(piece))
                header_only = bool(last_header and not piece[last_header[-1].end():].strip())
                if header_only:
                    # Carry only the trailing header line to the next piece; merging
                    # whole pieces cascaded into 49,000-character chunks (2026-10-14).
                    cut = last_header[-1].start()
                    if len(piece[:cut].strip()) >= MIN_CHUNK_CHARS:
                        chunks.append(piece[:cut].rstrip("\n") + "\n")
                        pending = piece[cut:]
                    else:
                        pending = piece
                elif len(piece.strip()) < MIN_CHUNK_CHARS:
                    pending = piece
                else:
                    chunks.append(piece)
            if pending:
                if chunks:
                    chunks[-1] += pending
                else:
                    chunks.append(pending)
        else:
            packed += block
    if packed:
        chunks.append(packed)
    # Tokens never exceed UTF-8 bytes: keep every chunk under the embedder's 8,192-token slot.
    chunks = [piece for chunk in chunks for piece in _cap_bytes(chunk, overlap)]

    result = []
    governing = None
    first_match = _ROLE_HEADER.match(messages[0][1])
    first_role = first_match.group(2) or first_match.group(3)
    first_turn = int(first_match.group(1) or first_match.group(4))
    for i, content in enumerate(chunks):
        headers = list(_ROLE_HEADER.finditer(content))
        if content.startswith("## ") and headers and headers[0].start() == 0:
            active = headers[0]
            governing = {"turn": int(active.group(1) or active.group(4)),
                         "role": active.group(2) or active.group(3)}
        elif governing is None:
            governing = {"turn": first_turn, "role": first_role}
        turn_start = dict(governing)
        if headers:
            active = headers[-1]
            governing = {"turn": int(active.group(1) or active.group(4)),
                         "role": active.group(2) or active.group(3)}
        result.append({"content": content, "index": i,
                       "metadata": {"source": str(file_path), "chunk_size": len(content),
                                    "turn_start": turn_start}})
    return result


TRANSCRIPT_MAX_BYTES = 8000


def _byte_slices(chunk: str) -> list[str]:
    """Hard split into consecutive pieces of at most TRANSCRIPT_MAX_BYTES UTF-8 bytes."""
    out, current, used = [], [], 0
    for ch in chunk:
        n = len(ch.encode("utf-8"))
        if used + n > TRANSCRIPT_MAX_BYTES:
            out.append("".join(current)); current, used = [], 0
        current.append(ch); used += n
    if current:
        out.append("".join(current))
    return out


def _cap_bytes(chunk: str, overlap: int, depth: int = 0) -> list[str]:
    """Split a transcript chunk until each piece is at most TRANSCRIPT_MAX_BYTES of UTF-8."""
    size = len(chunk.encode("utf-8"))
    if size <= TRANSCRIPT_MAX_BYTES:
        return [chunk]
    if depth >= 4:  # guaranteed progress
        return _byte_slices(chunk)
    target = max(500, int(len(chunk) * (TRANSCRIPT_MAX_BYTES * 0.9) / size))
    pieces = _chunk_recursive(chunk, target, min(overlap, target // 5), keep_sep=True)
    if len(pieces) <= 1:  # no separator helped: hard split by characters
        pieces = [chunk[i:i + target] for i in range(0, len(chunk), target)]
    # A header that ends a piece moves to the start of the next one (role stays with its text).
    carried = []
    for i, piece in enumerate(pieces):
        headers = list(_ROLE_HEADER.finditer(piece))
        if i + 1 < len(pieces) and headers and not piece[headers[-1].end():].strip():
            cut = headers[-1].start()
            pieces[i + 1] = piece[cut:].rstrip() + "\n\n" + pieces[i + 1]
            piece = piece[:cut]
        if piece.strip():
            carried.append(piece)
    return [p for piece in carried for p in _cap_bytes(piece, overlap, depth + 1)]


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


def _chunk_recursive(text: str, chunk_size: int, overlap: int, keep_sep: bool = False) -> list[str]:
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
    return _split_recursive(text, separators, chunk_size, overlap, keep_sep)


def _split_recursive(text: str, separators: list[str],
                     chunk_size: int, overlap: int, keep_sep: bool = False) -> list[str]:
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
                        sub_chunks = _split_recursive(part, remaining_seps, chunk_size, overlap, keep_sep)
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
                # keep_sep (transcript mode): restore the separator the split removed, so
                # words do not fuse and a "## " header marker survives at the boundary.
                overlapped.append(prev_tail + (sep if keep_sep and prev_tail else "") + chunks[i])
            chunks = overlapped

        return [c for c in chunks if c.strip()]

    # No separator worked — hard split
    chunks = []
    for i in range(0, len(text), chunk_size - overlap):
        chunks.append(text[i:i + chunk_size])
    return [c for c in chunks if c.strip()]
