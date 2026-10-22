"""Markdown-aware chunking with section-based splitting for large files."""

import re
from html import unescape
from pathlib import Path
from datetime import datetime

from .config import CHUNK_SIZE, CHUNK_OVERLAP, get_embedding_config
from .content_filter import information_density, strip_boilerplate

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
    r"(?:(USER|ASSISTANT|TOOL|SYSTEM) \(turn (\d+)\))|"
    r"(Prompt|Response))$|^\*\*(spk_\d+):\*\*", re.MULTILINE)


_EMAIL_HEADER = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) — (.+)$", re.M)
_SESSION_HEADER = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) session$", re.M)
_QUOTE_START = re.compile(r"^(?:On .{5,120} wrote:|-----Original Message-----)")
_DOCUMENT_HEADING = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_EMAIL_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_EMAIL_TAG = re.compile(r"<[^>]+>")
_EMAIL_BLOCK_TAG = re.compile(r"</?(?:br|div|p|tr|td|li|h[1-6])\b[^>]*>", re.IGNORECASE)
_CSS_DECLARATION = re.compile(r"^[-\w]+\s*:\s*[^;]+;\s*$")


def _clean_profile_body(body: str, *, email: bool = False) -> str:
    """Clean each structured body before it consumes a chunk's size budget."""
    if email:
        body = strip_boilerplate(body)
        body = _EMAIL_COMMENT.sub("", body)
        body = _EMAIL_BLOCK_TAG.sub("\n", body)
        body = unescape(_EMAIL_TAG.sub("", body))
        lines = []
        for line in body.splitlines():
            line = line.strip()
            if not line or "{" in line or "}" in line or _CSS_DECLARATION.match(line):
                continue
            if sum(ch.isalnum() for ch in line) < 0.35 * len(line):
                continue
            lines.append(line)
        body = "\n".join(lines)
    cleaned = strip_boilerplate(body)
    if not cleaned or information_density(cleaned) < 0.3:
        return ""
    if email and sum(ch.isalnum() for ch in cleaned) < 40:
        return ""
    return cleaned


def _with_prefix(body: str, prefix: str, metadata: dict, path: Path) -> dict:
    lead = prefix + "\n\n" if prefix else ""
    content = lead + body
    return {"content": content, "metadata": {"source": str(path.resolve()),
            "chunk_size": len(content), "prefix_len": len(lead), **metadata}}


