# Extraction notes

The intended final home is `~/projects/corpus-pipeline`. This directory is a staging
area used only because the build sandbox can write inside its current workspace. No
runtime file contains the staging path.

## Source provenance

Paths in the destination column are relative to the repository root.

### CLI and shared library

| Source | Destination | Treatment |
|---|---|---|
| `~/projects/skills/bin/corpus` | `corpus_pipeline/cli.py`, `corpus_pipeline/__main__.py`, `pyproject.toml` | Replaced Bash script dispatch with one console entry point and in-package calls. |
| `~/projects/skills/lib/__init__.py` | `corpus_pipeline/__init__.py` | Replaced the empty initializer with package metadata. |
| `~/projects/skills/lib/chunker.py` | `corpus_pipeline/chunker.py` | Copied with package-relative imports. |
| `~/projects/skills/lib/code_chunker.py` | `corpus_pipeline/code_chunker.py` | Copied with package-relative imports. |
| `~/projects/skills/lib/config.py` | `corpus_pipeline/config.py`, `corpus.example.yml` | Rewritten around YAML configuration, environment overrides, and local defaults. |
| `~/projects/skills/lib/content_filter.py` | `corpus_pipeline/content_filter.py` | Copied without deployment-specific changes. |
| `~/projects/skills/lib/corpus_db.py` | `corpus_pipeline/corpus_db.py` | Preserved schema, dimension guards, deduplication, FTS5, sqlite-vec, and hybrid fusion. |
| `~/projects/skills/lib/embedder.py` | `corpus_pipeline/embedder.py` | Generalized OpenAI-compatible and Ollama clients. Preserved batching and endpoint fan-out. |
| `~/projects/skills/lib/hygiene.py` | `corpus_pipeline/hygiene.py` | Generalized link-table column names and canonical metadata names. |
| `~/projects/skills/lib/manifest.py` | `corpus_pipeline/manifest.py` | Copied with package-relative imports. |
| `~/projects/skills/lib/ollama_llm.py` | `corpus_pipeline/ollama_llm.py` | Preserved JSON recovery, generation, expansion, and honest rerank fallback. Added optional chat authentication. |
| `~/projects/skills/lib/source_url.py` | `corpus_pipeline/source_url.py` | Copied without deployment-specific changes. |
| `~/projects/skills/lib/status.py` | `corpus_pipeline/status.py` | Rewritten to use configured corpus and status paths. |
| `~/projects/skills/lib/symbol_index.py` | `corpus_pipeline/symbol_index.py` | Copied with the optional Universal Ctags interface intact. |

Backup files and the unrelated analytics package were not read into the destination.

### Command scripts

| Source | Destination | Treatment |
|---|---|---|
| `~/projects/skills/skills/search-corpus/scripts/search_hybrid.py` | `corpus_pipeline/commands/search.py` | Preserved RRF federation, flags, dimension checks, captions, expansion, and rerank reporting. |
| `~/projects/skills/skills/process-corpus/scripts/process_chunks.py` | `corpus_pipeline/commands/process.py` | Preserved incremental processing, retry logic, metadata, hygiene, and pre-embed deduplication. |
| `~/projects/skills/skills/process-corpus/scripts/build_context.py` | `corpus_pipeline/commands/context.py` | Preserved bounded context and local summary generation. |
| `~/projects/skills/skills/process-corpus/scripts/bridge_delta_frontmatter.py` | `corpus_pipeline/commands/bridge_frontmatter.py` | Replaced fixed directories and source labels with arguments. |
| `~/projects/skills/skills/process-corpus/scripts/reembed_changed_chunks.py` | `corpus_pipeline/commands/reembed.py` | Replaced fixed corpus, dimension, and snapshot paths with runtime checks and configuration. |
| `~/projects/skills/skills/process-corpus/scripts/strip_poisoned_frontmatter.py` | `corpus_pipeline/commands/strip_frontmatter.py` | Made the removed key set configurable. Kept backup and FTS rebuild behavior. |
| `~/projects/skills/skills/process-corpus/scripts/writeback_task_type_labels.py` | `corpus_pipeline/commands/import_labels.py` | Generalized record identifiers, label names, metadata keys, and file paths. |
| `~/projects/skills/skills/acquire-corpus/scripts/acquire_git.py` | `corpus_pipeline/commands/acquire_git.py` | Preserved shallow clone, documentation selection, and quality filters. |
| `~/projects/skills/skills/acquire-corpus/scripts/acquire_jina.py` | `corpus_pipeline/commands/acquire_page.py` | Preserved reader-prefix fetch and Markdown storage. The prefix is configurable. |
| `~/projects/skills/skills/acquire-corpus/scripts/dedup_raw.py` | `corpus_pipeline/commands/deduplicate.py` | Preserved content hashing and quality filtering. |
| `~/projects/skills/skills/acquire-corpus/scripts/finalize_acquisition.py` | `corpus_pipeline/commands/finalize.py` | Preserved manifest reconciliation, attribution, logging, and status updates. |
| `~/projects/skills/skills/research-topic/scripts/build_manifest.py` | `corpus_pipeline/commands/research.py`, `corpus_pipeline/cli.py` | Preserved JSON-to-manifest behavior. The CLI accepts a file or standard input. |
| `~/projects/skills/skills/find-first-mention/scripts/find_first_mention.py` | `corpus_pipeline/commands/first_mention.py` | Replaced subprocess search with an in-process call. Preserved timestamp fallbacks and merge order. |
| `~/projects/skills/skills/refresh-corpus/scripts/diff_corpus.py` | `corpus_pipeline/commands/refresh.py` | Preserved raw-file snapshots and change reports. |
| `~/projects/skills/skills/corpus-health/scripts/corpus_health.py` | `corpus_pipeline/commands/health.py` | Preserved schema, row, vector, dimension, date, metadata, snapshot, and diff checks. Removed shared-workspace output. |

