"""Canonical chunk dates shared by retrieval, first mention, and evaluation."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
import re


DATE_KEYS = ("ts", "recording_start", "first", "started_at", "date",
             "created_at", "content_date", "period", "original_mtime")


def chunk_ts(metadata: dict) -> str | None:
    """Return the highest-priority valid date, preserving its source precision."""
    if not isinstance(metadata, dict):
        return None
    for key in DATE_KEYS:
        value = metadata.get(key)
        if key == "original_mtime":
            try:
                if value is not None and str(value).strip():
                    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError, OSError):
                pass
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}", value):
            try:
                date.fromisoformat(value + "-01")
            except ValueError:
                continue
            return value + "-01" if key == "period" else value
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            try:
                date.fromisoformat(value)
                return value
            except ValueError:
                continue
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            continue
    return None


def chunk_date_sql(metadata_column: str = "metadata_json") -> str:
    """SQLite expression mirroring date-key priority for indexed search."""
    parts = []
    for key in DATE_KEYS:
        value = f"json_extract({metadata_column}, '$.{key}')"
        if key == "original_mtime":
            parts.append(f"strftime('%Y-%m-%dT%H:%M:%fZ', {value}, 'unixepoch')")
        else:
            is_text = f"json_type({metadata_column}, '$.{key}') = 'text'"
            parts.append(f"CASE WHEN {is_text} AND length({value}) = 7 "
                         f"AND date({value} || '-01') IS NOT NULL THEN {value} || '-01' "
                         f"WHEN {is_text} AND date({value}) IS NOT NULL THEN NULLIF({value}, '') END")
    return "COALESCE(" + ", ".join(parts) + ")"


def date_bounds(since: str | None, until: str | None) -> tuple[str | None, str | None]:
    """Parse inclusive year, month, or day filters into YYYY-MM-DD bounds."""
    def bound(value: str | None, upper: bool) -> str | None:
        if value is None:
            return None
        if re.fullmatch(r"\d{4}", value):
            return f"{value}-12-31" if upper else f"{value}-01-01"
        if re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = map(int, value.split("-"))
            try:
                last = calendar.monthrange(year, month)[1]
            except (calendar.IllegalMonthError, ValueError):
                raise ValueError(f"invalid date filter: {value}") from None
            return f"{year:04d}-{month:02d}-{last if upper else 1:02d}"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            try:
                date.fromisoformat(value)
                return value
            except ValueError:
                pass
        raise ValueError(f"invalid date filter: {value}")
    lower, upper = bound(since, False), bound(until, True)
    if lower and upper and lower > upper:
        raise ValueError("--since is after --until")
    return lower, upper


def in_date_bounds(metadata: dict, since: str | None, until: str | None) -> bool:
    stamp = chunk_ts(metadata)
    day = (stamp + "-01" if len(stamp) == 7 else stamp[:10]) if stamp else None
    return bool(day and (since is None or day >= since) and (until is None or day <= until))
