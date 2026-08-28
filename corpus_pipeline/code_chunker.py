"""Code-aware chunking via tree-sitter.

Sibling to lib.chunker (markdown). Splits source files at function/class
boundaries instead of character windows so retrieval lands on coherent units.
Falls back to lib.chunker._chunk_recursive on any parse failure (e.g. files
the bundled grammars don't ship for, or syntax-broken files).

Returns the same shape as chunker.chunk_file:
    [{"content": str, "index": int, "metadata": dict}]
so the existing process-corpus insert path consumes it unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .chunker import _chunk_recursive, MIN_CHUNK_CHARS
from .config import CHUNK_OVERLAP_ENHANCED, CHUNK_SIZE_ENHANCED, get_embedding_config

# Map file extension -> tree-sitter language name as exposed by
# tree-sitter-language-pack / tree_sitter_languages.
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".v": "verilog",
    ".sv": "verilog",
    ".svh": "verilog",
}

# Tree-sitter node kinds that delimit a "code unit" worth retrieving as one
# chunk. Per-language because grammars name things differently.
_UNIT_NODES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier",
            "namespace_definition", "template_declaration"},
    "c": {"function_definition", "struct_specifier", "union_specifier"},
    "verilog": {"module_declaration", "function_declaration", "task_declaration",
                "always_construct", "initial_construct"},
}

# Cache parser objects keyed by language name; cheap to construct but no point
# rebuilding per-file.
_parser_cache: dict[str, Any] = {}


def language_for(path: Path | str) -> str | None:
    """Return tree-sitter language name for a path, or None if unsupported."""
    return _EXT_TO_LANG.get(Path(path).suffix.lower())


def _get_parser(lang: str):
    """Load a tree-sitter parser, caching it. Returns None if unavailable."""
    if lang in _parser_cache:
        return _parser_cache[lang]

    parser = None
    # Prefer tree-sitter-language-pack (newer, broader coverage).
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore
        parser = get_parser(lang)
    except Exception:
        pass

    if parser is None:
        # Older bundle; works for python/cpp/c. Verilog support is patchy.
        try:
            from tree_sitter_languages import get_parser  # type: ignore
            parser = get_parser(lang)
        except Exception:
            pass

    _parser_cache[lang] = parser
    return parser


def _node_text(source: bytes, node) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _walk_units(node, kinds: set[str]) -> list[Any]:
    """Pre-order traversal collecting the outermost nodes whose type is in kinds.

    Outermost: once a unit is captured we don't descend into it (avoids emitting
    both an outer class and its methods as separate top-level chunks). Methods
    inside a class still get included via the class chunk's body.
    """
    found: list[Any] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type in kinds:
            found.append(cur)
            continue
        # Push children right-to-left so traversal stays left-to-right.
        for i in range(cur.child_count - 1, -1, -1):
            stack.append(cur.children[i])
    return found


def _split_oversize(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Fall back to recursive char chunking for a single oversize unit."""
    if len(text) <= chunk_size:
        return [text]
    return _chunk_recursive(text, chunk_size, overlap)


def chunk_code_file(file_path: str | Path,
                    chunk_size: int | None = None,
                    overlap: int | None = None) -> list[dict]:
    """Chunk a source file by syntactic units. Falls back to recursive on failure.

    chunk_size / overlap default to the active embedding config (Qwen3 = 6000/600).
    Oversize functions are split with the recursive markdown chunker as a fallback.
    """
    path = Path(file_path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip() or len(text) < MIN_CHUNK_CHARS:
        return []

    if chunk_size is None or overlap is None:
        cfg = get_embedding_config()
        if chunk_size is None:
            chunk_size = int(cfg.get("chunk_size", CHUNK_SIZE_ENHANCED))
        if overlap is None:
            overlap = int(cfg.get("chunk_overlap", CHUNK_OVERLAP_ENHANCED))
    assert chunk_size is not None and overlap is not None

    lang = language_for(path)
    parser = _get_parser(lang) if lang else None

    if parser is None:
        # No grammar — fall back to recursive char chunking on the raw source.
        return _fallback_chunks(path, text, chunk_size, overlap, lang)

    try:
        source_bytes = text.encode("utf-8")
        tree = parser.parse(source_bytes)
        kinds = _UNIT_NODES.get(lang, set())  # type: ignore[arg-type]
        units = _walk_units(tree.root_node, kinds) if kinds else []

        if not units:
            # Grammar parsed but found no units — small file or unusual shape.
            return _fallback_chunks(path, text, chunk_size, overlap, lang)

        chunks: list[dict] = []
        idx = 0
        for u in units:
            unit_text = _node_text(source_bytes, u)
            if len(unit_text.strip()) < MIN_CHUNK_CHARS:
                continue
            unit_name = _unit_name(u, source_bytes)
            for piece in _split_oversize(unit_text, chunk_size, overlap):
                if len(piece.strip()) < MIN_CHUNK_CHARS:
                    continue
                chunks.append({
                    "content": piece,
                    "index": idx,
                    "metadata": {
                        "source": str(path),
                        "chunk_size": len(piece),
                        "language": lang,
                        "kind": u.type,
                        "symbol": unit_name,
                        "start_line": u.start_point[0] + 1,
                        "end_line": u.end_point[0] + 1,
                    },
                })
                idx += 1

        if not chunks:
            return _fallback_chunks(path, text, chunk_size, overlap, lang)
        return chunks

    except Exception:
        # Any parser blow-up — fall back rather than dropping the file.
        return _fallback_chunks(path, text, chunk_size, overlap, lang)


def _fallback_chunks(path: Path, text: str, chunk_size: int, overlap: int,
                     lang: str | None) -> list[dict]:
    """Recursive char-chunking fallback that still tags language metadata."""
    pieces = _chunk_recursive(text, chunk_size, overlap)
    return [
        {
            "content": p,
            "index": i,
            "metadata": {
                "source": str(path),
                "chunk_size": len(p),
                "language": lang or "unknown",
                "kind": "fallback",
                "symbol": None,
            },
        }
        for i, p in enumerate(pieces)
        if len(p.strip()) >= MIN_CHUNK_CHARS
    ]


def _unit_name(node, source: bytes) -> str | None:
    """Best-effort extraction of the function/class name from a unit node.

    Walks immediate children looking for an identifier; works across the
    grammars we care about because they all expose the name as a child node
    of type 'identifier' or 'name' near the front of the unit.
    """
    for child in node.children:
        if child.type in {"identifier", "name", "type_identifier"}:
            return _node_text(source, child)
        # Some grammars wrap the name in a declarator node.
        if child.type in {"function_declarator", "declarator", "module_header"}:
            for grand in child.children:
                if grand.type in {"identifier", "name", "type_identifier"}:
                    return _node_text(source, grand)
    return None

