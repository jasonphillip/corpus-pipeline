"""Per-corpus search settings with validated manifest overrides."""

import os
import sys

from .config import (SEARCH_CONVERSATION_CAP, SEARCH_DISPLAY, SEARCH_FUSION,
                     SEARCH_QUERY_EXPANSION)
from .manifest import read_manifest


def corpus_search_settings(topic: str, manifest: dict | None = None) -> dict:
    """Resolve config defaults, manifest values, then explicit environment values."""
    settings = {"fusion": SEARCH_FUSION, "expansion": SEARCH_QUERY_EXPANSION,
                "conversation_cap": SEARCH_CONVERSATION_CAP, "display": SEARCH_DISPLAY,
                "rerank": None}
    block = (manifest if manifest is not None else read_manifest(topic)).get("search")
    if block is not None and not isinstance(block, dict):
        print(f"Warning: [{topic}] invalid manifest search block; using defaults", file=sys.stderr)
    elif isinstance(block, dict):
        for key, value in block.items():
            valid = ((key == "fusion" and isinstance(value, str) and value in {"linear", "rrf"}) or
                     (key in {"expansion", "rerank"} and isinstance(value, bool)) or
                     (key == "conversation_cap" and type(value) is int and value >= 0) or
                     (key == "display" and isinstance(value, str) and value in {"first", "passage"}))
            if valid:
                settings[key] = value
            else:
                print(f"Warning: [{topic}] invalid search setting: {key}", file=sys.stderr)
    env = {"fusion": "CORPUS_SEARCH_FUSION", "expansion": "CORPUS_QUERY_EXPANSION",
           "conversation_cap": "CORPUS_CONVERSATION_CAP", "display": "CORPUS_SEARCH_DISPLAY"}
    defaults = {"fusion": SEARCH_FUSION, "expansion": SEARCH_QUERY_EXPANSION,
                "conversation_cap": SEARCH_CONVERSATION_CAP, "display": SEARCH_DISPLAY}
    for key, name in env.items():
        if name in os.environ:
            settings[key] = defaults[key]
    return settings
