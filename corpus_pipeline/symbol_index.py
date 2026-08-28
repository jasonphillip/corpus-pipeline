"""Symbol + xref tables for the code corpus, built from universal-ctags JSON.

These tables ride alongside the existing chunks/chunks_fts/chunks_vec schema
in the same SQLite DB. ctags is the data source — it's fast (handles thousands
of files in seconds), it's installed by default on Linux ARM64, and its JSON
output via `--output-format=json --fields=+ne+l --extras=+r` covers Python,
C, C++, and (with verible/iverilog tags-style) Verilog adequately for v1.

LSP-precise queries (pyright, clangd, verible-verilog-ls) are a future upgrade;
ctags is the right primitive for bulk indexing across a polyglot tree.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Iterable


def init_code_tables(conn: sqlite3.Connection) -> None:
    """Create symbols + xrefs tables and indexes. Idempotent."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo TEXT NOT NULL,
            source_path TEXT NOT NULL,
            line INTEGER NOT NULL,
            end_line INTEGER,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            scope TEXT,
            signature TEXT,
            language TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
        CREATE INDEX IF NOT EXISTS idx_symbols_repo ON symbols(repo);
        CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(source_path);
        CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);

        CREATE TABLE IF NOT EXISTS xrefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_symbol_id INTEGER,
            callee_name TEXT NOT NULL,
            source_path TEXT NOT NULL,
            line INTEGER NOT NULL,
            FOREIGN KEY (caller_symbol_id) REFERENCES symbols(id)
        );

        CREATE INDEX IF NOT EXISTS idx_xrefs_callee ON xrefs(callee_name);
        CREATE INDEX IF NOT EXISTS idx_xrefs_caller ON xrefs(caller_symbol_id);
    """)
    conn.commit()


def have_ctags() -> bool:
    """True if a usable ctags binary is on PATH (universal-ctags only)."""
    bin_path = shutil.which("ctags")
    if not bin_path:
        return False
    try:
        out = subprocess.run([bin_path, "--version"], capture_output=True,
                             text=True, timeout=5)
        return "Universal Ctags" in (out.stdout + out.stderr)
    except (subprocess.SubprocessError, OSError):
        return False


def run_ctags(repo_root: Path, files: Iterable[Path]) -> list[dict]:
    """Run ctags over the given files, returning a list of tag dicts.

    Files must be inside repo_root. ctags is invoked with --output-format=json
    so we don't have to parse its legacy tab-separated output. --extras=+r
    enables reference tags for cross-references.
    """
    rel_files = [str(f.relative_to(repo_root)) for f in files]
    if not rel_files:
        return []

    cmd = [
        "ctags",
        "--output-format=json",
        "--fields=+neKlS",  # name, end-line, kind, line, language, signature
        "--extras=+r",
        "--languages=Python,C,C++,SystemVerilog,Verilog",
        "-f", "-",
    ] + rel_files

    proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True,
                          text=True, timeout=300)
    if proc.returncode != 0 and not proc.stdout:
        # ctags writes errors per-file to stderr but still emits valid JSON
        # for the files it understood. Only treat as fatal when there's no
        # stdout at all.
        raise RuntimeError(
            f"ctags failed in {repo_root}: {proc.stderr.strip()[:500]}"
        )

    tags: list[dict] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            tags.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return tags


def ingest_tags(conn: sqlite3.Connection, repo_name: str,
                tags: list[dict]) -> tuple[int, int]:
    """Insert tags into symbols (definitions) and xrefs (references).

    Returns (definitions_inserted, references_inserted). Splits on the 'roles'
    field that --extras=+r produces — references have role='ref', definitions
    don't have a roles field (or have role='def').
    """
    defs_count = 0
    refs_count = 0

    # Wipe prior rows for this repo so re-runs are idempotent.
    conn.execute("DELETE FROM xrefs WHERE source_path IN "
                 "(SELECT source_path FROM symbols WHERE repo = ?)",
                 (repo_name,))
    conn.execute("DELETE FROM symbols WHERE repo = ?", (repo_name,))

    # Build a name -> latest definition id map so we can attribute references
    # to a calling symbol at insert time. Imperfect (there can be multiple
    # definitions of the same name across files), but sufficient for v1.
    name_to_id: dict[str, int] = {}

    for t in tags:
        if t.get("_type") != "tag":
            continue
        name = t.get("name")
        path = t.get("path")
        line = t.get("line")
        if not (name and path and line):
            continue

        roles = t.get("roles", "")
        is_ref = isinstance(roles, str) and "ref" in roles.split(",")

        if is_ref:
            cur = conn.execute(
                "INSERT INTO xrefs (caller_symbol_id, callee_name, source_path, line) "
                "VALUES (?, ?, ?, ?)",
                (None, name, path, int(line)),
            )
            refs_count += 1
        else:
            cur = conn.execute(
                "INSERT INTO symbols "
                "(repo, source_path, line, end_line, name, kind, scope, signature, language) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    repo_name,
                    path,
                    int(line),
                    _to_int(t.get("end")),
                    name,
                    t.get("kind", "unknown"),
                    t.get("scope"),
                    t.get("signature"),
                    t.get("language"),
                ),
            )
            name_to_id[name] = cur.lastrowid or 0
            defs_count += 1

    # Best-effort caller attribution for references: link each xref to the
    # most recently seen symbol whose extent covers that line. Simple and
    # cheap; correct for the common case where references happen inside a
    # function/method body of an enclosing definition.
    conn.execute("""
        UPDATE xrefs
        SET caller_symbol_id = (
            SELECT s.id FROM symbols s
            WHERE s.source_path = xrefs.source_path
              AND s.line <= xrefs.line
              AND (s.end_line IS NULL OR s.end_line >= xrefs.line)
            ORDER BY s.line DESC
            LIMIT 1
        )
        WHERE caller_symbol_id IS NULL
    """)

    conn.commit()
    return defs_count, refs_count


def _to_int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def find_symbol(conn: sqlite3.Connection, name: str,
                repo: str | None = None) -> list[dict]:
    """Look up a symbol by exact name. Returns rows ordered by repo, path."""
    if repo:
        rows = conn.execute(
            "SELECT * FROM symbols WHERE name = ? AND repo = ? "
            "ORDER BY source_path, line",
            (name, repo),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM symbols WHERE name = ? ORDER BY repo, source_path, line",
            (name,),
        ).fetchall()
    return [dict(r) for r in rows]


def find_callers(conn: sqlite3.Connection, name: str) -> list[dict]:
    """Return every (file, line, calling_symbol) referencing `name`."""
    rows = conn.execute(
        """
        SELECT x.source_path, x.line, x.callee_name,
               s.name AS caller_name, s.kind AS caller_kind, s.repo AS caller_repo
        FROM xrefs x
        LEFT JOIN symbols s ON s.id = x.caller_symbol_id
        WHERE x.callee_name = ?
        ORDER BY x.source_path, x.line
        """,
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_code_stats(conn: sqlite3.Connection) -> dict:
    """Counts for symbols, xrefs, and per-repo distribution."""
    sym = conn.execute("SELECT COUNT(*) AS c FROM symbols").fetchone()["c"]
    refs = conn.execute("SELECT COUNT(*) AS c FROM xrefs").fetchone()["c"]
    by_repo = {
        r["repo"]: r["c"]
        for r in conn.execute(
            "SELECT repo, COUNT(*) AS c FROM symbols GROUP BY repo"
        ).fetchall()
    }
    return {"total_symbols": sym, "total_xrefs": refs, "by_repo": by_repo}

