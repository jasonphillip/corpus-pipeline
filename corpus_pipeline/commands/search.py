#!/usr/bin/env python3
"""Hybrid BM25 + vector search over one or more corpora with reranking, query expansion, and CONTEXT.md injection.

Federation:
  - Single corpus (positional):       search_hybrid.py <topic> <query>
  - Multiple corpora (comma list):    search_hybrid.py --corpora a,b,c <query>
  - All primary corpora:              search_hybrid.py --all <query>

Federation merges per-corpus result lists with Reciprocal Rank Fusion (RRF, k=60;
Cormack et al. 2009). Each candidate's RRF score is the sum of 1/(k + rank) over
the corpora that returned it. Reranker then re-scores the federated top-K.
"""

import argparse
import re
import sys
from pathlib import Path


from ..config import (
    corpus_path, SEARCH_TOP_K, SEARCH_RERANK, SEARCH_RERANK_MODEL,
    SEARCH_QUERY_EXPANSION, SEARCH_SMART_MIN_CHUNKS,
    SEARCH_EXCLUDE_CAPTIONS, SEARCH_CAPTION_PREFIX, SEARCH_PER_SOURCE_LIMIT,
    PRIMARY_CORPORA, CORPUS_ROOT,
)
from ..corpus_db import connect, search_hybrid, get_stats, get_vec_dim
from ..embedder import embed_query, get_label




# Cormack et al. 2009, "Reciprocal Rank Fusion outperforms Condorcet and individual
# Rank Learning Methods", SIGIR.
RRF_K = 60


