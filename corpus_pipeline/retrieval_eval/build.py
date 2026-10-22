"""Construct reproducible retrieval questions from indexed chunks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import unicodedata
from urllib.request import Request, urlopen

from ..config import corpus_path
from ..corpus_db import _FTS_COMMON_WORDS
from ..chunk_dates import chunk_ts
from .run import parent_mapping

WORD = re.compile(r"\b[\w]+(?:['’\-][\w]+)*\b", re.UNICODE)
THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _clean_chunk(text: str) -> str:
    return "".join(char for char in text if char in "\n\t" or unicodedata.category(char) != "Cc")


def llm_question(prompt: str) -> tuple[str, str | None]:
    """Ask for a question and its supporting sentence on separate lines."""
    model = os.environ.get("EVAL_LLM_MODEL", "qwen3:14b")
    base = os.environ.get("EVAL_LLM_URL", "http://127.0.0.1:11434/v1").rstrip("/")
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    payload = {"model": model, "temperature": 0.3, "stream": False,
               "messages": [{"role": "system", "content": "Return exactly two lines: the question, then its supporting sentence copied from the passage. No preamble. /no_think"},
                            {"role": "user", "content": prompt}]}
    headers = {"Content-Type": "application/json"}
    if key := os.environ.get("EVAL_LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    request = Request(url, data=json.dumps(payload).encode(), method="POST", headers=headers)
    with urlopen(request, timeout=120) as response:
        content = json.load(response)["choices"][0]["message"].get("content") or ""
    lines = [line.strip().strip('"') for line in THINK.sub("", content).splitlines() if line.strip()]
    questions = [line for line in lines if line.endswith("?")]
    if not questions:
        raise ValueError("eval LLM returned no question")
    question = re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", questions[0]).strip()
    if not question:
        raise ValueError("eval LLM returned an empty question")
    supporting = next((line for line in lines if line != questions[0] and not line.endswith("?")), None)
    return question, supporting


def _date(metadata: dict) -> str | None:
    return chunk_ts(metadata)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text)
            if len(WORD.findall(s)) >= 4]


def _phrase(text: str, rng: random.Random) -> tuple[str, str]:
    sentences = []
    for sentence in _sentences(text):
        if "http://" in sentence or "https://" in sentence or sentence.lstrip().startswith(("#", "*", "-", "`")):
            continue
        words = list(WORD.finditer(sentence))
        choices = []
        for size in range(4, 8):
            for start in range(len(words) - size + 1):
                selected = words[start:start + size]
                span = sentence[selected[0].start():selected[-1].end()]
                if re.search(r"[`\[\]<>]|https?://", span):
                    continue
                if all(word.group().lower() in _FTS_COMMON_WORDS for word in selected):
                    continue
                choices.append(span)
        if choices:
            sentences.append((sentence, choices))
    if not sentences:
        raise ValueError("chunk has no valid phrase span")
    sentence, choices = rng.choice(sentences)
    return rng.choice(choices), sentence


def _regressions(path: Path, corpus: str, rows_by_id: dict[int, dict],
                 question_corpus: str | None = None) -> list[dict]:
    question_corpus = question_corpus or corpus
    records = []
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            item = json.loads(line)
            question = item.get("question", item.get("query"))
            chunk_id = int(item["chunk_id"])
            target_corpus = item.get("corpus", corpus)
        else:
            parts = re.split(r"\t|\s+->\s+", line, maxsplit=1)
            if len(parts) != 2:
                raise ValueError(f"regression line {line_no}: use JSONL, TAB, or 'query -> id'")
            question, chunk_id = parts
            if question.isdigit():
                question, chunk_id = chunk_id, question
            chunk_id = int(chunk_id)
            target_corpus = corpus
        if not question:
            raise ValueError(f"regression line {line_no}: empty question")
        row = rows_by_id.get(chunk_id) if target_corpus in (corpus, question_corpus) else None
        if row is None:
            raise ValueError(f"regression line {line_no}: chunk {chunk_id} is not in {corpus}")
        records.append({"qid": _qid(question_corpus, chunk_id, "regression", question),
                        "corpus": question_corpus, "chunk_id": chunk_id,
                        "source_path": row["source_path"] if row else None,
                        "chunk_index": row["chunk_index"] if row else None,
                        "kind": "regression", "question": question,
                        "answer_span": None, "chunk_date": row["chunk_date"] if row else None,
                        "chunk_len": row["chunk_len"] if row else None,
                        "generator": "span"})
    return records


def _qid(corpus: str, chunk_id: int, kind: str, question: str) -> str:
    return hashlib.sha256(f"{corpus}\0{chunk_id}\0{kind}\0{question}".encode()).hexdigest()[:16]


def build_questions(corpus: str, n: int = 120, seed: int = 7,
                    db_path: Path | None = None, llm_client=None,
                    append_regression: Path | None = None) -> tuple[list[dict], list[str]]:
    if n <= 0:
        raise ValueError("--n must be positive")
    question_corpus = parent_mapping([corpus])[corpus]
    db_path = db_path or corpus_path(corpus) / "processed" / "chunks.db"
    llm_client = llm_client or llm_question
    rng = random.Random(seed)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT id, source_path, chunk_index, length(CAST(content AS BLOB)) AS chunk_len, "
            "metadata_json FROM chunks")]
    finally:
        conn.close()
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (ValueError, TypeError):
            metadata = {}
        row["chunk_date"] = _date(metadata if isinstance(metadata, dict) else {})
    by_id = {row["id"]: row for row in rows}
    by_source = {}
    for row in rows:
        if row["chunk_len"] >= 300:
            by_source.setdefault(row["source_path"], []).append(row)
    notes = []
    if n > len(by_source):
        notes.append(f"requested {n} questions; only {len(by_source)} eligible sources")
        n = len(by_source)
    if not n:
        raise ValueError(f"{corpus} has no chunks of at least 300 characters")
    counts = {"whole": round(n * .40), "late": round(n * .20),
              "phrase": round(n * .25)}
    counts["temporal"] = n - sum(counts.values())
    # Sample source names uniformly, then one eligible chunk per source.
    sources = rng.sample(list(by_source), n)
    dated_sources = [source for source in sources if any(row["chunk_date"] for row in by_source[source])]
    if len(dated_sources) < counts["temporal"]:
        extras = [source for source in by_source if source not in sources
                  and any(row["chunk_date"] for row in by_source[source])]
        rng.shuffle(extras)
        for source in extras[:counts["temporal"] - len(dated_sources)]:
            replaced = next((s for s in reversed(sources) if s not in dated_sources), None)
            if replaced is None:
                break
            sources[sources.index(replaced)] = source
            dated_sources.append(source)
    temporal_sources = set(rng.sample(dated_sources, min(counts["temporal"], len(dated_sources))))
    temporal = [(source, rng.choice([row for row in by_source[source] if row["chunk_date"]]))
                for source in sources if source in temporal_sources]
    if len(temporal) < counts["temporal"]:
        notes.append(f"temporal skipped: {counts['temporal'] - len(temporal)} questions lack dated chunks")
    remainder = [(source, rng.choice(by_source[source])) for source in sources
                 if source not in temporal_sources]
    rng.shuffle(remainder)
    assignments = [("temporal", row) for _, row in temporal]
    for kind in ("whole", "late", "phrase"):
        assignments.extend((kind, row) for _, row in (remainder.pop() for _ in range(counts[kind])))
    rng.shuffle(assignments)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for _, row in assignments:
            row["content"] = conn.execute("SELECT content FROM chunks WHERE id = ?", (row["id"],)).fetchone()[0]
            row["chunk_len"] = len(row["content"])
    finally:
        conn.close()
    records = []
    skipped = 0
    used_ids = {row["id"] for _, row in assignments}
    selected_sources = {row["source_path"] for _, row in assignments}
    for kind, row in assignments:
        while True:
            try:
                record = _build_record(question_corpus, kind, row, rng, llm_client, notes)
                records.append(record)
                break
            except Exception as error:
                skipped += 1
                detail = " ".join(str(error).split())
                notes.append(f"chunk {row['id']}: {type(error).__name__}: {detail}; skipped")
                replacements = [candidate for candidate in by_source[row["source_path"]]
                                if candidate["id"] not in used_ids]
                if not replacements:
                    replacements = [candidate for source, candidates in by_source.items()
                                    if source not in selected_sources for candidate in candidates
                                    if candidate["id"] not in used_ids]
                if not replacements:
                    break
                row = rng.choice(replacements)
                used_ids.add(row["id"])
                selected_sources.add(row["source_path"])
                with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as replacement_conn:
                    row["content"] = replacement_conn.execute(
                        "SELECT content FROM chunks WHERE id = ?", (row["id"],)).fetchone()[0]
                row["chunk_len"] = len(row["content"])
    notes.append(f"skipped chunks: {skipped}")
    if append_regression:
        regressions = _regressions(append_regression, corpus, by_id, question_corpus)
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as regression_conn:
            for record in regressions:
                content = regression_conn.execute("SELECT content FROM chunks WHERE id = ?",
                                                  (record["chunk_id"],)).fetchone()[0]
                sentences = _sentences(content)
                record["answer_span"] = (record["question"] if record["question"].casefold() in content.casefold()
                                         else sentences[0] if sentences else content.strip())
        records.extend(regressions)
    return records, notes


def _build_record(corpus: str, kind: str, row: dict, rng: random.Random,
                  llm_client, notes: list[str]) -> dict:
    content = row["content"]
    target = _clean_chunk(content[len(content) * 2 // 3:] if kind == "late" else content)[:6000]
    if kind == "phrase":
        try:
            question, answer = _phrase(content, rng)
            generator = "span"
        except ValueError:
            notes.append(f"chunk {row['id']}: no valid phrase; generated a whole question")
            kind = "whole"
    if kind != "phrase":
        sentences = _sentences(target)
        answer = rng.choice(sentences) if sentences else target.strip()
        instruction = ("Begin exactly with 'When did' and ask when this event happened"
                       if kind == "temporal" else "Ask what happened or what someone said")
        prompt = (f"{instruction}. Write one natural question a reader might ask. "
                  "Do not copy the answer sentence or quote the passage verbatim. "
                  "Do not mention the index or chunk. Return question then supporting sentence on two lines.\n"
                  f"Answer sentence: {answer}\nPassage: {target[:max(0, 6000 - len(answer))]}")
        for attempt in range(2):
            try:
                response = llm_client(prompt)
                question, support = response if isinstance(response, tuple) else (response, None)
                if not isinstance(question, str) or not question.strip().endswith("?"):
                    raise ValueError("eval LLM returned no question")
                break
            except Exception:
                if attempt:
                    raise
        if support and " ".join(support.split()).casefold() in " ".join(target.split()).casefold():
            answer = support
        generator = os.environ.get("EVAL_LLM_MODEL", "qwen3:14b")
    return {"qid": _qid(corpus, row["id"], kind, question),
            "corpus": corpus, "chunk_id": row["id"],
            "source_path": row["source_path"],
            "chunk_index": row["chunk_index"], "kind": kind,
            "question": question, "answer_span": answer,
            "chunk_date": row["chunk_date"], "chunk_len": row["chunk_len"],
            "generator": generator}
