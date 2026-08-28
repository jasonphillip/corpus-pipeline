from corpus_pipeline.chunker import _chunk_recursive, chunk_text
from corpus_pipeline.content_filter import clean_chunk, is_valid_content


def test_recursive_chunking_respects_limit_and_overlap():
    text = "\n\n".join(f"Paragraph {index} " + "word " * 20 for index in range(8))
    chunks = _chunk_recursive(text, chunk_size=220, overlap=30)
    assert len(chunks) > 1
    assert all(len(chunk) <= 250 for chunk in chunks)


def test_chunk_text_skips_stubs():
    assert chunk_text("short") == []


def test_content_filter_removes_banner_but_keeps_prose():
    text = "We use cookies to improve this site.\n" + "Useful technical prose. " * 20
    cleaned = clean_chunk(text)
    assert cleaned is not None
    assert "cookies" not in cleaned.lower()
    assert is_valid_content(cleaned)

