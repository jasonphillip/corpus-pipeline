# corpus-pipeline

This project helps one person index their own documents for search on one machine with local models.

## What this is, in one verifiable line

This repo turns a directory of documents into a local hybrid-search corpus in one SQLite file, combining FTS5 keyword scores with sqlite-vec vector scores and an optional local reranker.

The default mechanism is normalized weighted fusion of the two candidate lists.

Verify the mechanics with `python3 -m pytest tests/ -q`, then try the quickstart on `examples/docs/`.

The canonical source is https://github.com/jasonphillip/corpus-pipeline

Extracted from a private working tree on 2026-08-28.

## What works today and limits

The pipeline acquires, chunks, and indexes documents. Search combines FTS5 keyword results with cosine vectors. Optional local models expand queries and rerank results.
Deduplication stays within each source. Deleting a raw file does not remove its indexed chunks.
Legacy L2 vector indexes need the [documented rebuild](#rebuilding-a-legacy-l2-index).
The BM25 keyword leg uses OR matching with a small common-word filter. Strict all-terms matching remains available via `search.fts_match: all`.
Transcript chunking, metadata filters, RRF, conversation caps, and passage display
are opt-in. Query embedding now waits up to 120 seconds by default. A passage
window is a snippet, not the full message.
[Retrieval evaluation](#retrieval-evaluation) records the earlier AND behavior.

## Quickstart

Question: **How does hybrid retrieval combine BM25 and cosine distance?**
The answer is in `examples/docs/2024-06-retrieval.md`.
Run these commands from the repository root after starting the embedding service described below:

```bash
python3 -m venv "$PWD/.venv"
. "$PWD/.venv/bin/activate"
python3 -m pip install -e "$PWD"
cp "$PWD/corpus.example.yml" "$PWD/corpus.yml"
export CORPUS_CONFIG="$PWD/corpus.yml"
export CORPUS_ROOT="$(mktemp -d)"
export CORPUS_EMBED_PROVIDER=openai
export CORPUS_EMBED_URL=http://localhost:8000/v1/embeddings
export CORPUS_OPENAI_EMBED_MODEL=Qwen3-Embedding-4B
export CORPUS_OPENAI_EMBED_DIMS=2560
mkdir -p "$CORPUS_ROOT/my-docs/raw/example"
cp "$PWD"/examples/docs/*.md "$CORPUS_ROOT/my-docs/raw/example/"
corpus process my-docs rag
corpus search my-docs --top-k 3 "How does hybrid retrieval combine BM25 and cosine distance?"
```

The expected top source is `example/2024-06-retrieval.md`.
Its text explains weighted fusion of FTS5 BM25 and cosine vector results.
Scores vary by model and build. This three-file corpus does not establish retrieval quality.
The corpus is below the default 500-chunk smart-mode threshold, so Ollama reranking does not run.
`examples/quickstart.txt` repeats the commands and shows abbreviated output.

### CPU fixture run

The test suite uses a fake embedder to exercise indexing, search, update, rollback, and rerun mechanics.
Its CPU results say nothing about retrieval quality with a real embedding model.

## Measured result

The [retrieval evaluation](#retrieval-evaluation) used 30 queries and 45 synthetic documents with a local embedding model.
Its [measured results](eval/results/2026-10-12-833796a.md) show full vector recall on this easy set.
The new processing and search options are opt-in and were not covered by that evaluation.

## Design decision

Each corpus uses one SQLite file with FTS5, sqlite-vec, and weighted fusion.
This serves a single-box setup with no service beyond the embedder. The tradeoff is a small corpus per file and no distributed index.

## How this was built

I built this with LLM coding agents, Claude Code and Codex, under my direction.
I set the requirements, chose the SQLite design and local-model constraint, reviewed every change, and set the acceptance criteria.
The agents wrote most of the code and tests.

## Pipeline

```text
research     discovered URLs -> manifest.json
acquire      Git docs + fetched pages -> raw/
process      clean -> chunk -> embed -> SQLite FTS5 + sqlite-vec
search       BM25 + vector candidates -> fusion -> optional local rerank
first        dated metadata -> earliest matching sources
refresh      raw-file snapshots -> added, removed, and modified files
health       schema, row, vector, dimension, and metadata checks
```

The `research` command does not search the web. It converts a reviewed JSON source
list into a stable manifest. Use your preferred discovery method, then pass its URLs
to the command.

## Architecture

```text
manifest.json
     |
     v
raw source files --> cleaner --> section chunker --> embedding service
                                      |                    |
                                      +--------+-----------+
                                               v
                                   processed/chunks.db
                                   + chunks source records
                                   + chunks_fts FTS5 index
                                   + chunks_vec vector index
                                               |
                         +---------------------+------------------+
                         v                                        v
                  BM25 candidates                         vector candidates
                         +---------------------+------------------+
                                               v
                                  normalized weighted fusion
                                               |
                                  optional local reranking
```

Each corpus is independent. It has one manifest, raw tree, metadata directory, and
SQLite index. The source-state table makes processing resumable. The chunk identifier
links the primary chunk row to its FTS5 and sqlite-vec rows.

## Requirements

- Python 3.11 or newer.
- Python's SQLite build with FTS5 enabled.
- `sqlite-vec` for the loadable vector extension.
- A local embedding service. The default is an OpenAI-compatible endpoint at
  `http://localhost:8000/v1/embeddings`.
- The OpenAI-compatible embedding endpoint must accept `model` and `input`. It must
  return `data[].embedding` and `data[].index` for batches.
- Ollama at `http://localhost:11434` is the default fallback embedder. It is also used
  for summaries, query expansion, and LLM reranking.
- Git is required only for repository acquisition.
- Internet access is required only when you run acquisition against remote URLs.
- `tree-sitter-language-pack` is optional for callers of the code-chunking API.
- Universal Ctags is optional. It enables symbol and cross-reference tables for code.

The configured embedding dimension becomes part of each sqlite-vec table. The
pipeline probes live vectors and rejects mismatched insertions. Do not change the
embedding model for an existing corpus without rebuilding or migrating its index.
The vector table uses cosine distance; corpora built before this change use L2 and must be rebuilt.

## Rebuilding a legacy L2 index

Stop other writers, use the configured embedding model, and run:

```bash
python3 -m corpus_pipeline reembed my-docs --rebuild-vectors
python3 -m corpus_pipeline health --scope all --no-persist
```

The command prints a timestamped `chunks.db.backup-*.db` path beside the index.
If a rebuild is interrupted, close corpus processes and restore that backup:

```bash
python3 -c 'import sqlite3,sys; source=sqlite3.connect(sys.argv[1]); target=sqlite3.connect(sys.argv[2]); source.backup(target); source.close(); target.close()' \
  "$HOME/.corpus/my-docs/processed/chunks.db.backup-<timestamp>.db" \
  "$HOME/.corpus/my-docs/processed/chunks.db"
```

## Embedding and reranking services

The local embedding deployment records llama.cpp v0.6.0 (commit `d812350`) and
`Qwen3-Embedding-4B-Q8_0.gguf`. Give `llama-server` the model file on your machine:

```bash
export MODEL_GGUF=/absolute/path/to/Qwen3-Embedding-4B-Q8_0.gguf
llama-server --version
llama-server -m "$MODEL_GGUF" --host 127.0.0.1 --port 8000 --embeddings --pooling last --ctx-size 8192
```

Run the last command in a separate terminal before the quickstart.
This model returns 2,560 dimensions. Set `CORPUS_OPENAI_EMBED_DIMS=2560` and
`CORPUS_OPENAI_EMBED_MODEL=Qwen3-Embedding-4B` when using it.
The client calls `http://localhost:8000/v1/embeddings` with `model` and `input`.
An existing index cannot change embedding model without rebuilding its vectors.

Ollama is optional for query expansion and LLM reranking. The local client checked
for this example is Ollama 0.40.0. To provide the configured model names:

```bash
ollama --version
ollama pull qwen3:8b
ollama pull qwen3:14b
ollama serve
```

Run `ollama serve` in a separate terminal unless Ollama is already serving at
`http://localhost:11434`. The defaults use `qwen3:8b` for expansion and
`qwen3:14b` for LLM reranking. The search path enables both only when the
largest searched corpus has at least 500 chunks. A configured `/v1/rerank`
cross-encoder takes precedence over Ollama reranking.

## Retrieval upgrades (2026-10)

- Email-thread cleaning now keeps dense prose and Markdown anchor text, falls back for unparsed files, and can be checked without indexing with `python scripts/chunk_coverage.py <corpus> --mode email_thread`; eval build writes the parent corpus name for shadow corpora.
- Rerank window default 2000 characters (was 4000): same MRR within the confidence interval on two 120-question sets, 16-35 % lower median search latency; the candidate pool stays 2 x top_k because a pool of 30 cost about 0.02 source MRR.

### Canonical dates

The chunk date uses `ts`, `recording_start`, `first`, `started_at`, `date`,
`created_at`, `content_date`, `period`, then `original_mtime`, in that order.
First-mention, health ranges, recency, and date filters use the same date.

```bash
corpus first-mention my-docs "amber relay"
corpus health --scope all --no-persist
```

### Search

Search adds a phrase channel for the whole query and each quoted segment;
`SEARCH_FTS_PHRASE=1`, `SEARCH_PHRASE_BONUS=0.5`, and
`SEARCH_PHRASE_RRF_WEIGHT=3.0` control the channel and its linear and RRF tiers.
`CORPUS_SEARCH_FUSION=rrf` selects RRF, and `CORPUS_RECENCY_WEIGHT=0.15`
ranks dated hits; `--since` and `--until`
include the full year, month, or day and exclude undated chunks.
`CORPUS_RERANK_DOC_CHARS=2000` selects the best query window, and
`CORPUS_RERANK_BATCH_TOKENS=4096` bounds the reranker payload.
Set `search.rerank: true` in a manifest to request reranking when any searched corpus opts in; the size threshold still applies. Explicit `SEARCH_RERANK=1|0` wins over manifests, and the existing `CORPUS_SEARCH_RERANK` environment variable remains supported. `corpus eval run --search-arg rerank=true|false` overrides one evaluation run.

```bash
export CORPUS_EMBED_URL=http://127.0.0.1:8000/v1/embeddings
export CORPUS_RERANK_URL=http://127.0.0.1:8003/v1/rerank
corpus search my-docs --since 2025-01 --until 2025-12 '"amber relay"'
```

### Structured chunking

Set `chunking.mode` to `generic`, `transcript`, `email_thread`,
`chat_sessions`, or `document`; `chunking.chunk_chars`,
`chunking.overlap_chars`, and `chunking.prefix` tune each profile.
Structured chunks carry a prefix and `prefix_len`, plus per-chunk `ts` and
`conversation_id` when the source provides them; the YAML or JSON mapping
contains corpus names, with no names built into the script.
Structured modes remove boilerplate and low-density body text before packing, while the final noise gate preserves the prefix.
Email mode also removes HTML and CSS noise; bodies under 40 alphanumeric characters retain only the message header.
Transcript mode keeps each `## Prompt` with its `## Response` when it fits, records `pair_index`, and repeats a `Prompt:` header on oversized pieces. Document mode defaults to 2500 characters with 250 overlap, splits at H1-H3 outside fenced code, and stores `heading_path` beside a `Section:` prefix. LongMemEval motivates keeping rounds together (https://arxiv.org/abs/2410.10813); Snowflake motivates section-aware splits (https://www.snowflake.com/en/blog/engineering/impact-retrieval-chunking-finance-rag/).

```bash
python3 scripts/apply_corpus_profiles.py apply --mapping examples/profiles.yml --dry-run
corpus profiles apply --mapping examples/profiles.yml
corpus shadow my-docs candidate --mapping examples/profiles.yml
```

### Retrieval evaluation

The context writer now stores a situating sentence and a newline-separated `Entities:` line, capped together at 500 characters. Its prompt version is `context-v2-entities`; the LongMemEval paper above discusses fact-augmented retrieval keys, but this repo has no measured gain for this change.

`corpus eval build` generates synthetic whole, late, phrase, and temporal
questions through `EVAL_LLM_URL=http://127.0.0.1:11434/v1` and
`EVAL_LLM_MODEL=qwen3:14b`; set `EVAL_LLM_API_KEY` if the endpoint needs a bearer token.
`corpus eval run` reports strict, source, and answer-span Recall@k, MRR, and
nDCG, with `--since` and `--until` passed to retrieval.
`corpus eval compare` uses a paired bootstrap on shared question IDs; use
`--append-regression /tmp/regressions.jsonl` on build for known queries.
For shadow indexes named `<parent>--<suffix>`, eval scores hits against `<parent>`; repeat `--alias shadow=parent` for other names, and each run records the mapping.

```bash
corpus eval build --corpus my-docs --n 120 --seed 7 --out /tmp/my-docs-questions.jsonl
corpus eval run --questions /tmp/my-docs-questions.jsonl --label baseline --out /tmp/my-docs-baseline.json
corpus eval run --questions /tmp/my-docs-questions.jsonl --label candidate --search-arg fusion=rrf --out /tmp/my-docs-candidate.json
corpus eval compare /tmp/my-docs-baseline.json /tmp/my-docs-candidate.json
```

### Contextual retrieval

`corpus context write` stores hash-keyed context in `chunk_context` through
`CONTEXT_LLM_URL=http://127.0.0.1:11434/v1` and `CONTEXT_LLM_MODEL=qwen3:14b`;
set `CONTEXT_LLM_API_KEY` for a bearer token.
`corpus context apply` re-embeds context plus content and adds `context_fts`
to BM25 token and phrase search; `SEARCH_CONTEXT_FTS=0` disables that channel.
`status` and `prune` inspect and clean the sidecar, and the example nightly
script uses `CORPUS_CONTEXT_TOPICS` to select corpora without a host-specific unit.

```bash
corpus context write --corpus my-docs --limit 100
corpus context apply --corpus my-docs
corpus context status --corpus my-docs
corpus context prune --corpus my-docs
CORPUS_CONTEXT_TOPICS=my-docs scripts/context_nightly.sh
```

### Measured on private data

On a 120-question synthetic set over a 126k-chunk personal email index, the
exact-phrase tier lifted phrase-query Recall@1 from 0.03 to 0.37 and overall
MRR from 0.26 to 0.35 without a reranker.
These are the author's private data, synthetic questions, one run.

## Retrieval evaluation

The [evaluation method](eval/README.md) checks passages and compares document-level retrieval modes.
The [2026-10-12 rerun](eval/results/2026-10-12-4117b89.md) at git revision `4117b89` used OR matching with a common-word filter.
It used Qwen3-Embedding-4B at 2560 dimensions through llama.cpp and Ollama `qwen3:14b` for reranking.
The run used 30 eval queries and 45 synthetic documents of one chunk each. Building the index took 5.9 seconds.

| Mode | Recall@5 | Recall@10 | nDCG@10 | p50 ms | p95 ms | Queries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | 0.950 | 0.967 | 0.840 | 0.49 | 0.75 | 30 |
| vector | 1.000 | 1.000 | 0.936 | 39.42 | 43.74 | 30 |
| hybrid | 1.000 | 1.000 | 0.987 | 43.16 | 45.75 | 30 |
| hybrid_rerank | 1.000 | 1.000 | 0.992 | 964.79 | 1008.89 | 30 |

The [first run](eval/results/2026-10-12-833796a.md) at git revision `833796a` used an AND query with the same queries, documents, and services.
Building its index took 8.6 seconds.

| Mode | Recall@5 | Recall@10 | nDCG@10 | p50 ms | p95 ms | Queries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | 0.250 | 0.250 | 0.254 | 0.24 | 0.35 | 30 |
| vector | 1.000 | 1.000 | 0.936 | 43.39 | 45.98 | 30 |
| hybrid | 1.000 | 1.000 | 0.985 | 43.11 | 46.03 | 30 |
| hybrid_rerank | 1.000 | 1.000 | 0.992 | 1744.28 | 3106.54 | 30 |

In the first run, `_build_fts_query` joined every token with AND, including "how", "does", and "the".
A natural question such as "How does the admin API authenticate requests?" matched no chunk, while a keyword query such as "lsctl purge older-than" did.
The keyword leg now uses OR matching with a small common-word filter. Strict all-terms matching remains available via `search.fts_match: all`.
Mean Recall@5 for BM25 alone rose from 0.250 to 0.950. Hybrid nDCG@10 moved from 0.985 to 0.987.
Vector and hybrid recall every judged document on this set. Reranking adds 0.007 nDCG@10 at about 40 times the p50 latency.
The rerank step's latency halved between runs because of a warm Ollama cache. That cause is outside the query change, so the latency columns are not comparable between the two tables.

A [2026-10-16 rerun](eval/results/2026-10-16-10ef530.md) at git revision `10ef530`, after the opt-in transcript, filter and search-setting options landed, reproduced every Recall and nDCG value of the 2026-10-12 rerun with default settings.
Its hybrid p50 fell to 2.33 ms because query vectors are now cached per process and the vector mode embeds the same queries first, so that figure excludes the embedding call.

Run the evaluation from the repository root with the same local services.

```bash
CORPUS_EMBED_PROVIDER=openai CORPUS_EMBED_URL=http://localhost:8000/v1/embeddings CORPUS_OPENAI_EMBED_MODEL=Qwen3-Embedding-4B CORPUS_OPENAI_EMBED_DIMS=2560 CORPUS_TEST_OLLAMA_URL=http://localhost:11434 CORPUS_TRIAGE_MODEL=qwen3:14b CORPUS_SCORING_MODEL=qwen3:14b python3 eval/run_eval.py
```

The corpus and judgments are synthetic and self-authored. These numbers show the mechanics on an easy set and are not a benchmark.

## Tests

Run the same CPU command as CI:

```bash
python3 -m pip install -e . && python3 -m pip install 'pytest>=8,<10' && python3 -m pytest tests/ -q
```

The two service tests skip with `CORPUS_TEST_EMBED_URL is not set` when no endpoint is selected.
To enable embedding and optional Ollama coverage, use their base URLs:

```bash
CORPUS_TEST_EMBED_URL=http://localhost:8000/v1 python3 -m pytest tests/test_services.py -q
CORPUS_TEST_EMBED_URL=http://localhost:8000/v1 CORPUS_TEST_OLLAMA_URL=http://localhost:11434 python3 -m pytest tests/test_services.py -q
```

The Ollama test forces query expansion on this small corpus.
It uses `qwen3:14b` for expansion and scoring by default. Set `CORPUS_TEST_OLLAMA_MODEL` to another installed model.

## Source discovery and acquisition

Create a reviewed JSON array:

```json
[
  {
    "url": "https://example.org/project/docs",
    "type": "single_page",
    "method": "reader",
    "priority": 1,
    "content_date": "2025-01"
  }
]
```

Then create a manifest and acquire its URLs:

```bash
corpus research papers --input sources.json
corpus acquire papers https://example.org/project/docs
```

Supported repository-root URLs use common public Git hosts. Other URLs pass through
the configured reader prefix and are stored as Markdown. Acquisition filters empty,
markup-heavy, and non-target-language content. `corpus deduplicate papers` removes
duplicate and low-quality raw files; review the corpus before using that destructive
maintenance command.

## Processing

```bash
corpus process papers context
corpus process papers rag
corpus process papers both
corpus process papers rag --force
corpus process papers rag --only selected-paths.txt
```

`context` creates a bounded `CONTEXT.md` and asks the configured local LLM for a
summary. `rag` performs the durable indexing path. It:

1. Skips unchanged source files by SHA-256.
2. Reads YAML frontmatter into chunk metadata.
3. Applies optional `meta/hygiene.json` exclusions and flags.
4. Chunks document text with Markdown-aware section boundaries.
5. Removes common page boilerplate and low-density chunks.
6. Drops duplicate content before embedding.
7. Retries transient batch failures and isolates invalid chunks.
8. Writes synchronized `chunks`, FTS5, sqlite-vec, and source-state rows.

`--only` reads raw-relative paths, one per line. It reprocesses each listed file
even when unchanged. It validates the full list before writing and leaves other
sources intact. In default `both` mode, it skips the corpus-wide context rebuild.

Set `"chunking": {"transcript": true}` in a corpus manifest to preserve role-headed
conversations. A USER turn and its replies stay together where size permits. Short
prompts, headers, and code survive; placeholder-only messages are omitted. Transcript
chunks record `turn_start` and stay below 8,000 UTF-8 bytes. An optional
`transcript_chunk_chars` in the same manifest section sets the character target.
Existing indexes need `corpus process papers rag --force` to apply a changed mode.

`corpus import-labels papers labels.jsonl` stores imported labels in the index and
in `meta/imported-labels.json`. Reprocessing or rebuilding reapplies them by source
and record ID. Label rows with a matching `source_hash` are marked `verified`;
rows without that evidence, or whose raw source later changes, are `unverified`.

Deduplication stays within each source, so shared text uses extra index storage but keeps each source searchable.
Deleting a raw file does not remove its indexed chunks; processing and refresh do not sync deletions.
For indexes built with corpus-wide deduplication, run `python3 -m corpus_pipeline process papers rag --force` once to restore shared owners.

## Search

```bash
corpus search my-docs "embedding dimension guard"
corpus search --corpora my-docs,papers "index migration"
corpus search --all --top-k 12 "source provenance"
corpus search my-docs --no-captions "body text only"
corpus search my-docs --meta kind=note --meta group=red --label alpha,beta "review"
```

Each corpus produces BM25 and vector candidates. Federated search merges ranked lists
with Reciprocal Rank Fusion. Large corpora can use local query expansion and reranking.
`--meta KEY=VALUE` filters chunk metadata before BM25 and vector ranking. Repeat a key
for alternatives; different keys combine. `--label A[,B]` matches the `label` and
`labels` metadata written by `corpus import-labels`.
If reranking fails, the command states that it returned hybrid order. It does not label
those results as reranked.

Per-corpus `manifest.json` may set `search.fusion` (`linear` or `rrf`),
`search.expansion` (boolean), `search.conversation_cap` (non-negative integer), and
`search.display` (`first` or `passage`). A passage shows a query window and its turn
label when transcript metadata exists. The cap groups rows by `conversation_id` and
is disabled when `--meta conversation_id=...` selects a conversation. Filtered
searches omit `CONTEXT.md` sections. Context matches require whole query terms;
multi-term queries need two distinct matches.

The optional cross-encoder endpoint accepts `query` and `documents` at
`/v1/rerank`. If it is unavailable, the pipeline tries the configured local LLM. If
both fail, search keeps the hybrid order.
Query embedding waits up to 120 seconds by default. A timeout reports
`Embedding service busy` and exits with code 5.

## Chronological search

```bash
corpus first-mention my-docs "vector index" --limit 5
corpus first-mention my-docs "vector index" --no-expand --format json
```

Chronological search reads `ts`, `started_at`, `created_at`, then `content_date` from
chunk metadata. Files without dated metadata cannot appear in chronological results.
The command combines a dated FTS or LIKE scan with semantic candidates, deduplicates by
source, and sorts oldest first.

## Refresh and health

```bash
corpus refresh snapshot papers
corpus acquire papers https://example.org/project/docs
corpus refresh diff papers
corpus process papers both

corpus health --scope all
corpus health diff 2025-01-01 2025-02-01
```

Refresh is an explicit four-step workflow. The diff command returns a nonzero status
when it finds changes. Health reads corpus databases, writes only its configured metric
database, and can compare dated snapshots.

## Configuration

Copy `corpus.example.yml` to `corpus.yml`. Set `CORPUS_CONFIG` to use another file.
Environment variables override common deployment values:

- `CORPUS_ROOT`
- `CORPUS_EMBED_PROVIDER`, `CORPUS_EMBED_URL`, `CORPUS_EMBED_URL_2`
- `CORPUS_EMBED_API_KEY`
- `CORPUS_QUERY_EMBED_TIMEOUT` (seconds; query requests only)
- `OLLAMA_HOST`
- `CORPUS_LLM_CHAT_URL`, `CORPUS_LLM_CHAT_MODEL`, `CORPUS_LLM_CHAT_API_KEY`
- `CORPUS_RERANK_URL`
- `CORPUS_TOP_K`, `CORPUS_SEARCH_RERANK`, `CORPUS_QUERY_EXPANSION`
- `CORPUS_SEARCH_FUSION`, `CORPUS_CONVERSATION_CAP`, `CORPUS_SEARCH_DISPLAY`

Search setting precedence is explicit environment variable, then per-corpus
`manifest.json` search entry, then `corpus.yml` search entry, then built-in default.
The existing weighted fusion, expansion threshold, unlimited conversations, and
opening-text display remain the defaults.

API keys stay in environment variables or an untracked `corpus.yml`. The example file
contains no credential.

## Storage layout

```text
~/.corpus/
  my-docs/
    manifest.json
    raw/
    meta/
      acquisition.log
      processing.log
      imported-labels.json  # after import-labels
      hygiene.json          # optional
    processed/
      chunks.db
      CONTEXT.md
      corpus-summary.md
  health/
    corpus-health.db
  snapshots/
```

## License

MIT.
