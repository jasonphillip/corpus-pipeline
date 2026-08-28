"""Ollama LLM helpers for scoring, summarization, and triage."""

import json
import os
import re

import httpx

from .config import (
    OLLAMA_GENERATE_URL, MODEL_SCORING, MODEL_TRIAGE,
    LLM_CHAT_URL, LLM_CHAT_MODEL, LLM_CHAT_API_KEY,
)

# Ollama's server-side default context (~4K) silently truncates long RAG prompts;
# pin a real window per request (server env OLLAMA_CONTEXT_LENGTH is belt-and-braces).
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "32768"))


class RerankUnavailable(RuntimeError):
    """No rerank engine produced a real ordering — results stay in hybrid order.

    Raised instead of fabricating rerank_score from the hybrid score, which is
    what made dead rerank services invisible to callers and users."""


def generate(prompt: str, model: str = MODEL_SCORING, temperature: float = 0.3,
             max_tokens: int = 2048, timeout: float = 60.0) -> str:
    """Generate text through the configured chat endpoint or Ollama."""
    if LLM_CHAT_URL:
        payload = {
            "model": LLM_CHAT_MODEL or model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            # vLLM extension — keeps reasoning models from thinking through
            # utility calls (expansion, scoring). Retried without on a 400.
            "chat_template_kwargs": {"thinking": False},
        }
        headers = ({"Authorization": f"Bearer {LLM_CHAT_API_KEY}"}
                   if LLM_CHAT_API_KEY else {})
        r = httpx.post(LLM_CHAT_URL, headers=headers, json=payload, timeout=timeout)
        if r.status_code == 400:
            payload.pop("chat_template_kwargs", None)
            r = httpx.post(
                LLM_CHAT_URL, headers=headers, json=payload, timeout=timeout
            )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"].get("content") or ""
        return _strip_think_tags(text)
    r = httpx.post(
        OLLAMA_GENERATE_URL,
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens,
                        "num_ctx": OLLAMA_NUM_CTX},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    text = r.json().get("response", "")
    return _strip_think_tags(text)


def generate_json(prompt: str, model: str = MODEL_SCORING,
                  temperature: float = 0.1, timeout: float = 60.0) -> dict | list:
    """Generate and parse JSON from Ollama."""
    text = generate(prompt, model=model, temperature=temperature, timeout=timeout)
    return _parse_json(text)


def score_source(url: str, source_type: str, topic: str) -> dict:
    """Score a source URL for relevance to a topic using local model."""
    prompt = f"""Rate this documentation source for the topic "{topic}".
URL: {url}
Type: {source_type}

Return JSON with:
- quality_score: float 0.0-1.0 (how useful for building a reference corpus)
- rationale: string (1 sentence why)
- estimated_pages: int (rough estimate)

JSON only, no other text: /no_think"""

    try:
        result = generate_json(prompt, model=MODEL_SCORING)
        return {
            "quality_score": float(result.get("quality_score", 0.5)),
            "quality_rationale": str(result.get("rationale", "")),
            "estimated_pages": int(result.get("estimated_pages", 10)),
        }
    except Exception as e:
        import sys
        print(f"Warning: score_source failed for {url}: {type(e).__name__}: {e}", file=sys.stderr)
        return {"quality_score": 0.5, "quality_rationale": f"scoring failed: {type(e).__name__}", "estimated_pages": 10}


def summarize_corpus(topic: str, file_list: list[str]) -> str:
    """Generate a corpus summary from file listing."""
    files_str = "\n".join(f"- {f}" for f in file_list[:50])
    prompt = f"""You are summarizing a documentation corpus about "{topic}".

Files in this corpus:
{files_str}
{"(... and more)" if len(file_list) > 50 else ""}

Write a 2-3 paragraph summary covering:
1. What this topic is about
2. What documentation is available in this corpus
3. Key areas covered

Be concise and factual:"""

    return generate(prompt, model=MODEL_SCORING)


