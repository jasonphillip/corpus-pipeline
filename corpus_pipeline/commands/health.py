"""Audit local corpus indexes and persist comparable health snapshots."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import CORPUS_ROOT, HEALTH_ROOT, PRIMARY_CORPORA


HEALTH_DB = HEALTH_ROOT / "corpus-health.db"
CANONICAL_KEYS = ("ts", "source_file")
_VEC_DIM_RE = re.compile(r"embedding\s+float\[(\d+)\]")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def discover_corpora(scope: str = "primary") -> list[str]:
    """Return indexed corpora under the configured root."""
    available = sorted(
        path.name
        for path in CORPUS_ROOT.iterdir()
        if path.is_dir() and (path / "processed" / "chunks.db").is_file()
    ) if CORPUS_ROOT.is_dir() else []
    if scope == "primary" and PRIMARY_CORPORA:
        return [name for name in PRIMARY_CORPORA if name in available]
    return available


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }


def _vector_dim(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'chunks_vec'"
    ).fetchone()
    match = _VEC_DIM_RE.search(row[0] or "") if row else None
    return int(match.group(1)) if match else None


def _vector_distance_metric(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'chunks_vec'"
    ).fetchone()
    sql = (row[0] or "") if row else ""
    return "cosine" if re.search(r"\bdistance_metric\s*=\s*cosine\b", sql, re.I) else "l2"


def _manifest_dim(corpus_dir: Path) -> int | None:
    manifest = corpus_dir / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
        dimension = (value.get("embedding") or {}).get("dim")
        return int(dimension) if dimension is not None else None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _metadata_coverage(connection: sqlite3.Connection, sample: int = 500) -> tuple[int, dict[str, float]]:
    rows = connection.execute(
        "SELECT metadata_json FROM chunks ORDER BY id LIMIT ?", (sample,)
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        try:
            metadata = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            continue
        for key, value in metadata.items():
            if value not in (None, "", [], {}):
                counts[key] = counts.get(key, 0) + 1
    total = len(rows)
    coverage = {
        key: round(100.0 * count / max(total, 1), 1) for key, count in counts.items()
    }
    return total, coverage


def _tail_log(corpus_dir: Path) -> str:
    for name in ("ingest.log", "build.log", "processing.log"):
        path = corpus_dir / "meta" / name
        if not path.is_file():
            continue
        lines = [line.strip() for line in path.read_text(errors="replace").splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return "—"


def audit_corpus(corpus: str) -> dict[str, Any]:
    """Collect read-only integrity and coverage metrics for one corpus."""
    corpus_dir = CORPUS_ROOT / corpus
    database = corpus_dir / "processed" / "chunks.db"
    metrics: dict[str, Any] = {"corpus": corpus}
    if not database.is_file():
        return {**metrics, "status": "missing_index"}
    stat = database.stat()
    metrics.update(
        {
            "disk_size_bytes": stat.st_size,
            "db_mtime": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds"),
        }
    )
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    import sqlite_vec

    connection.enable_load_extension(True)
    connection.load_extension(sqlite_vec.loadable_path())
    connection.enable_load_extension(False)
    try:
        tables = _table_names(connection)
        required = {"chunks", "chunks_fts", "chunks_vec", "sources"}
        missing = sorted(required - tables)
        metrics["missing_tables"] = missing
        if missing:
            return {**metrics, "status": "incomplete_schema"}
        metrics["journal_mode"] = connection.execute("PRAGMA journal_mode").fetchone()[0]
        metrics["quick_check"] = connection.execute("PRAGMA quick_check").fetchone()[0]
        metrics["total_chunks"] = connection.execute(
            "SELECT COUNT(*) FROM chunks"
        ).fetchone()[0]
        metrics["distinct_sources"] = connection.execute(
            "SELECT COUNT(DISTINCT source_path) FROM chunks"
        ).fetchone()[0]
        metrics["source_rows"] = connection.execute(
            "SELECT COUNT(*) FROM sources"
        ).fetchone()[0]
        metrics["fts_rows"] = connection.execute(
            "SELECT COUNT(*) FROM chunks_fts"
        ).fetchone()[0]
        metrics["vector_rows"] = connection.execute(
            "SELECT COUNT(*) FROM chunks_vec"
        ).fetchone()[0]
        metrics["missing_vec_rows"] = connection.execute(
            "SELECT COUNT(*) FROM chunks c LEFT JOIN chunks_vec v ON v.id = c.id "
            "WHERE v.id IS NULL"
        ).fetchone()[0]
        metrics["orphan_chunks_no_source"] = connection.execute(
            "SELECT COUNT(*) FROM chunks c LEFT JOIN sources s "
            "ON s.source_path = c.source_path WHERE s.source_path IS NULL"
        ).fetchone()[0]
        metrics["orphan_sources_no_chunks"] = connection.execute(
            "SELECT COUNT(*) FROM sources s LEFT JOIN chunks c "
            "ON c.source_path = s.source_path WHERE c.id IS NULL"
        ).fetchone()[0]
        metrics["avg_content_len"] = round(
            connection.execute("SELECT AVG(content_len) FROM chunks").fetchone()[0] or 0,
            1,
        )
        timestamp = (
            "COALESCE(NULLIF(json_extract(metadata_json, '$.ts'), ''), "
            "NULLIF(json_extract(metadata_json, '$.started_at'), ''), "
            "NULLIF(json_extract(metadata_json, '$.created_at'), ''), "
            "NULLIF(json_extract(metadata_json, '$.content_date'), ''))"
        )
        date_row = connection.execute(
            f"SELECT MIN({timestamp}), MAX({timestamp}) FROM chunks"
        ).fetchone()
        metrics["date_range"] = [date_row[0], date_row[1]]
        metrics["last_log_line"] = _tail_log(corpus_dir)
        metrics["dim_chunks_vec"] = _vector_dim(connection)
        metrics["vec_distance_metric"] = _vector_distance_metric(connection)
        metrics["dim_manifest"] = _manifest_dim(corpus_dir)
        metrics["dim_mismatch"] = bool(
            metrics["dim_chunks_vec"]
            and metrics["dim_manifest"]
            and metrics["dim_chunks_vec"] != metrics["dim_manifest"]
        )
        sample_size, coverage = _metadata_coverage(connection)
        metrics["metadata_sample_size"] = sample_size
        metrics["metadata_keys"] = coverage
        metrics["status"] = "ok"
        return metrics
    finally:
        connection.close()


def collect_flags(metrics: dict[str, Any]) -> list[dict[str, str]]:
    """Turn metrics into actionable findings."""
    corpus = str(metrics.get("corpus", "?"))
    flags: list[dict[str, str]] = []

    def add(code: str, severity: str, summary: str, remediation: str) -> None:
        flags.append(
            {
                "code": code,
                "corpus": corpus,
                "severity": severity,
                "summary": summary,
                "remediation": remediation,
            }
        )

    if metrics.get("status") != "ok":
        add(
            "status",
            "CRITICAL",
            f"status is {metrics.get('status')}",
            "Rebuild or repair the index schema.",
        )
        return flags
    if metrics.get("quick_check") != "ok":
        add("sqlite", "CRITICAL", "SQLite quick check failed", "Restore from a known-good index or rebuild.")
    if metrics.get("missing_vec_rows"):
        add("vectors", "CRITICAL", f"{metrics['missing_vec_rows']} chunks lack vectors", "Reprocess the affected corpus.")
    if metrics.get("fts_rows") != metrics.get("total_chunks"):
        add("fts", "WARN", "FTS row count differs from chunk count", "Rebuild the FTS table from chunks.")
    if metrics.get("dim_mismatch"):
        add("dimension", "CRITICAL", "Manifest and vector dimensions differ", "Use the original embedding model or rebuild the corpus.")
    if metrics.get("vec_distance_metric") == "l2":
        add("vector_metric", "WARN", "Vector table uses L2 distance", "Rebuild the corpus for cosine distance.")
    if metrics.get("orphan_chunks_no_source"):
        add("sources", "WARN", "Chunks exist without source-state rows", "Run process again to reconcile source state.")
    coverage = metrics.get("metadata_keys") or {}
    for key in CANONICAL_KEYS:
        if metrics.get("metadata_sample_size", 0) and coverage.get(key, 0.0) < 50.0:
            add("metadata", "INFO", f"metadata key {key} has low coverage", "Add normalized frontmatter before chronological queries.")
    return flags


def render_md(metrics_list: list[dict[str, Any]], scope: str, generated_at: str) -> str:
    """Render a health report with structured flags."""
    flags = [flag for metrics in metrics_list for flag in collect_flags(metrics)]
    critical = len({flag["corpus"] for flag in flags if flag["severity"] == "CRITICAL"})
    warned = len({flag["corpus"] for flag in flags if flag["severity"] == "WARN"})
    healthy = len(metrics_list) - len({flag["corpus"] for flag in flags if flag["severity"] in {"CRITICAL", "WARN"}})
    lines = [
        "---",
        f"created: {generated_at}",
        f"scope: {scope}",
        f"corpora_audited: {len(metrics_list)}",
        "---",
        "",
        "# Corpus health",
        "",
        f"**Summary:** {healthy} healthy · {warned} with warnings · {critical} critical",
        "",
        "## Findings",
        "",
    ]
    if not flags:
        lines.append("No findings.")
    else:
        lines.extend(["| Corpus | Severity | Finding | Remediation |", "|---|---|---|---|"])
        for flag in flags:
            lines.append(
                f"| `{flag['corpus']}` | **{flag['severity']}** | {flag['summary']} | {flag['remediation']} |"
            )
    lines.append("")
    for metrics in metrics_list:
        lines.extend([f"## {metrics['corpus']}", ""])
        for key in sorted(key for key in metrics if key not in {"corpus", "metadata_keys"}):
            lines.append(f"- **{key}:** `{metrics[key]}`")
        if metrics.get("metadata_keys"):
            lines.extend(["", "### Metadata coverage", "", "| Key | Present |", "|---|---|"])
            for key, value in sorted(metrics["metadata_keys"].items()):
                lines.append(f"| `{key}` | {value}% |")
        lines.append("")
    return "\n".join(lines) + "\n"


def _health_connection() -> sqlite3.Connection:
    HEALTH_ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(HEALTH_DB))
    connection.execute(
        "CREATE TABLE IF NOT EXISTS metrics ("
        "snapshot_ts TEXT NOT NULL, corpus TEXT NOT NULL, key TEXT NOT NULL, "
        "value TEXT, PRIMARY KEY(snapshot_ts, corpus, key))"
    )
    return connection


def persist(metrics_list: list[dict[str, Any]], dedupe_day: bool = True) -> int:
    """Persist one metric snapshot. Replace earlier rows from the same day."""
    connection = _health_connection()
    timestamp = now_iso()
    if dedupe_day:
        for corpus in {metrics["corpus"] for metrics in metrics_list}:
            connection.execute(
                "DELETE FROM metrics WHERE corpus = ? AND substr(snapshot_ts, 1, 10) = ?",
                (corpus, timestamp[:10]),
            )
    count = 0
    for metrics in metrics_list:
        for key, value in metrics.items():
            if key == "corpus":
                continue
            rendered = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
            connection.execute(
                "INSERT INTO metrics(snapshot_ts, corpus, key, value) VALUES (?, ?, ?, ?)",
                (timestamp, metrics["corpus"], key, rendered),
            )
            count += 1
    connection.commit()
    connection.close()
    return count


def _resolve_snapshot(connection: sqlite3.Connection, date: str) -> str | None:
    row = connection.execute(
        "SELECT snapshot_ts FROM metrics WHERE substr(snapshot_ts, 1, 10) <= ? "
        "ORDER BY snapshot_ts DESC LIMIT 1",
        (date,),
    ).fetchone()
    return row[0] if row else None


def run_diff(date_a: str, date_b: str) -> str:
    """Compare the closest snapshots on or before two dates."""
    if not HEALTH_DB.is_file():
        return "No baseline exists. Run the health command first.\n"
    connection = sqlite3.connect(str(HEALTH_DB))
    try:
        snapshot_a = _resolve_snapshot(connection, date_a)
        snapshot_b = _resolve_snapshot(connection, date_b)
        if not snapshot_a or not snapshot_b:
            return "No baseline exists for one or both dates.\n"
        def load(timestamp: str) -> dict[tuple[str, str], str]:
            return {
                (corpus, key): value
                for corpus, key, value in connection.execute(
                    "SELECT corpus, key, value FROM metrics WHERE snapshot_ts = ?",
                    (timestamp,),
                )
            }
        before, after = load(snapshot_a), load(snapshot_b)
        lines = [
            "# Corpus health diff",
            "",
            f"- Snapshot A: `{snapshot_a}`",
            f"- Snapshot B: `{snapshot_b}`",
            "",
            "| Corpus | Metric | Before | After |",
            "|---|---|---|---|",
        ]
        changed = 0
        for corpus, key in sorted(set(before) | set(after)):
            old, new = before.get((corpus, key), "—"), after.get((corpus, key), "—")
            if old != new:
                lines.append(f"| `{corpus}` | `{key}` | `{old}` | `{new}` |")
                changed += 1
        if not changed:
            lines.append("| _No differences_ | | | |")
        return "\n".join(lines) + "\n"
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corpus health", description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    diff_parser = subparsers.add_parser("diff")
    diff_parser.add_argument("date_a")
    diff_parser.add_argument("date_b")
    parser.add_argument("--scope", choices=["primary", "all"], default="primary")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--no-dedupe-day", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "diff":
        sys.stdout.write(run_diff(args.date_a, args.date_b))
        return 0
    corpora = discover_corpora(args.scope)
    if not corpora:
        print(f"No indexed corpora under {CORPUS_ROOT}", file=sys.stderr)
        return 1
    metrics_list = [audit_corpus(corpus) for corpus in corpora]
    report = render_md(metrics_list, args.scope, now_iso())
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    if not args.no_persist:
        persist(metrics_list, dedupe_day=not args.no_dedupe_day)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
