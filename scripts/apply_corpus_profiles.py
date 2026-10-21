#!/usr/bin/env python3
"""Apply a YAML or JSON corpus profile mapping."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from corpus_pipeline.commands.profiles import main

if __name__ == "__main__":
    raise SystemExit(main())