def _pieces(body: str, header: str, limit: int, overlap: int,
            *, separator: str) -> list[str]:
    """Split an oversized body while repeating its governing header."""
    capacity = max(1, limit - len(header))
    if len(header + body) <= limit:
        return [header + body]
    units = body.splitlines(keepends=True) if separator == "\n" else re.split(r"(?<=\n\n)", body)
    if len(units) == 1:
        units = body.splitlines(keepends=True)
    out, current = [], ""
    def tail(value: str) -> str:
        if not overlap:
            return ""
        boundary = "\n" if separator == "\n" else "\n\n"
        candidate = value[-min(overlap, max(0, capacity // 3)):]
        at = candidate.find(boundary)
        return candidate[at + len(boundary):] if at >= 0 else ""

    for unit in units:
        if current and len(current + unit) > capacity:
            out.append(header + current)
            current = tail(current)
        while len(current + unit) > capacity and unit:
            take = max(1, capacity - len(current))
            current += unit[:take]
            unit = unit[take:]
            out.append(header + current)
            current = tail(current)
        current += unit
    if current.strip():
        out.append(header + current)
    return out


def _document_sections(text: str) -> list[tuple[str, list[str]]]:
    """Split at H1-H3 outside fenced code, retaining the active heading path."""
    sections = []
    headings = ["", "", ""]
    active_path = []
    start = offset = 0
    fence = ""
    for line in text.splitlines(keepends=True):
        marker = _FENCE.match(line)
        if marker:
            run = marker.group(1)
            if not fence:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = ""
        elif not fence and (match := _DOCUMENT_HEADING.match(line.rstrip("\r\n"))):
            if offset > start:
                sections.append((text[start:offset], active_path))
            level = len(match.group(1))
            headings[level - 1] = match.group(2).strip()
            headings[level:] = [""] * (3 - level)
            active_path = [heading for heading in headings if heading]
            start = offset
        offset += len(line)
    if start < len(text):
        sections.append((text[start:], active_path))
    return sections


def _fence_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    opening = None
    fence = ""
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = _FENCE.match(line)
        if marker:
            run = marker.group(1)
            if not fence:
                opening, fence = offset, run
            elif run[0] == fence[0] and len(run) >= len(fence):
                spans.append((opening, offset + len(line)))
                opening, fence = None, ""
        offset += len(line)
    if opening is not None:
        spans.append((opening, len(text)))
    return spans


def _document_pieces(text: str, limit: int, overlap: int) -> list[str]:
    """Prefer paragraph and sentence ends, moving nearby code splits to fence edges."""
    if len(text) <= limit:
        return [text] if text.strip() else []
    spans = _fence_spans(text)
    out = []
    start = 0
    while start < len(text):
        upper = min(len(text), start + limit)
        if upper == len(text):
            out.append(text[start:])
            break
        floor = start + max(1, limit // 2)
        blank = [m.end() for m in re.finditer(r"\n[ \t]*\n", text[start:upper])]
        sentences = [m.end() for m in re.finditer(r"[.!?][ \t]+", text[start:upper])]
        lines = [m.end() for m in re.finditer(r"\n", text[start:upper])]
        candidates = [start + positions[-1] for positions in (blank, sentences, lines)
                      if positions and start + positions[-1] >= floor]
        end = candidates[0] if candidates else upper
        for opening, closing in spans:
            if opening < end < closing:
                edges = [edge for edge in (opening, closing)
                         if start < edge <= upper and abs(edge - end) <= 400]
                if edges:
                    end = min(edges, key=lambda edge: abs(edge - end))
                break
        out.append(text[start:end])
        next_start = max(start + 1, end - overlap)
        for opening, closing in spans:
            if opening < next_start < closing:
                edges = [edge for edge in (opening, closing)
                         if start < edge <= end and abs(edge - next_start) <= 400]
                if edges:
                    next_start = min(edges, key=lambda edge: abs(edge - next_start))
                break
        start = next_start
    return [piece for piece in out if piece.strip()]


def chunk_profiled_file(file_path: str | Path, mode: str, frontmatter: dict,
                        chunk_chars: int, overlap_chars: int,
                        prefix: bool = True, channel: str | None = None) -> list[dict]:
    """Chunk personal archives by their source structure; preserve per-chunk dates."""
    path = Path(file_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return []
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            text = text[end + 5:]
    rows = []
    if mode == "email_thread":
        matches = list(_EMAIL_HEADER.finditer(text))
        messages = []
        for i, match in enumerate(matches):
            raw = text[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            lines = []
            for line in raw.splitlines():
                if _QUOTE_START.match(line):
                    break
                if not line.startswith(">"):
                    lines.append(line)
            body = _clean_profile_body("\n".join(lines), email=True)
            messages.append((match, body, i + 1))
        for match, body, index in messages:
            sender = match.group(2)
            address = re.search(r"<([^>]+)>", sender)
            stamp = match.group(1)
            subject = str(frontmatter.get("subject") or "")
            participants = frontmatter.get("participants") or []
            info = {"ts": datetime.fromisoformat(stamp).isoformat(), "sender": sender,
                    "sender_addr": address.group(1) if address else "",
                    "message_index": index, "message_count": len(messages),
                    "conversation_id": frontmatter.get("thread_id"), "subject": subject,
                    "channel": "email"}
            lead = (f"Email | Subject: {subject} | From: {sender} | Date: {stamp} | "
                    f"Message {index} of {len(messages)} | Participants: {', '.join(map(str, participants))}") if prefix else ""
            header = match.group(0) + "\n"
            for piece in _pieces(body, header, max(1, chunk_chars - len(lead) - (2 if lead else 0)),
                                 overlap_chars, separator="\n\n"):
                rows.append(_with_prefix(piece.rstrip(), lead, info, path))
    elif mode == "chat_sessions":
        matches = list(_SESSION_HEADER.finditer(text))
        sessions = []
        for i, match in enumerate(matches):
            raw = text[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            body = _clean_profile_body(raw)
            if body:
                sessions.append((match, match.group(0) + "\n" + body))
        channel = channel or str(frontmatter.get("channel") or "chat")
        contact = str(frontmatter.get("contact") or "")
        key = str(frontmatter.get("contact_key") or "")
        packs = []
        current = []
        for session in sessions:
            candidate = "\n\n".join(x[1] for x in [*current, session])
            first_date = current[0][0].group(1)[:10] if current else session[0].group(1)[:10]
            lead_len = len(f"{channel.upper()} with {contact} | {first_date}") + 2 if prefix else 0
            if current and len(candidate) > chunk_chars - lead_len:
                packs.append(current)
                current = []
            current.append(session)
        if current:
            packs.append(current)
        for pack in packs:
            first, last = pack[0][0].group(1), pack[-1][0].group(1)
            info = {"ts": datetime.fromisoformat(first).isoformat(),
                    "ts_end": datetime.fromisoformat(last).isoformat(), "contact": contact,
                    "contact_key": frontmatter.get("contact_key"), "channel": channel,
                    "conversation_id": f"{channel}:{key}",
                    "participants": [contact], "session_count": len(pack)}
            lead = f"{channel.upper()} with {contact} | {first[:10]}" if prefix else ""
            body = "\n\n".join(x[1] for x in pack)
            header = pack[0][0].group(0) + "\n" if len(pack) == 1 else ""
            if header and body.startswith(header):
                body = body[len(header):]
            for piece in _pieces(body, header, max(1, chunk_chars - len(lead) - (2 if lead else 0)),
                                 overlap_chars, separator="\n"):
                rows.append(_with_prefix(piece, lead, info, path))
    elif mode == "transcript":
        is_recording = bool(frontmatter.get("recording_start"))
        stamp = str(frontmatter.get("recording_start") or frontmatter.get("date") or "")
        name = str(frontmatter.get("name") if is_recording else frontmatter.get("title") or "")
        lead = (f"Recording: {name} | {stamp[:10]}" if is_recording else
                f"Chat: {name} | {stamp[:10]}") if prefix else ""
        info = {"ts": stamp, "conversation_id": frontmatter.get("id") or path.stem,
                "channel": "recording" if is_recording else "chat"}
        matches = list(_ROLE_HEADER.finditer(text))
        blocks = [text[m.start():matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip()
                  for i, m in enumerate(matches)]
        if any(m.group(5) == "Prompt" for m in matches):
            pair_index = 0
            for i, match in enumerate(matches):
                if match.group(5) != "Prompt":
                    continue
                pair_index += 1
                prompt = _clean_profile_body(blocks[i][len(match.group(0)):])
                response = (blocks[i + 1] if i + 1 < len(matches) and
                            matches[i + 1].group(5) == "Response" else "")
                if response:
                    response_body = _clean_profile_body(response[len(matches[i + 1].group(0)):])
                    response = "## Response\n" + response_body if response_body else ""
                if not prompt and not response:
                    continue
                pair = f"## Prompt\n{prompt}" + (f"\n\n{response}" if response else "")
                pair_info = {**info, "pair_index": pair_index}
                available = max(1, chunk_chars - len(lead) - (2 if lead else 0))
                if len(pair) <= available:
                    rows.append(_with_prefix(pair, lead, pair_info, path))
                else:
                    subject = " ".join(prompt.split())[:120]
                    header = f"Prompt: {subject}\n"
                    for piece in _pieces(pair, header, available, overlap_chars,
                                         separator="\n"):
                        rows.append(_with_prefix(piece, lead, pair_info, path))
            for i, row in enumerate(rows):
                row["index"] = i
            return rows
        if not blocks:
            blocks = [text.strip()]
        packed = []
        for block in blocks:
            role_match = _ROLE_HEADER.match(block)
            header = role_match.group(0) if role_match else ""
            body = _clean_profile_body(block[len(header):] if header else block)
            if not body:
                continue
            block = header + "\n" + body if header else body
            if packed and len(packed[-1] + "\n\n" + block) <= chunk_chars - len(lead) - 2:
                packed[-1] += "\n\n" + block
            else:
                packed.append(block)
        for block in packed:
            role_match = _ROLE_HEADER.match(block)
            header = role_match.group(0) if role_match else ""
            body = block[len(header):] if header else block
            for piece in _pieces(body, header, max(1, chunk_chars - len(lead) - (2 if lead else 0)),
                                 overlap_chars, separator="\n"):
                rows.append(_with_prefix(piece, lead, info, path))
    elif mode == "document":
        title = str(frontmatter.get("title") or frontmatter.get("filename") or path.name)
        original = Path(str(frontmatter.get("original_path") or path))
        tail = "/".join(original.parts[-2:])
        stamp = str(frontmatter.get("created_at") or frontmatter.get("date") or "")
        base_lead = f"Document: {title} | Path: {tail} | Date: {stamp[:10]}" if prefix else ""
        for section, heading_path in _document_sections(text):
            section = _clean_profile_body(section)
            if not section:
                continue
            lead = base_lead
            if prefix and heading_path:
                lead += f" | Section: {' > '.join(heading_path)}"
            info = {"ts": stamp, "heading_path": heading_path,
                    "conversation_id": frontmatter.get("conversation_id") or frontmatter.get("id")}
            limit = max(1, chunk_chars - len(lead) - (2 if lead else 0))
            for piece in _document_pieces(section, limit, overlap_chars):
                rows.append(_with_prefix(piece, lead, info, path))
    else:
        raise ValueError(f"unknown chunking mode: {mode}")
    for i, row in enumerate(rows):
        row["index"] = i
    return rows


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

    if transcript and any(m.group(5) or m.group(6) for m in _ROLE_HEADER.finditer(text)):
        return chunk_profiled_file(file_path, "transcript", {}, chunk_size, overlap, prefix=False)
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
