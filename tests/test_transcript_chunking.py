"""Offline transcript preservation and opt-in compatibility checks."""

import hashlib
import json
import re

import pytest

from corpus_pipeline.chunker import chunk_file
from corpus_pipeline.content_filter import clean_chunk


HEADER = re.compile(r"^## (?:Turn (\d+) · (USER|ASSISTANT|TOOL|SYSTEM)[^\n]*|"
                    r"(USER|ASSISTANT|TOOL|SYSTEM) \(turn (\d+)\))$", re.M)


def message(turn, role, body, whole=True):
    heading = (f"## Turn {turn} · {role} · 2026-01-01T00:00:00Z" if whole else
               f"## {role} (turn {turn})")
    return f"{heading}\n\n{body}\n\n"


def normalized(text):
    return " ".join(text.split()).casefold()


def assert_preserved(source, chunks, placeholder="the output of this plugin was redacted."):
    pieces = [normalized(clean_chunk(c["content"], transcript=True)) for c in chunks]
    joined = " ".join(pieces)
    matches = list(HEADER.finditer(source))
    expected_headers = []
    for i, match in enumerate(matches):
        header = match.group(0)
        body = source[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(source)]
        if normalized(body) == normalized(placeholder):
            assert normalized(header) not in joined
            continue
        expected_headers.append(normalized(header))
        assert any(normalized(header) in piece and normalized(body)[:40] in piece
                   for piece in pieces)
        probe = normalized(body)
        for offset in range(0, len(probe), 300):
            assert probe[offset:offset + 40] in joined
        assert probe[-40:] in joined
    positions = [joined.index(header) for header in expected_headers]
    assert positions == sorted(positions)


@pytest.mark.parametrize("large", [False, True])
def test_whole_transcript_keeps_short_prompt_long_first_paragraph_and_placeholder(tmp_path, large):
    first = "alpha " * (1200 if large else 260)
    reply = "technical answer " * (2700 if large else 90)
    source = ("---\nconversation_id: abc\ntitle: Example\n---\n\n" +
              message(1, "USER", first) + message(2, "ASSISTANT", reply) +
              message(3, "USER", "shorter please. use a numbered list.") +
              message(4, "ASSISTANT", "Here is the numbered list again. " * 20) +
              message(5, "TOOL", "The output of this plugin was redacted."))
    if large:
        assert len(source) > 50_000
    path = tmp_path / "whole.md"
    path.write_text(source)
    chunks = chunk_file(path, chunk_size=6000, overlap=600, transcript=True)
    assert_preserved(source, chunks)
    short = next(c for c in chunks if "shorter please. use a numbered list." in c["content"])
    assert "## Turn 3 · USER" in short["content"]
    assert "## Turn 4 · ASSISTANT" in short["content"]
    assert "Here is the numbered list" in short["content"]
    assert chunks[0]["metadata"]["turn_start"] == {"turn": 1, "role": "USER"}
    if large:
        assert any(c["metadata"]["turn_start"] == {"turn": 2, "role": "ASSISTANT"}
                   for c in chunks if not c["content"].startswith("## "))


def test_turn_file_keeps_short_prompt_code_and_conversation_lines(tmp_path):
    source = ("---\nconversation_id: abc\n---\n\n" +
              message(6, "ASSISTANT", "Earlier long reply. " * 200, False) +
              message(7, "USER", "shorter please. use a numbered list.", False) +
              message(7, "ASSISTANT", "<<<<<<< HEAD\n" + "merge side\n" * 160 +
                      "=======\n" + "other side\n" * 160 + ">>>>>>> branch", False) +
              message(8, "USER", "Please inspect this SVG.", False) +
              message(8, "ASSISTANT", '<svg><path d="M 0 0 L 1 1"/></svg>\n' * 160 +
                      "deployed on field sensors with jitter\n"
                      "powered by a bench supply\nprivacy policy", False))
    path = tmp_path / "turn.md"
    path.write_text(source)
    chunks = chunk_file(path, chunk_size=6000, overlap=600, transcript=True)
    assert_preserved(source, chunks)
    cleaned = "\n".join(clean_chunk(c["content"], transcript=True) for c in chunks)
    for phrase in ("<<<<<<< HEAD", "<svg>", "deployed on field sensors with jitter",
                   "powered by a bench supply", "privacy policy"):
        assert phrase in cleaned
    assert clean_chunk("  <svg/>  ", transcript=True) == "<svg/>"
    assert clean_chunk("  ", transcript=True) is None


def test_short_transcript_survives_and_placeholder_only_is_empty(tmp_path):
    path = tmp_path / "short.md"
    path.write_text(message(1, "USER", "shorter please."))
    chunks = chunk_file(path, chunk_size=6000, overlap=600, transcript=True)
    assert len(chunks) == 1
    assert "## Turn 1 · USER" in chunks[0]["content"]
    assert "shorter please." in chunks[0]["content"]
    path.write_text(message(1, "TOOL", "The output of this plugin was redacted."))
    assert chunk_file(path, chunk_size=6000, overlap=600, transcript=True) == []


