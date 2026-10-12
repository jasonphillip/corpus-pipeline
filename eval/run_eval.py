#!/usr/bin/env python3
"""Measure document-level retrieval on the synthetic, passage-checked set."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def load_judgments(dataset: Path) -> list[dict]:
    """Reject missing documents and passages that are not exact substrings."""
    rows = [json.loads(line) for line in (dataset / "queries.jsonl").read_text().splitlines()
            if line.strip()]
    for row in rows:
        if row.get("split") not in {"eval", "tune"} or not row.get("relevant"):
            raise ValueError(f"invalid split or empty judgments: {row.get('id')}")
        for judgment in row["relevant"]:
            name = judgment["doc"]
            if Path(name).name != name:
                raise ValueError(f"invalid document name: {name}")
            path = dataset / "docs" / name
            if not path.is_file() or not judgment["passage"] or judgment["passage"] not in path.read_text(encoding="utf-8"):
                raise ValueError(f"judged passage absent: {row['id']} {name}: {judgment['passage']!r}")
    return rows


def score_ranking(ranked_docs: list[str], relevant: set[str]) -> dict:
    """Binary document relevance; duplicate chunks from one document count once."""
    unique = list(dict.fromkeys(ranked_docs))
    ranks = {doc: unique.index(doc) + 1 if doc in unique else None for doc in sorted(relevant)}
    def recall(k: int) -> float:
        return sum(doc in relevant for doc in unique[:k]) / len(relevant)
    dcg = sum(1 / math.log2(i + 2) for i, doc in enumerate(unique[:10]) if doc in relevant)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), 10)))
    return {"recall_at_5": recall(5), "recall_at_10": recall(10),
            "ndcg_at_10": dcg / ideal, "relevant_document_count": len(relevant),
            "relevant_doc_ranks": ranks}


def _percentile(values: list[float], fraction: float) -> float:
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lo = math.floor(position)
    hi = math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def _command(args: list[str]) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=True)
        return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def _fake_config() -> dict:
    return {"provider": "fake", "url": "", "model": "fake (mechanics only)",
            "dims": 2, "chunk_size": 6000, "chunk_overlap": 600,
            "label": "fake (mechanics only)"}


def _package_version() -> str:
    try:
        return importlib.metadata.version("corpus-pipeline")
    except importlib.metadata.PackageNotFoundError:
        return tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]


def _doc_names(rows: list[dict]) -> set[str]:
    return {item["doc"] for row in rows for item in row["relevant"]}


def run_evaluation(dataset: Path, output_dir: Path, fake_embedder: bool = False) -> tuple[dict, Path]:
    dataset = dataset.resolve()
    rows = load_judgments(dataset)  # Check the complete supplied set before indexing.
    docs = sorted((dataset / "docs").glob("*.md"))
    if not docs:
        raise ValueError("dataset has no markdown documents")
    if not _doc_names(rows) <= {p.name for p in docs}:
        raise ValueError("judgment refers to a document outside docs/")
    eval_rows = [row for row in rows if row["split"] == "eval"]
    tune_rows = [row for row in rows if row["split"] == "tune"]
    if not eval_rows:
        raise ValueError("dataset has no eval queries")

    with tempfile.TemporaryDirectory(prefix="corpus-eval-") as root:
        with patch.dict(os.environ, {"CORPUS_ROOT": root}):
            from corpus_pipeline import chunker, config, corpus_db, embedder, manifest, ollama_llm
            from corpus_pipeline.commands import process
        topic_dir = Path(root) / "retrieval-eval"
        raw = topic_dir / "raw"
        raw.mkdir(parents=True)
        for path in docs:
            shutil.copy2(path, raw / path.name)
        cfg = _fake_config() if fake_embedder else config.get_embedding_config()
        if not fake_embedder and cfg["provider"] != "openai":
            raise ValueError("real evaluation requires CORPUS_EMBED_PROVIDER=openai")
        path_fn = lambda topic: topic_dir
        with ExitStack() as stack:
            for module in (config, corpus_db, manifest, process):
                stack.enter_context(patch.object(module, "corpus_path", path_fn))
            stack.enter_context(patch.object(config, "CORPUS_ROOT", Path(root)))
            if fake_embedder:
                vector = struct.pack("<2f", 1.0, 0.0)
                for module in (process, chunker, embedder):
                    stack.enter_context(patch.object(module, "get_embedding_config", lambda: cfg))
                stack.enter_context(patch.object(process, "get_dims", lambda: 2))
                stack.enter_context(patch.object(process, "get_label", lambda: cfg["label"]))
                stack.enter_context(patch.object(process, "_embed_with_fallback",
                                                 lambda texts: ([vector] * len(texts), 0)))
                stack.enter_context(patch.object(embedder, "embed_query", lambda query: vector))
            embedder.reset_config_cache()
            start = time.perf_counter()
            process.process_topic("retrieval-eval")
            indexing_seconds = time.perf_counter() - start
            db_path = topic_dir / "processed" / "chunks.db"
            conn = corpus_db.connect("retrieval-eval")
            try:
                counts = corpus_db.get_stats(conn)
                if counts["unique_sources"] != len(docs):
                    raise RuntimeError(f"indexed {counts['unique_sources']} of {len(docs)} documents")

                def retrieve(mode: str, query: str) -> list[str]:
                    limit = max(10, counts["total_chunks"])
                    if mode == "bm25":
                        candidates = corpus_db.search_bm25(conn, query, limit=limit)
                    elif mode == "vector":
                        candidates = corpus_db.search_vector(conn, embedder.embed_query(query), limit=limit)
                    else:
                        candidates = corpus_db.search_hybrid(
                            conn, query, embedder.embed_query(query), top_k=limit,
                            per_source_limit=limit)
                        if mode == "hybrid_rerank":
                            candidates = ollama_llm.rerank_chunks(query, candidates[:20],
                                                                  model=config.MODEL_SCORING)
                    return [Path(item["source_path"]).name for item in candidates]

                modes = {}
                per_query = {row["id"]: {"query": row["query"], "relevant_docs": sorted(_doc_names([row])),
                                         "modes": {}} for row in eval_rows}
                rerank_configured = not fake_embedder and any(os.environ.get(name) for name in (
                    "CORPUS_TEST_OLLAMA_URL", "OLLAMA_HOST", "CORPUS_RERANK_URL", "CORPUS_LLM_CHAT_URL"))
                rerank_configured = rerank_configured or (not fake_embedder and any(
                    config._cfg(key, "") for key in (
                        "services.ollama.base_url", "services.reranker.url", "services.chat.url")))
                test_ollama = os.environ.get("CORPUS_TEST_OLLAMA_URL", "").strip().rstrip("/")
                if test_ollama:
                    stack.enter_context(patch.object(ollama_llm, "OLLAMA_GENERATE_URL",
                                                     f"{test_ollama}/api/generate"))
                for mode in ("bm25", "vector", "hybrid", "hybrid_rerank"):
                    if mode == "hybrid_rerank" and not rerank_configured:
                        modes[mode] = {"status": "unrun", "reason": "no Ollama or reranker explicitly configured"}
                        continue
                    try:
                        retrieve(mode, eval_rows[0]["query"])  # Untimed warmup per mode.
                        latencies = []
                        scores = []
                        for row in eval_rows:
                            tick = time.perf_counter()
                            ranked = retrieve(mode, row["query"])
                            latencies.append((time.perf_counter() - tick) * 1000)
                            score = score_ranking(ranked, _doc_names([row]))
                            scores.append(score)
                            per_query[row["id"]]["modes"][mode] = score
                        modes[mode] = {"status": "run", "query_count": len(scores),
                                       "warmup_queries": 1,
                                       "relevant_document_denominator": "judged relevant documents per query",
                                       "recall_at_5": statistics.mean(s["recall_at_5"] for s in scores),
                                       "recall_at_10": statistics.mean(s["recall_at_10"] for s in scores),
                                       "ndcg_at_10": statistics.mean(s["ndcg_at_10"] for s in scores),
                                       "p50_latency_ms": _percentile(latencies, 0.50),
                                       "p95_latency_ms": _percentile(latencies, 0.95)}
                    except Exception as exc:
                        if mode != "hybrid_rerank":
                            raise
                        modes[mode] = {"status": "unrun", "reason": f"{type(exc).__name__}: {exc}"}
                        for entry in per_query.values():
                            entry["modes"].pop(mode, None)
                failures = [{"id": qid, "query": value["query"],
                             "missed_docs": [doc for doc, rank in value["modes"]["hybrid"]["relevant_doc_ranks"].items()
                                             if rank is None or rank > 5]}
                            for qid, value in per_query.items()
                            if any(rank is None or rank > 5 for rank in
                                   value["modes"]["hybrid"]["relevant_doc_ranks"].values())]
            finally:
                conn.close()
                embedder.reset_config_cache()

        revision = _command(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"]) or "unknown"
        gpu = _command(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        result = {"created_utc": datetime.now(timezone.utc).isoformat(), "git_revision": revision,
                  "package_version": _package_version(),
                  "python_version": platform.python_version(),
                  "sqlite_vec_version": importlib.metadata.version("sqlite-vec"),
                  "embedder": cfg["label"] if fake_embedder else cfg["model"],
                  "embedding_model_id": cfg["model"], "embedding_dims_configured": cfg["dims"],
                  "chunk_settings": {"size": cfg["chunk_size"], "overlap": cfg["chunk_overlap"]},
                  "corpus": {"documents": len(docs), "chunks": counts["total_chunks"]},
                  "hardware": {"platform": platform.platform(), "machine": platform.machine(), "gpu": gpu},
                  "indexing_wall_seconds": indexing_seconds, "chunks_db_bytes": db_path.stat().st_size,
                  "split": {"eval_queries": len(eval_rows), "tune_queries": len(tune_rows),
                            "tune_query_ids": [row["id"] for row in tune_rows]},
                  "modes": modes, "per_query": per_query, "failure_examples": failures}
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "-fake" if fake_embedder else ""
    stem = f"{datetime.now(timezone.utc):%Y-%m-%d}-{revision}{suffix}"
    json_path = output_dir / f"{stem}.json"
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    summary = [f"# Retrieval evaluation: {stem}", "",
               f"Git revision: `{revision}`. Documents: {len(docs)}. Chunks: {counts['total_chunks']}.",
               f"Eval queries: {len(eval_rows)}. Tune queries excluded: {len(tune_rows)}.", "",
               "Recall uses judged relevant documents per query as its denominator. Metrics average across eval queries.",
               "", "| Mode | Recall@5 | Recall@10 | nDCG@10 | p50 ms | p95 ms | Queries |", 
               "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, mode in modes.items():
        if mode["status"] == "unrun":
            summary.append(f"| {name} | unrun: {mode['reason']} | | | | | |")
        else:
            summary.append(f"| {name} | {mode['recall_at_5']:.3f} | {mode['recall_at_10']:.3f} | "
                           f"{mode['ndcg_at_10']:.3f} | {mode['p50_latency_ms']:.2f} | "
                           f"{mode['p95_latency_ms']:.2f} | {mode['query_count']} |")
    if fake_embedder:
        summary.extend(["", "Fake embedder: these numbers are mechanics only, not retrieval quality."])
    summary.extend(["", "One untimed warmup query preceded each mode; timed queries share the same fresh index."])
    json_path.with_suffix(".md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return result, json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=REPO / "eval" / "dataset")
    parser.add_argument("--output-dir", type=Path, default=REPO / "eval" / "results")
    parser.add_argument("--fake-embedder", action="store_true")
    parser.add_argument("--check-passages", action="store_true", help="Validate judgments without indexing")
    args = parser.parse_args(argv)
    if args.check_passages:
        rows = load_judgments(args.dataset)
        print(f"Verified {sum(len(row['relevant']) for row in rows)} judged passages in {len(rows)} queries")
        return 0
    result, path = run_evaluation(args.dataset, args.output_dir, args.fake_embedder)
    print(path)
    for name, mode in result["modes"].items():
        if mode["status"] == "run":
            print(f"{name}: R@5={mode['recall_at_5']:.3f} R@10={mode['recall_at_10']:.3f} "
                  f"nDCG@10={mode['ndcg_at_10']:.3f} p50={mode['p50_latency_ms']:.2f}ms "
                  f"p95={mode['p95_latency_ms']:.2f}ms n={mode['query_count']}")
        else:
            print(f"{name}: unrun ({mode['reason']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