def _rrf_merge(per_corpus_results: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """Merge ranked result lists from multiple corpora using Reciprocal Rank Fusion.

    Each input list is assumed sorted best-first. A candidate's rrf_score is
    sum(1/(k+rank)) over every corpus that returned it (rank is 1-indexed).
    Candidates are keyed by (corpus, id) since the same chunk id can exist in
    multiple corpora.
    """
    fused: dict[tuple[str, int], dict] = {}
    for results in per_corpus_results:
        for rank, r in enumerate(results, start=1):
            key = (r.get("corpus", ""), r["id"])
            contribution = 1.0 / (k + rank)
            if key in fused:
                fused[key]["rrf_score"] += contribution
            else:
                r_copy = dict(r)
                r_copy["rrf_score"] = contribution
                r_copy["rrf_rank_in_corpus"] = rank
                fused[key] = r_copy
    out = list(fused.values())
    out.sort(key=lambda x: x["rrf_score"], reverse=True)
    return out


def _search_single_corpus(topic: str, queries: list[str], top_k: int,
                          captions: str = "include",
                          per_source: int | None = None) -> list[dict]:
    """Run hybrid search against one corpus for all query variants, dedup, tag.

    Returns results ordered best-first by the within-corpus hybrid score, with
    each result tagged with its corpus name. Cross-corpus fusion happens in
    _rrf_merge using each result's rank in this returned list.
    """
    db_path = corpus_path(topic) / "processed" / "chunks.db"
    if not db_path.exists():
        print(f"[{topic}] no corpus database at {db_path} — skipping", file=sys.stderr)
        return []

    try:
        conn = connect(topic)
    except Exception as e:
        print(f"[{topic}] failed to open: {e} — skipping", file=sys.stderr)
        return []

    try:
        stats = get_stats(conn)
        chunk_count = stats["total_chunks"]
        print(f"  {topic}: {chunk_count:,} chunks, {stats['unique_sources']} sources")

        # Per-query results merged by chunk id (best score wins)
        merged: dict[int, dict] = {}
        for qi, q in enumerate(queries):
            q_emb = embed_query(q)
            if qi == 0:
                # Query-vs-index dim guard: a mismatched embedder (e.g. the
                # 768-d Ollama fallback against a 2560-d index) produces
                # garbage neighbors, not an error. Skip loudly instead.
                try:
                    index_dims = get_vec_dim(conn)
                except Exception:
                    index_dims = None
                query_dims = len(q_emb) // 4
                if index_dims is not None and index_dims != query_dims:
                    print(f"[{topic}] embedding dim mismatch: query {query_dims}d "
                          f"vs index {index_dims}d — embedder fell back to a "
                          f"different model; skipping this corpus")
                    return []
            results = search_hybrid(conn, q, q_emb, top_k=top_k,
                                    per_source_limit=per_source or SEARCH_PER_SOURCE_LIMIT,
                                    caption_mode=captions)
            for r in results:
                rid = r["id"]
                if rid not in merged or r["score"] > merged[rid]["score"]:
                    merged[rid] = r
    finally:
        conn.close()

    out = list(merged.values())
    out.sort(key=lambda x: x["score"], reverse=True)
    for r in out:
        r["corpus"] = topic
    return out


def search(corpora: list[str], query: str, top_k: int = SEARCH_TOP_K,
           captions: str = "include", per_source: int | None = None):
    """Run hybrid search across one or more corpora and print merged results.

    For a single-corpus invocation the behavior mirrors the original single-
    corpus pipeline (rerank, query expansion, CONTEXT.md injection). For
    federated runs the same pipeline is applied per-corpus, then results are
    merged on per-corpus normalized scores.
    """
    if not corpora:
        print("Error: no corpora specified", file=sys.stderr)
        sys.exit(1)
    if not query or not query.strip():
        print("Error: empty query", file=sys.stderr)
        sys.exit(2)

    federated = len(corpora) > 1

    # Determine global smart-mode flags using the LARGEST corpus's chunk count.
    # In federation, a single small corpus shouldn't disable rerank/expansion
    # for the run as a whole — those are global signals.
    max_chunks = 0
    available = []
    missing = []
    for t in corpora:
        db_path = corpus_path(t) / "processed" / "chunks.db"
        if not db_path.exists():
            print(f"[{t}] no corpus database at {db_path} — skipping", file=sys.stderr)
            missing.append(t)
            continue
        try:
            conn = connect(t)
            stats = get_stats(conn)
            conn.close()
        except Exception as e:
            print(f"[{t}] failed to open: {e} — skipping", file=sys.stderr)
            continue
        max_chunks = max(max_chunks, stats["total_chunks"])
        available.append(t)

    if not available:
        print("Error: none of the requested corpora are searchable", file=sys.stderr)
        # Suggest close local corpus names for a likely typo.
        if missing:
            import difflib
            known = sorted({d.name for d in CORPUS_ROOT.iterdir() if d.is_dir() and (d / "processed" / "chunks.db").exists()}) if CORPUS_ROOT.exists() else []
            for t in missing:
                close = difflib.get_close_matches(t, known, n=2, cutoff=0.6)
                if close:
                    print(f"  Did you mean: {', '.join(close)}?", file=sys.stderr)
        sys.exit(3)

    use_rerank = SEARCH_RERANK and max_chunks >= SEARCH_SMART_MIN_CHUNKS
    use_expansion = SEARCH_QUERY_EXPANSION and max_chunks >= SEARCH_SMART_MIN_CHUNKS

    if federated:
        print(f"Federated search across {len(available)} corpora: {', '.join(available)}")
    print(f"Query: {query}")
    try:
        print(f"Embedder: {get_label()}")
    except Exception as e:
        print(f"Embedder: UNAVAILABLE ({type(e).__name__}: {e})")
    # Always printed: the effective mode must be visible even when a per-query
    # flag overrides the env default (or fails to — see the REMAINDER bug).
    print(f"Captions: {captions}")
    if max_chunks < SEARCH_SMART_MIN_CHUNKS and (SEARCH_RERANK or SEARCH_QUERY_EXPANSION):
        print(f"(Smart mode: rerank/expansion skipped — largest corpus below {SEARCH_SMART_MIN_CHUNKS} chunks)")

    # Query expansion — one expansion call, applied uniformly to every corpus.
    # State goes to stdout: a dead expansion backend must be visible in the
    # search output, not buried on stderr.
    all_queries = [query]
    if use_expansion:
        try:
            from ..ollama_llm import expand_query
            alternatives = expand_query(query, n=3)
            if alternatives:
                all_queries.extend(alternatives)
                print(f"Query expansion: +{len(alternatives)} variants")
            else:
                print("Query expansion skipped: no variants returned")
        except Exception as e:
            print(f"Query expansion skipped: {type(e).__name__}: {e}")

    print(f"{'='*60}\n")

    # Per-corpus retrieval. Each list is sorted best-first by within-corpus
    # hybrid score and tagged with its corpus name.
    per_corpus: list[list[dict]] = []
    for t in available:
        per_corpus.append(_search_single_corpus(t, all_queries, top_k,
                                                captions=captions,
                                                per_source=per_source))

    combined = _rrf_merge(per_corpus)

    if not combined:
        print("No results found. Try a different query or check the corpus content.")
        return

    # For federation we pull 2x top_k candidates to give the reranker enough to
    # work with. Single-corpus is already sorted by hybrid score; rrf_score is
    # monotone in rank so the order is identical.
    candidates = combined[: top_k * 2]

    # Report the engine that ran. Never label hybrid order as reranked.
    if use_rerank and len(candidates) > 1:
        from ..ollama_llm import rerank_chunks, RerankUnavailable
        pool = len(candidates)
        try:
            candidates = rerank_chunks(query, candidates, model=SEARCH_RERANK_MODEL)
            engine = candidates[0].get("rerank_engine", "unknown") if candidates else "unknown"
            print(f"Rerank: {engine} over {pool} candidates")
            note = candidates[0].get("rerank_note") if candidates else None
            if note:
                print(f"  ({note})")
        except RerankUnavailable as e:
            print(f"Rerank UNAVAILABLE — results in hybrid order ({e})")
        except Exception as e:
            print(f"Rerank UNAVAILABLE — results in hybrid order ({type(e).__name__}: {e})")
        candidates = candidates[:top_k]
    else:
        candidates = candidates[:top_k]

    # CONTEXT.md injection — only meaningful for single-corpus searches. For
    # federation we'd be guessing which corpus's CONTEXT.md applies, so skip.
    if not federated:
        context_sections = _find_context_sections(available[0], query)
        if context_sections:
            print(f"--- CONTEXT.md Sections ---\n")
            for header, body in context_sections:
                print(f"### {header}")
                display = body[:800].strip()
                if len(body) > 800:
                    display += "\n..."
                print(f"{display}\n")
            print(f"{'─'*60}\n")

    # Emit results
    for i, r in enumerate(candidates, 1):
        score = r.get("rerank_score", r.get("rrf_score", r["score"]))
        hybrid_score = r["score"]
        vec_s = r["vec_score"]
        bm25_s = r["bm25_score"]
        source = r["source_path"]
        corpus_tag = r.get("corpus", "")
        content = r["content"]

        display = content[:500].strip()
        if len(content) > 500:
            display += "..."

        signals = []
        if vec_s > 0:
            signals.append("vector")
        if bm25_s > 0:
            signals.append("bm25")
        if "rerank_score" in r:
            signals.append(f"rerank={r['rerank_score']:.2f}")
        signal = "+".join(signals) if signals else "hybrid"

        # Provenance: chunk file, original source document, heading, and
        # whether this is body text or a figure-caption sidecar.
        meta = r.get("metadata") or {}
        heading = str(meta.get("heading") or "").strip()
        origin = str(meta.get("source_file") or meta.get("source_relpath") or "").strip()
        kind = "CAPTION" if content.startswith(SEARCH_CAPTION_PREFIX) else "BODY"

        print(f"### Result {i} — score: {score:.3f} ({signal})")
        src_line = f"**Source:** {source}"
        if origin and origin != source:
            src_line += f" ← {origin}"
        if heading:
            src_line += f" | {heading}"
        src_line += f" [{kind}]"
        if corpus_tag:
            src_line += f" [{corpus_tag}]"
        print(src_line)
        print(f"**Scores:** hybrid={hybrid_score:.3f} vec={vec_s:.3f} bm25={bm25_s:.3f}")
        print(f"\n{display}\n")
        print(f"{'─'*60}\n")

    print(f"Returned {len(candidates)} results")


def _find_context_sections(topic: str, query: str, max_sections: int = 2) -> list[tuple[str, str]]:
    """Search CONTEXT.md for sections relevant to the query.

    Returns list of (header, body) tuples for matching sections.
    """
    context_path = corpus_path(topic) / "processed" / "CONTEXT.md"
    if not context_path.exists():
        return []

    try:
        text = context_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    if not text.strip():
        return []

    header_pattern = re.compile(r'^(#{2,3}\s+.+)$', re.MULTILINE)
    parts = header_pattern.split(text)

    sections: list[tuple[str, str]] = []
    for j in range(len(parts)):
        if header_pattern.match(parts[j]):
            header = parts[j].strip().lstrip('#').strip()
            body = parts[j + 1] if j + 1 < len(parts) else ""
            if body.strip():
                sections.append((header, body.strip()))

    if not sections:
        return []

    query_words = set(query.lower().split())
    scored = []
    for header, body in sections:
        section_text = (header + " " + body[:500]).lower()
        matches = sum(1 for w in query_words if w in section_text and len(w) > 2)
        if matches > 0:
            scored.append((matches, header, body))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [(h, b) for _, h, b in scored[:max_sections]]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="search_hybrid",
        description="Hybrid BM25 + vector search over one or more corpora.",
    )
    p.add_argument("--corpora", help="Comma-separated list of corpus names to federate across.")
    p.add_argument("--all", dest="all_primary", action="store_true",
                   help=f"Search the primary corpus list: {', '.join(PRIMARY_CORPORA)}")
    p.add_argument("--top-k", dest="top_k", type=int, default=SEARCH_TOP_K,
                   help=f"Number of results to return (also controls rerank candidate pool size). Default: {SEARCH_TOP_K}.")
    p.add_argument("--no-captions", dest="captions_mode", action="store_const", const="exclude",
                   help="Exclude figure-caption sidecar chunks (body text only).")
    p.add_argument("--with-captions", dest="captions_mode", action="store_const", const="include",
                   help="Include caption sidecars (overrides SEARCH_EXCLUDE_CAPTIONS=1).")
    p.add_argument("--captions-only", dest="captions_mode", action="store_const", const="only",
                   help="Search caption sidecars only (figure content).")
    p.add_argument("--per-source", dest="per_source", type=int, default=None,
                   help=f"Max results per source file. Default: {SEARCH_PER_SOURCE_LIMIT}.")
    p.set_defaults(captions_mode=None)
    # nargs="*" + parse_intermixed_args (not REMAINDER): flags typed AFTER the
    # topic must still parse. REMAINDER silently folded them into the query
    # text, so `corpus search t --captions-only q` searched for the literal
    # string "--captions-only" with the env-default caption mode.
    p.add_argument("rest", nargs="*",
                   help="Either '<topic> <query...>' (single corpus, positional) or just '<query...>' when --corpora/--all is used.")
    return p


