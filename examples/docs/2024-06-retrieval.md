---
ts: "2024-06-20T09:15:00Z"
source_file: "synthetic/retrieval-review.md"
title: "Hybrid retrieval review"
---

# Hybrid retrieval review

Hybrid retrieval combines two signals. FTS5 BM25 finds exact terms, while cosine
distance finds related language. The pipeline normalizes both result sets and applies
configured weights. A per-source limit prevents one long document from filling every
result position.

Federated search ranks each corpus independently. Reciprocal Rank Fusion then combines
the ranked lists. An optional local reranker can reorder the fused candidate pool.

