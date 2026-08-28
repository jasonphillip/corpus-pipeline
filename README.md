# corpus-pipeline

Turn a directory of documents into a local hybrid-search corpus on one GPU box.

The pipeline records discovered sources, acquires repository documentation and web
pages, cleans and chunks the files, embeds each chunk, and stores the result in one
SQLite database per corpus. Search combines SQLite FTS5 BM25 with sqlite-vec cosine
distance. Optional local models expand queries, rerank candidates, and summarize a
corpus.

No hosted language model is required. Page acquisition uses the configured reader
service. Repository acquisition uses a shallow `git clone` at runtime.

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

## Quickstart

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
cp corpus.example.yml corpus.yml
```

Edit `corpus.yml`. Set the embedding model name and its output dimension. Start the
embedding service before processing.

Use the included synthetic files for a local first run:

```bash
mkdir -p ~/.corpus/my-docs/raw/example
cp examples/docs/*.md ~/.corpus/my-docs/raw/example/
corpus process my-docs rag
corpus search my-docs "how does hybrid retrieval work"
corpus first-mention my-docs "vector index"
corpus health --scope all --no-persist
```

`examples/quickstart.txt` shows the expected command sequence and abbreviated output.

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
