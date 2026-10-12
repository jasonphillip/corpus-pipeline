# corpus-pipeline

This project helps one person index their own documents for search on one machine with local models.

## What this is, in one verifiable line

This repo turns a directory of documents into a local hybrid-search corpus in one SQLite file, combining FTS5 keyword scores with sqlite-vec vector scores and an optional local reranker.

The mechanism is normalized weighted fusion of the two candidate lists.

Verify the mechanics with `python3 -m pytest tests/ -q`, then try the quickstart on `examples/docs/`.

The canonical source is https://github.com/jasonphillip/corpus-pipeline

Extracted from a private working tree on 2026-08-28.

## What works today and limits

The pipeline acquires, chunks, and indexes documents. Search combines FTS5 keyword results with cosine vectors. Optional local models expand queries and rerank results.
Deduplication stays within each source. Deleting a raw file does not remove its indexed chunks.
Legacy L2 vector indexes need the [documented rebuild](#rebuilding-a-legacy-l2-index).
BM25 joins all query tokens with AND, including common question words. [Retrieval evaluation](#retrieval-evaluation) shows the effect.

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

## Retrieval evaluation

The [evaluation method](eval/README.md) checks passages and compares document-level retrieval modes.
The [2026-10-12 result](eval/results/2026-10-12-833796a.md) used git revision `833796a`, Qwen3-Embedding-4B at 2560 dimensions through llama.cpp, and Ollama `qwen3:14b` for reranking.
The run used 30 eval queries and 45 synthetic documents of one chunk each. Building the index took 8.6 seconds.

| Mode | Recall@5 | Recall@10 | nDCG@10 | p50 ms | p95 ms | Queries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| bm25 | 0.250 | 0.250 | 0.254 | 0.24 | 0.35 | 30 |
| vector | 1.000 | 1.000 | 0.936 | 43.39 | 45.98 | 30 |
| hybrid | 1.000 | 1.000 | 0.985 | 43.11 | 46.03 | 30 |
| hybrid_rerank | 1.000 | 1.000 | 0.992 | 1744.28 | 3106.54 | 30 |

Run the evaluation from the repository root with the same local services:

```bash
CORPUS_EMBED_PROVIDER=openai CORPUS_EMBED_URL=http://localhost:8000/v1/embeddings CORPUS_OPENAI_EMBED_MODEL=Qwen3-Embedding-4B CORPUS_OPENAI_EMBED_DIMS=2560 CORPUS_TEST_OLLAMA_URL=http://localhost:11434 CORPUS_TRIAGE_MODEL=qwen3:14b CORPUS_SCORING_MODEL=qwen3:14b python3 eval/run_eval.py
```

The corpus and judgments are synthetic and self-authored. These numbers show the mechanics on an easy set and are not a benchmark.
Vector and hybrid recall every judged document on this set. Reranking adds 0.007 nDCG@10 at about 40 times the p50 latency.
BM25 alone misses three quarters of the queries because `_build_fts_query` joins every token with AND, including words like "how", "does", and "the".
A natural question such as "How does the admin API authenticate requests?" matches no chunk, while a keyword query such as "lsctl purge older-than" does.
This is a known limitation of the keyword leg. Hybrid search covers it through the vector leg.
Relaxing the keyword query is a possible change that was not made here.

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

Deduplication stays within each source, so shared text uses extra index storage but keeps each source searchable.
Deleting a raw file does not remove its indexed chunks; processing and refresh do not sync deletions.
For indexes built with corpus-wide deduplication, run `python3 -m corpus_pipeline process papers rag --force` once to restore shared owners.

## Search

```bash
corpus search my-docs "embedding dimension guard"
corpus search --corpora my-docs,papers "index migration"
corpus search --all --top-k 12 "source provenance"
corpus search my-docs --no-captions "body text only"
```

Each corpus produces BM25 and vector candidates. Federated search merges ranked lists
with Reciprocal Rank Fusion. Large corpora can use local query expansion and reranking.
If reranking fails, the command states that it returned hybrid order. It does not label
those results as reranked.

The optional cross-encoder endpoint accepts `query` and `documents` at
`/v1/rerank`. If it is unavailable, the pipeline tries the configured local LLM. If
both fail, search keeps the hybrid order.

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
- `OLLAMA_HOST`
- `CORPUS_LLM_CHAT_URL`, `CORPUS_LLM_CHAT_MODEL`, `CORPUS_LLM_CHAT_API_KEY`
- `CORPUS_RERANK_URL`
- `CORPUS_TOP_K`, `CORPUS_SEARCH_RERANK`, `CORPUS_QUERY_EXPANSION`

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
