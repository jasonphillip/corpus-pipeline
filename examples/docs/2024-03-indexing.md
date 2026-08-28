---
ts: "2024-03-03T14:30:00Z"
source_file: "synthetic/indexing-design.md"
title: "Vector index design"
---

# Vector index design

The second design adds a vector index through sqlite-vec. Every chunk row has one FTS5
row and one fixed-dimension vector row. The processor checks the byte length before it
inserts an embedding. This guard prevents a fallback model from silently corrupting an
existing index with vectors of another dimension.

Source hashes make processing resumable. Repeated runs skip unchanged files and remove
duplicate chunk content before calling the embedding service.