`corpus_pipeline/commands/acquire.py` and `corpus_pipeline/commands/__init__.py` are new
package adapters. They replace shell-level dispatch.

### Tests

| Source test | Destination coverage |
|---|---|
| `~/projects/skills/skills/search-corpus/tests/test_caption_filter.py` | `tests/test_search.py` |
| `~/projects/skills/skills/search-corpus/tests/test_llm_honesty.py` | `tests/test_llm_helpers.py` |
| `~/projects/skills/skills/search-corpus/tests/test_rrf.py` | `tests/test_search.py` |
| `~/projects/skills/skills/search-corpus/tests/test_search_output.py` | `tests/test_search.py` |
| `~/projects/skills/skills/search-corpus/tests/test_top_k.py` | `tests/test_search.py` |
| `~/projects/skills/skills/process-corpus/tests/test_hygiene.py` | `tests/test_hygiene.py` |
| `~/projects/skills/skills/process-corpus/tests/test_process_chunks_hygiene.py` | `tests/test_hygiene.py`, `tests/test_process.py` |
| `~/projects/skills/skills/acquire-corpus/tests/test_acquisition.py` | `tests/test_acquisition.py` |
| `~/projects/skills/skills/corpus-health/tests/test_corpus_health.py` | `tests/test_health.py` |

`tests/test_chunking.py` and `tests/test_first_mention.py` add pure-local coverage for
shared modules and chronology. `tests/test_services.py` records the service-dependent
end-to-end coverage as explicit skips.

### Documentation and repository files

| Source | Destination |
|---|---|
| `~/projects/ai-history-miner/README.md` | `README.md` structure and tone only |
| `~/projects/ai-history-miner/LICENSE` | `LICENSE` MIT terms; author line generalized |
| `~/projects/ai-history-miner/miner.example.yml` | `corpus.example.yml` configuration pattern |
| `~/projects/ai-history-miner/banned-strings.example.txt` | `banned-strings.example.txt` pattern |
| New publication files | `.gitignore`, `examples/docs/*.md`, `examples/quickstart.txt`, `pyproject.toml` |

The harness instruction files were used only to identify workflow order and user-facing
concepts. They were not copied.

## Generalizations

- Replaced private machine names, fixed addresses, user directories, and shared-folder
  paths with YAML values and environment overrides.
- Replaced internal corpus names with `my-docs`, `papers`, and `codebase` examples.
- Removed personal identity, contact details, customer names, handling labels, and
  program-specific wording.
- Removed incident dates and historical deployment commentary. Comments now state only
  the active technical constraint.
- Removed the retired proxy-specific path. The embedder now supports one or two direct
  OpenAI-compatible endpoints and an Ollama fallback.
- Kept API keys empty by default. They load only from configuration or environment.
- Kept remote acquisition as runtime behavior. No acquisition ran during extraction.
- Kept raw-file deletion in the explicit `deduplicate` maintenance command. It does not
  run during normal processing.

## Judgment calls and remaining limits

- Source discovery was implemented by the harness instruction layer, not by the source
  Python script. The public `research` command therefore records a reviewed JSON list;
  it does not claim to perform web discovery.
- The default page reader is a remote Markdown conversion service because that is the
  source implementation's actual API. Users can replace its prefix in configuration.
- The extracted processing script indexes document extensions. The retained code chunker
  and symbol index remain reusable APIs, but the source command did not call them.
- Health checks compare the manifest dimension with the sqlite-vec schema. They use
  SQLite quick-check and row synchronization instead of the source tool's write-mode
  FTS control command. They do not contact the embedding service. Processing probes the
  live dimension before inserts.
- Maintenance utilities keep their data-mutation behavior. They now require explicit
  paths or corpus names and create configured snapshots before database rewrites.

No unresolved fleet dependency remains in the normal research, acquire, process, search,
chronology, refresh, or health workflow.