def _parse_argv(argv=None):
    """Parse argv accepting options before AND after the positional tokens."""
    return _build_parser().parse_intermixed_args(argv)


def main(argv=None):
    args = _parse_argv(argv)

    rest = list(args.rest)

    if args.all_primary:
        corpora = list(PRIMARY_CORPORA)
        query_tokens = rest
    elif args.corpora:
        corpora = [c.strip() for c in args.corpora.split(",") if c.strip()]
        query_tokens = rest
    else:
        # Original positional form: <topic> <query>
        if len(rest) < 2:
            print("Usage:", file=sys.stderr)
            print("  search_hybrid.py <topic> <query>", file=sys.stderr)
            print("  search_hybrid.py --corpora a,b,c <query>", file=sys.stderr)
            print("  search_hybrid.py --all <query>", file=sys.stderr)
            sys.exit(1)
        corpora = [rest[0]]
        query_tokens = rest[1:]

    if not query_tokens:
        print("Error: no query provided", file=sys.stderr)
        sys.exit(1)

    query = " ".join(query_tokens)
    captions = args.captions_mode or ("exclude" if SEARCH_EXCLUDE_CAPTIONS else "include")
    search(corpora, query, top_k=args.top_k, captions=captions,
           per_source=args.per_source)


if __name__ == "__main__":
    main()
