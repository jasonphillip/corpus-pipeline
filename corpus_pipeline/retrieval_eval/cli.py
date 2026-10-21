"""corpus eval build, run, and compare."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

from .. import config
from ..config import corpus_path
from ..manifest import read_manifest
from . import build, compare, run


def _jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _write_json(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _git_rev() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"],
                                cwd=Path(__file__).resolve().parents[2],
                                check=True, capture_output=True, text=True)
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _table(metrics: dict) -> None:
    print("kind        n  strict R@1 R@5 R@10 R@30 MRR nDCG@10  source R@1 R@5 R@10 R@30 MRR nDCG@10  span R@1 R@5 R@10 R@30 MRR nDCG@10  med/p90 ms")
    for kind, row in metrics.items():
        strict, source, span = row["strict"], row["source"], row["span"]
        print(f"{kind:<10} {row['count']:>3}  "
              + " ".join(f"{strict[f'R@{k}']:.2f}" for k in (1, 5, 10, 30))
              + f" {strict['MRR']:.2f} {strict['nDCG@10']:.2f}     "
              + " ".join(f"{source[f'R@{k}']:.2f}" for k in (1, 5, 10, 30))
              + f" {source['MRR']:.2f} {source['nDCG@10']:.2f}  "
              + " ".join(f"{span[f'R@{k}']:.2f}" for k in (1, 5, 10, 30))
              + f" {span['MRR']:.2f} {span['nDCG@10']:.2f}  {row['median_ms']:.0f}/{row['p90_ms']:.0f}")


def _compare_table(result: dict) -> None:
    print("kind        n  strict/source/span delta MRR; R@10  strict comparable n")
    for kind, row in result.items():
        if kind in ("verdict", "common_qids"):
            continue
        delta = row["delta"]
        def fmt(value):
            return "n/a" if value is None else f"{value:+.2f}"
        print(f"{kind:<10} {row['count']:>3}  "
              + "  ".join(f"{level} {fmt(delta[level + '_MRR'])};{fmt(delta[level + '_R@10'])}"
                          for level in ("strict", "source", "span"))
              + f"  {row['strict_count']}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="corpus eval", description="Synthetic retrieval benchmark for local corpora.")
    commands = parser.add_subparsers(dest="action", required=True)
    maker = commands.add_parser("build", help="Sample indexed sources and generate questions")
    maker.add_argument("--corpus", required=True)
    maker.add_argument("--n", type=int, default=120)
    maker.add_argument("--seed", type=int, default=7)
    maker.add_argument("--out", type=Path)
    maker.add_argument("--append-regression", type=Path, metavar="PATH")
    runner = commands.add_parser("run", help="Run production search and score ranked hits",
                                 description=("Production search env overrides pass through unchanged: "
                                              + ", ".join(run.ENV_KEYS) + "."))
    runner.add_argument("--questions", required=True, type=Path)
    runner.add_argument("--corpora", help="Comma-separated federation; default is each question's corpus")
    runner.add_argument("--top-k", type=int, default=30)
    runner.add_argument("--since", help="Earliest chunk date: YYYY, YYYY-MM, or YYYY-MM-DD")
    runner.add_argument("--until", help="Latest chunk date, inclusive")
    runner.add_argument("--label", default="baseline")
    runner.add_argument("--search-arg", action="append", default=[], metavar="KEY=VALUE",
                        help="Override fusion, expansion, conversation_cap, or display without editing manifests")
    runner.add_argument("--out", type=Path)
    compared = commands.add_parser("compare", help="Paired bootstrap comparison on common qids")
    compared.add_argument("baseline", type=Path)
    compared.add_argument("candidate", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.action == "build":
            records, notes = build.build_questions(args.corpus, n=args.n, seed=args.seed,
                                                     append_regression=args.append_regression)
            today = datetime.now().date().isoformat()
            output = args.out or corpus_path(args.corpus) / "eval" / f"questions-{today}-{args.seed}.jsonl"
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("w", encoding="utf-8") as stream:
                for record in records:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            for note in notes:
                if not note.startswith("skipped chunks:"):
                    print(note, file=sys.stderr)
            print(f"Wrote {len(records)} questions: {output}")
            print(next(note for note in notes if note.startswith("skipped chunks:")))
            return 0
        if args.action == "run":
            questions = _jsonl(args.questions)
            if not questions:
                raise ValueError("questions file is empty")
            corpora = [part.strip() for part in args.corpora.split(",") if part.strip()] if args.corpora else None
            if args.corpora and not corpora:
                raise ValueError("--corpora is empty")
            settings = run.parse_settings(args.search_arg)
            report = run.evaluate_questions(questions, top_k=args.top_k, corpora=corpora,
                                            settings_overrides=settings, since=args.since, until=args.until)
            selected = corpora or sorted({q["corpus"] for q in questions})
            manifests = {name: read_manifest(name) for name in selected}
            report["config"] = {
                "questions_path": str(args.questions.resolve()), "top_k": args.top_k,
                "corpora": corpora, "search_args": settings,
                "since": args.since, "until": args.until,
                "env_overrides": run.environment_snapshot(), "git_rev": _git_rev(),
                "embedding": {name: manifest.get("embedding") for name, manifest in manifests.items()},
                "manifest_search": {name: manifest.get("search") for name, manifest in manifests.items()},
                "search_defaults": {"CORPUS_SEARCH_RERANK": config.SEARCH_RERANK,
                                    "CORPUS_FTS_MATCH": config.SEARCH_FTS_MATCH,
                                    "SEARCH_FTS_PHRASE": config.SEARCH_FTS_PHRASE,
                                    "SEARCH_PHRASE_BONUS": config.SEARCH_PHRASE_BONUS,
                                    "SEARCH_PHRASE_RRF_WEIGHT": config.SEARCH_PHRASE_RRF_WEIGHT,
                                    "CORPUS_QUERY_EXPANSION": config.SEARCH_QUERY_EXPANSION,
                                    "CORPUS_EXCLUDE_CAPTIONS": config.SEARCH_EXCLUDE_CAPTIONS,
                                    "CORPUS_PER_SOURCE_LIMIT": config.SEARCH_PER_SOURCE_LIMIT},
            }
            report["label"] = args.label
            report["created_at"] = datetime.now(timezone.utc).isoformat()
            safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", args.label).strip(".-") or "run"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            output = args.out or corpus_path(questions[0]["corpus"]) / "eval" / "runs" / f"{stamp}-{safe_label}.json"
            _write_json(output, report)
            _table(report["metrics"])
            print(f"Run: {output}")
            return 0
        result = compare.compare_reports(json.loads(args.baseline.read_text()),
                                         json.loads(args.candidate.read_text()))
        _compare_table(result)
        print(result["verdict"])
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"corpus eval: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
