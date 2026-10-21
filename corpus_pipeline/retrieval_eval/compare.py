"""Paired comparison and fixed-seed bootstrap confidence intervals."""

from __future__ import annotations

import math
import random

from .run import rank_metrics


def _metric(row: dict, metric: str) -> float:
    level = metric.split("_", 1)[0]
    rank = row.get(f"{level}_rank")
    if metric.endswith("MRR"):
        return 1 / rank if rank else 0.0
    if metric.endswith("nDCG@10"):
        return 1 / math.log2(rank + 1) if rank and rank <= 10 else 0.0
    cutoff = int(metric.split("R@")[1])
    return float(rank is not None and rank <= cutoff)


METRICS = [f"{level}_{name}" for level in ("strict", "source", "span")
           for name in ("R@1", "R@5", "R@10", "R@30", "MRR")]
METRICS.extend(f"{level}_nDCG@10" for level in ("strict", "source", "span"))


def _percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int(p * (len(values) - 1))))]


def _comparison(pairs: list[tuple[dict, dict]], seed: int) -> dict:
    baseline = rank_metrics([before for before, _ in pairs])
    candidate = rank_metrics([after for _, after in pairs])
    strict_pairs = [(before, after) for before, after in pairs
                    if before.get("strict_defined", True) and after.get("strict_defined", True)]
    deltas = {}
    for name in METRICS:
        selected = strict_pairs if name.startswith("strict_") else pairs
        deltas[name] = (sum(_metric(after, name) - _metric(before, name)
                            for before, after in selected) / len(selected) if selected else None)
    rng = random.Random(seed)
    ci95 = {}
    for name in ("strict_MRR", "source_MRR", "span_MRR", "strict_R@10", "source_R@10", "span_R@10"):
        selected = strict_pairs if name.startswith("strict_") else pairs
        if not selected:
            ci95[name] = None
            continue
        differences = [_metric(after, name) - _metric(before, name) for before, after in selected]
        samples = [sum(rng.choices(differences, k=len(selected))) / len(selected)
                   for _ in range(1000)]
        ci95[name] = [_percentile(samples, .025), _percentile(samples, .975)]
    return {"count": len(pairs), "baseline": baseline, "candidate": candidate,
            "strict_count": len(strict_pairs), "delta": deltas, "ci95": ci95}


def compare_reports(baseline: dict, candidate: dict, seed: int = 7) -> dict:
    before = {row["qid"]: row for row in baseline["questions"]}
    after = {row["qid"]: row for row in candidate["questions"]}
    common = sorted(before.keys() & after.keys())
    if not common:
        raise ValueError("no common qids")
    pairs = [(before[qid], after[qid]) for qid in common]
    result = {"overall": _comparison(pairs, seed)}
    for kind in sorted({b["kind"] for b, _ in pairs}):
        subset = [(b, a) for b, a in pairs if b["kind"] == kind and a["kind"] == kind]
        if subset:
            result[kind] = _comparison(subset, seed)
    ci = result["overall"]["ci95"]["strict_MRR"] or result["overall"]["ci95"]["span_MRR"]
    result["verdict"] = ("candidate better" if ci[0] > 0 else
                         "baseline better" if ci[1] < 0 else "no significant difference")
    result["common_qids"] = len(common)
    return result