def _rerank_cross_encoder(query: str, chunks: list[dict], timeout: float = 30.0):
    """Rerank via the llama.cpp cross-encoder endpoint (RERANK_URL).

    Returns (scored_chunks, None) on success — each chunk carries rerank_score
    and rerank_engine="cross-encoder" — or (None, reason) on any failure so the
    caller can fall through to the LLM prompt path and report why."""
    from .config import RERANK_URL
    try:
        docs = [c.get("content", "")[:2000] for c in chunks]
        r = httpx.post(RERANK_URL, json={"query": query, "documents": docs}, timeout=timeout)
        if r.status_code == 500 and "too large" in r.text:
            # llama-server rejects (query+doc) pairs over its physical batch
            # size (-ub, default 512 tokens). Retry once with a cap that fits
            # a default-configured server (~300-400 tokens per doc).
            docs = [d[:1200] for d in docs]
            r = httpx.post(RERANK_URL, json={"query": query, "documents": docs}, timeout=timeout)
        r.raise_for_status()
        results = r.json().get("results")
        if not isinstance(results, list) or not results:
            return None, f"empty results from {RERANK_URL}"
        scored = [dict(c) for c in chunks]
        for c in scored:
            c["rerank_score"] = 0.0
            c["rerank_engine"] = "cross-encoder"
        for item in results:
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(scored):
                scored[idx]["rerank_score"] = float(item.get("relevance_score", 0.0))
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def rerank_chunks(query: str, chunks: list[dict], model: str = MODEL_SCORING,
                  timeout: float = 60.0) -> list[dict]:
    """Rerank search result chunks by relevance to a query.

    Engine order: local cross-encoder (RERANK_URL) first, then the LLM batch
    prompt path. Every returned chunk carries rerank_score and rerank_engine
    naming the engine that produced the ordering.

    Raises RerankUnavailable when NEITHER engine worked — callers must fall
    back to hybrid order and say so in their output.
    """
    if not chunks:
        return chunks

    # Prefer the local cross-encoder reranker (Qwen3-Reranker-0.6B via llama.cpp
    # --rerank): far better ordering than prompt-scoring and ~1s for 60 candidates.
    cross, cross_err = _rerank_cross_encoder(query, chunks)
    if cross is not None:
        return cross

    # Build batch prompt with all chunks (cap each at 800 chars)
    chunk_lines = []
    for i, chunk in enumerate(chunks):
        content = chunk.get("content", "")[:800].replace("\n", " ")
        chunk_lines.append(f"Chunk {i+1}: {content}")
    chunks_text = "\n\n".join(chunk_lines)

    prompt = (
        f"Rate the relevance of each text chunk to the query. Score each from 0.0 to 1.0.\n"
        f"Query: {query}\n\n"
        f"{chunks_text}\n\n"
        f"Return ONLY a JSON array of {len(chunks)} scores in order, "
        f'e.g. [0.8, 0.3, 0.9, ...] /no_think'
    )

    try:
        scores = generate_json(prompt, model=model, temperature=0.0, timeout=timeout)
        if not isinstance(scores, list) or len(scores) != len(chunks):
            raise ValueError(f"Expected {len(chunks)} scores, got {scores}")
        scored = []
        for chunk, s in zip(chunks, scores):
            score = max(0.0, min(1.0, float(s)))
            scored.append({**chunk, "rerank_score": score,
                           "rerank_engine": f"llm:{model}",
                           "rerank_note": f"cross-encoder unavailable ({cross_err})"})
    except Exception as e:
        raise RerankUnavailable(
            f"cross-encoder unavailable ({cross_err}); "
            f"LLM fallback failed ({type(e).__name__}: {e})"
        ) from e

    scored.sort(key=lambda x: x["rerank_score"], reverse=True)
    return scored


def expand_query(query: str, model: str = MODEL_TRIAGE, n: int = 3,
                 timeout: float = 15.0) -> list[str]:
    """Generate alternative phrasings of a search query for query expansion.

    Uses the configured triage model by default.
    Returns a list of alternative queries (not including the original).

    Backend failures PROPAGATE (no silent [] — the caller prints the skip so a
    dead expansion backend is visible in the search output).
    """
    prompt = (
        f'Generate {n} alternative search queries for the following query. '
        f'Use different words and phrasings that would find the same information.\n\n'
        f'Original query: {query}\n\n'
        f'Return ONLY a JSON array of strings, e.g. ["query1", "query2", "query3"] /no_think'
    )
    result = generate_json(prompt, model=model, temperature=0.3, timeout=timeout)
    if isinstance(result, list):
        return [str(q) for q in result[:n]]
    return []


def triage_chunk(content: str, topic: str) -> dict:
    """Fast triage of a chunk for metadata using the small model."""
    prompt = f"""Classify this text chunk from a "{topic}" corpus.
Return JSON with: section_type (one of: overview, api_reference, tutorial, example, configuration, troubleshooting, other), topic_tags (list of 1-3 keywords).

Text (first 500 chars): {content[:500]}

JSON only:"""

    try:
        return generate_json(prompt, model=MODEL_TRIAGE, timeout=15.0)
    except Exception:
        return {"section_type": "other", "topic_tags": [topic]}


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from reasoning model output."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _parse_json(text: str) -> dict | list:
    """Extract JSON from potentially noisy model output."""
    # Try direct parse
    text = text.strip()
    text = re.sub(
        r"^[ \t]*```(?:[\w+-]+)?[ \t]*(?:\r?\n|$)",
        "", text, flags=re.MULTILINE,
    ).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try balanced JSON containers in appearance order
    pairs = {"[": "]", "{": "}"}
    for start, opener in enumerate(text):
        if opener not in pairs:
            continue

        stack = []
        in_string = False
        escaped = False
        for end in range(start, len(text)):
            char = text[end]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char in pairs:
                stack.append(char)
            elif char in "]}":
                if not stack or pairs[stack[-1]] != char:
                    break
                stack.pop()
                if not stack:
                    try:
                        return json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break

    # Salvage complete elements from an unterminated top-level array.
    lb = text.find("[")
    if lb != -1:
        decoder = json.JSONDecoder()
        items, i = [], lb + 1
        while i < len(text):
            while i < len(text) and text[i] in " \t\r\n,":
                i += 1
            if i >= len(text) or text[i] == "]":
                break
            try:
                obj, end = decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                break
            items.append(obj)
            i = end
        if items:
            return items

    raise ValueError(f"Could not parse JSON from: {text[:200]}")
