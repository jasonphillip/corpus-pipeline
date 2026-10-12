# Larkspur Systems Synthetic Retrieval Set

This corpus and its relevance judgments are synthetic. A language model (Claude) wrote them for this evaluation on 2026-10-12. Larkspur Systems, its wiki, its products, its part numbers, its ports and every event in the documents are fictional. Any resemblance to a real company or system is accidental.

## Contents

- `docs/`: 45 Markdown documents of 250 to 500 words each. Each starts with a title line and a note that the document is synthetic. Forms: 12 runbooks, 8 incident reports, 8 API and config references, 7 hardware notes, 5 onboarding and process pages, 5 decision records.
- `queries.jsonl`: 40 queries, one JSON object per line. Fields: `id`, `query`, `split` (`eval` or `tune`), `relevant`. Each entry of `relevant` has `doc` (a file name in `docs/`) and `passage` (text copied from that document).
- Split: 30 `eval` queries and 10 `tune` queries.
- Query forms: about a third are short keyword queries, about as many again are natural questions, and ten are paraphrases that avoid the wording of the document.
- 9 queries have two relevant documents. The rest have one. Where two documents answer one query, the repeated fact is intended.

## How relevance was decided

The author decided relevance at writing time. For each query, the author named the exact passage that answers it. A document is relevant when it holds such a passage. No other document was judged. A document that mentions the topic without the answer counts as not relevant, and the judgments do not grade partial relevance.

## Mechanical check

Every `passage` must be a verbatim substring of its named document. A mechanical check must confirm this before the set is used. The author ran a substring check once at the time of writing. A later edit to any document can break a passage, so run the check again after any change.

Check command: `python3 eval/run_eval.py --check-passages` (from the repository root).

## Limits

The set is small and self-authored. The same model wrote the documents, the queries and the judgments, so the vocabulary of the queries is close to that of the documents. Do not present this set as an independent benchmark. Use it to compare configurations of one search tool and to catch regressions. Treat small score differences with caution: with 30 eval queries, one query moves a hit-rate metric by more than 3 points.

Use the `tune` queries to choose settings, such as fusion weights. Report results on the `eval` queries only.

## License

MIT, the same license as the repository that holds this directory.
