# Retrieval evaluation

Run the real evaluation from the repository root with a live OpenAI-compatible embedding endpoint:

```bash
export CORPUS_EMBED_PROVIDER=openai
export CORPUS_EMBED_URL=http://localhost:8000/v1/embeddings
export CORPUS_OPENAI_EMBED_MODEL=Qwen3-Embedding-4B
export CORPUS_OPENAI_EMBED_DIMS=2560
python3 eval/run_eval.py
```

The runner creates a fresh temporary `CORPUS_ROOT`, indexes `eval/dataset/docs`, and writes dated JSON and Markdown files to `eval/results/`. Set `CORPUS_RERANK_URL`, `OLLAMA_HOST`, `CORPUS_TEST_OLLAMA_URL`, or `CORPUS_LLM_CHAT_URL` to enable the rerank mode. Equivalent service settings in `CORPUS_CONFIG` also count. It reports that mode as unrun when no service is explicitly configured or the service fails. Check passages without a service using `python3 eval/run_eval.py --check-passages`. Test indexing and report mechanics using `python3 eval/run_eval.py --fake-embedder`; fake numbers do not measure retrieval quality.

The synthetic corpus and relevance judgments were written by a language model on 2026-10-12. Relevance was fixed at writing time by naming the exact answer passage for each judged document. The runner checks that every judged passage remains a verbatim substring before indexing. The dataset has 30 `eval` queries and 10 `tune` queries. Only `eval` queries enter reported metrics; tune IDs are listed separately.

Each mode uses the same fresh index. BM25, vector, hybrid fusion, and optional hybrid plus reranking rank chunks. Reranking scores the hybrid top 20 chunks. Metrics first deduplicate results by `source_path` document. Recall@5 and Recall@10 count distinct relevant documents found by each cutoff, divided by the number of judged relevant documents for that query. nDCG@10 uses binary document relevance and the ideal ordering of those judged documents. Reported values are macro means across the 30 eval queries. Per-query ranks and hybrid top-5 misses appear in JSON.

Each mode runs one untimed warmup query. Query p50 and p95 latencies then measure 30 sequential eval queries against the same process, connection, and index. The warmup primes process and SQLite caches; no cache is cleared between modes. Indexing wall time includes embedding and SQLite writes. The SQLite file size is measured after indexing.

This is a small self-authored set and not an independent benchmark. Compare configurations cautiously; real retrieval quality requires a live embedding run.
