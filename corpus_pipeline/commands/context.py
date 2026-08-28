#!/usr/bin/env python3
"""Build CONTEXT.md and corpus-summary.md for a topic."""

import sys
from pathlib import Path


from ..config import corpus_path, CONTEXT_TOKEN_BUDGET
from ..ollama_llm import summarize_corpus


def build_context(topic: str):
    """Build curated context file from raw corpus."""
    raw_dir = corpus_path(topic) / "raw"
    processed_dir = corpus_path(topic) / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    if not raw_dir.exists():
        print(f"Error: no raw directory at {raw_dir}", file=sys.stderr)
        sys.exit(1)

    # Collect all files sorted by path
    files = sorted(f for f in raw_dir.rglob("*")
                   if f.is_file() and f.suffix in {".md", ".mdx", ".txt", ".rst"})
    print(f"Found {len(files)} raw files")

    if not files:
        print("No files to process.")
        return

    # Estimate tokens: ~4 chars per token
    char_budget = CONTEXT_TOKEN_BUDGET * 4

    # Build CONTEXT.md with files that fit the budget
    context_parts = []
    context_parts.append(f"# {topic.title()} — Corpus Context\n")
    context_parts.append(f"*{len(files)} source files. Curated to ~{CONTEXT_TOKEN_BUDGET:,} tokens.*\n")
    context_parts.append("---\n")

    total_chars = sum(len(p) for p in context_parts)
    included = 0
    skipped = 0

    for f in files:
        content = f.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            continue

        rel = f.relative_to(raw_dir)
        section = f"\n## Source: {rel}\n\n{content}\n\n---\n"
        section_chars = len(section)

        if total_chars + section_chars > char_budget:
            # Try truncating this file
            remaining = char_budget - total_chars - 200  # header overhead
            if remaining > 500:
                truncated = content[:remaining] + "\n\n*[truncated]*"
                section = f"\n## Source: {rel}\n\n{truncated}\n\n---\n"
                context_parts.append(section)
                included += 1
            else:
                skipped += 1
            break

        context_parts.append(section)
        total_chars += section_chars
        included += 1

    remaining_files = len(files) - included - skipped
    if remaining_files > 0:
        context_parts.append(f"\n*{remaining_files} additional files not included (token budget reached).*\n")

    context_md = "".join(context_parts)
    context_file = processed_dir / "CONTEXT.md"
    context_file.write_text(context_md, encoding="utf-8")
    token_estimate = len(context_md) // 4
    print(f"CONTEXT.md: {included} files, ~{token_estimate:,} tokens → {context_file}")

    # Build corpus summary via local model
    print("Generating corpus summary with the configured local model...")
    file_list = [str(f.relative_to(raw_dir)) for f in files]
    try:
        summary = summarize_corpus(topic, file_list)
        summary_file = processed_dir / "corpus-summary.md"
        summary_file.write_text(f"# {topic.title()} — Corpus Summary\n\n{summary}\n")
        print(f"corpus-summary.md → {summary_file}")
    except Exception as e:
        print(f"Warning: summary generation failed: {e}", file=sys.stderr)
        print("Continuing without summary.")

    print(f"\nContext build complete:")
    print(f"  Files included: {included}/{len(files)}")
    print(f"  Token estimate: ~{token_estimate:,}")


def main():
    if len(sys.argv) < 2:
        print("Usage: build_context.py <topic>", file=sys.stderr)
        sys.exit(1)
    build_context(sys.argv[1])


if __name__ == "__main__":
    main()

