#!/usr/bin/env bash
# Example bounded nightly context pass. Configure CORPUS_ROOT and endpoints first.
set -euo pipefail

if [[ -z "${CORPUS_CONTEXT_TOPICS:-}" ]]; then
  echo "Set CORPUS_CONTEXT_TOPICS to a comma-separated list." >&2
  exit 2
fi

IFS=, read -r -a topics <<< "$CORPUS_CONTEXT_TOPICS"
for topic in "${topics[@]}"; do
  [[ "$topic" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { echo "Invalid corpus name" >&2; exit 2; }
  python3 -m corpus_pipeline context write --corpus "$topic" --max-minutes "${CONTEXT_WRITE_MINUTES:-30}" --parallel "${CONTEXT_PARALLEL:-4}"
  python3 -m corpus_pipeline context apply --corpus "$topic" --max-minutes "${CONTEXT_APPLY_MINUTES:-30}" --batch "${CONTEXT_BATCH:-32}"
  python3 -m corpus_pipeline context prune --corpus "$topic"
done