def test_default_output_pinned_for_docs_and_transcript_shapes(tmp_path):
    fixtures = {
        "docs": ("# Guide\n\nThis paragraph explains the public interface and the setup steps. " * 3 +
                 "\n## Install\n\nRun the setup and inspect the result. " * 4,
                 "951d0f9915d44222aad002ef73b9fc1e3de3ffe68d0a3dcd294e494b6b88da53"),
        "transcript": ("---\nconversation_id: abc\n---\n\n## Turn 1 · USER · ts\n\n"
                       "shorter please. use a numbered list.\n\n## Turn 2 · ASSISTANT · ts\n\n" +
                       "Detailed reply about the hardware setup. " * 8,
                       "09359a884fedd29e8226ce5f74dd4f65b0bb5039e17d8b3cdd83fd64e7c2723a"),
    }
    for name, (source, expected) in fixtures.items():
        path = tmp_path / name
        path.write_text(source)
        chunks = chunk_file(path, chunk_size=180, overlap=20)
        assert chunks == chunk_file(path, chunk_size=180, overlap=20, transcript=False)
        rows = [(c["content"], clean_chunk(c["content"])) for c in chunks]
        assert hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest() == expected
        assert all("turn_start" not in c["metadata"] for c in chunks)


def test_transcript_flag_without_headers_uses_default_output(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text("# Notes\n\n" + "A long documentation paragraph. " * 30)
    assert chunk_file(path, chunk_size=180, overlap=20, transcript=True) == chunk_file(
        path, chunk_size=180, overlap=20)


def test_substantive_exchanges_start_their_own_chunk_and_tiny_ones_group(tmp_path):
    long_a = "Subject A detail. " * 100
    long_b = "Subject B detail. " * 100
    text = ("---\ntitle: t\n---\n\n" + message(1, "USER", "first question about A") + message(2, "ASSISTANT", long_a)
            + message(3, "USER", "now something about B") + message(4, "ASSISTANT", long_b)
            + message(5, "USER", "thanks") + message(6, "ASSISTANT", "ok")
            + message(7, "USER", "one more") + message(8, "ASSISTANT", "sure"))
    path = tmp_path / "chat.md"
    path.write_text(text)
    chunks = [c["content"] for c in chunk_file(path, chunk_size=6000, overlap=600, transcript=True)]
    with_a = [c for c in chunks if "Turn 1 · USER" in c]
    assert with_a and "Turn 3 · USER" not in with_a[0]        # B starts a new chunk
    tiny = [c for c in chunks if "Turn 5 · USER" in c]
    assert tiny and "Turn 7 · USER" in tiny[0]                # tiny exchanges grouped
    assert_preserved(text, [{"content": c} for c in chunks])


def test_many_short_messages_do_not_cascade_into_giant_chunks(tmp_path):
    parts = [message(1, "USER", "please run the checks")]
    parts += [message(n, "ASSISTANT" if n % 2 else "TOOL", f"step {n}: " + "result text " * 40) for n in range(2, 90)]
    text = "---\ntitle: t\n---\n\n" + "".join(parts)
    path = tmp_path / "chat.md"
    path.write_text(text)
    chunks = chunk_file(path, chunk_size=6000, overlap=600, transcript=True)
    assert max(len(c["content"]) for c in chunks) <= 6000 + 600 + 200
    assert_preserved(text, chunks)


def test_multibyte_heavy_text_stays_under_byte_cap(tmp_path):
    bar = ("━" * 60 + " 3.2/3.2 MB 71.5 MB/s 0:00:00\n") * 120
    text = "---\ntitle: t\n---\n\n" + message(1, "USER", "pip log:\n" + bar) + message(2, "ASSISTANT", "looks fine")
    path = tmp_path / "chat.md"
    path.write_text(text)
    chunks = chunk_file(path, chunk_size=6000, overlap=600, transcript=True)
    assert max(len(c["content"].encode("utf-8")) for c in chunks) <= 8000
    assert_preserved(text, chunks)


def test_split_boundaries_do_not_fuse_words_in_transcript_mode(tmp_path):
    log = "".join(f"Downloading package-{i}.whl (1.2 MB)\nCollecting dependency-{i}\n" for i in range(400))
    text = "---\ntitle: t\n---\n\n" + message(1, "USER", "pip output:\n" + log) + message(2, "ASSISTANT", "ok")
    path = tmp_path / "chat.md"
    path.write_text(text)
    chunks = [c["content"] for c in chunk_file(path, chunk_size=2500, overlap=500, transcript=True)]
    joined = "\n".join(chunks)
    assert not re.search(r"\)Collecting|\.whlCollecting|[0-9]Downloading", joined)
    assert_preserved(text, [{"content": c} for c in chunks])
