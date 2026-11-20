"""Read-only session cohort statistics. No chat or tool payloads cross this boundary."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class Counts:
    sessions: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_usd: float | None = None
    estimated_sessions: int = 0


@dataclass(frozen=True)
class StatsSnapshot:
    as_of: datetime
    daily: tuple[tuple[date, Counts], ...]
    total: Counts


def load_stats(path: str | Path, days: int = 7, *, now: datetime | None = None) -> StatsSnapshot:
    """Bucket lifetime counters for non-hidden rows by start day, through now (UTC)."""
    if not 1 <= days <= 365:
        raise ValueError("Choose a range from 1 to 365 days.")
    now = now if now is not None else datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("Snapshot time must include a timezone.")
    now = now.astimezone(timezone.utc)
    first = now.date() - timedelta(days=days - 1)
    start = datetime.combine(first, time.min, timezone.utc)
    uri = Path(path).expanduser().resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
        if not {"started_at", "tool_call_count", "input_tokens", "output_tokens"} <= columns:
            raise ValueError("Unsupported Hermes session store schema.")
        hidden = "AND COALESCE(hidden, 0) = 0" if "hidden" in columns else ""
        estimate = "estimated_cost_usd" if "estimated_cost_usd" in columns else "NULL"
        rows = db.execute(f"""
            SELECT date(CAST(started_at AS INTEGER), 'unixepoch') AS day, COUNT(*),
                   COALESCE(SUM(tool_call_count), 0), COALESCE(SUM(input_tokens), 0),
                   COALESCE(SUM(output_tokens), 0), SUM({estimate}), COUNT({estimate})
            FROM sessions
            WHERE started_at >= ? AND started_at < ? {hidden}
            GROUP BY day
        """, (start.timestamp(), now.timestamp()))
        observed = {date.fromisoformat(day): Counts(*counts) for day, *counts in rows}

    daily = tuple((day, observed.get(day, Counts()))
                  for day in (first + timedelta(days=i) for i in range(days)))
    counts = [counts for _, counts in daily]
    estimated = [c.estimated_usd for c in counts if c.estimated_usd is not None]
    total = Counts(sum(c.sessions for c in counts), sum(c.tool_calls for c in counts),
                   sum(c.input_tokens for c in counts), sum(c.output_tokens for c in counts),
                   sum(estimated) if estimated else None, sum(c.estimated_sessions for c in counts))
    return StatsSnapshot(now, daily, total)
