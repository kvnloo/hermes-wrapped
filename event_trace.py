"""Read-only metadata adapters for the live timeline."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Literal

Origin = Literal["Hermes", "Voice"]
VOICE_KINDS = frozenset({
    "call.created", "call.start", "call.negotiated", "call.setup_failed", "call.closed",
    "delegation.start", "delegation.done", "lanes.tier", "lanes.spoken",
})
MAX_SESSIONS = 80
MAX_FILES = 16
MAX_TAIL_BYTES = 2 * 1024 * 1024
MAX_ROWS_PER_CALL = 180
MAX_WALL = 253402214400  # Last full UTC day representable by datetime.


@dataclass(frozen=True)
class TraceEvent:
    kind: str
    wall: float
    elapsed: float


@dataclass(frozen=True)
class TraceSpan:
    kind: str
    start: float | None
    end: float | None
    seconds: float | None
    state: Literal["Complete", "End unknown", "Start unknown"]


@dataclass(frozen=True)
class TraceLane:
    origin: Origin
    span: TraceSpan
    details: tuple[TraceEvent | TraceSpan, ...] = ()


@dataclass(frozen=True)
class TraceSnapshot:
    start: float
    as_of: float
    lanes: tuple[TraceLane, ...]
    notices: tuple[str, ...]


def _clock(value: object, upper: float) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= upper:
        return float(value)
    return None


def _span(kind: str, start: float | None, end: float | None,
          seconds: float | None = None) -> TraceSpan:
    state = "Start unknown" if start is None else "End unknown" if end is None else "Complete"
    return TraceSpan(kind, start, end, seconds, state)


def _sessions(path: Path, start: float, now: float) -> list[TraceLane]:
    uri = path.expanduser().resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
        if not {"started_at", "ended_at"} <= columns:
            raise sqlite3.OperationalError("Unsupported sessions schema")
        hidden = "AND COALESCE(hidden, 0) = 0" if "hidden" in columns else ""
        activity = "last_activity_at" if "last_activity_at" in columns else "NULL"
        rows = db.execute(f"""
            SELECT started_at, ended_at FROM sessions
            WHERE started_at BETWEEN 0 AND ?
              AND (started_at >= ? OR ended_at BETWEEN ? AND ?
                   OR (ended_at IS NULL AND {activity} BETWEEN ? AND ?))
              {hidden}
            ORDER BY MAX(started_at, COALESCE(ended_at, started_at),
                         COALESCE({activity}, started_at)) DESC, started_at DESC
            LIMIT ?
        """, (now, start, start, now, start, now, MAX_SESSIONS + 1))
        lanes = []
        for raw_start, raw_end in rows:
            a, b = _clock(raw_start, now), _clock(raw_end, now)
            if a is None:
                continue
            if b is not None and b < a:
                b = None
            lanes.append(TraceLane("Hermes", _span("Session record", a, b, None if b is None else b - a)))
        return lanes


def _voice_rows(path: Path, now: float) -> tuple[list[tuple[TraceEvent, str | None]], int, bool]:
    with path.open("rb") as source:
        size = source.seek(0, 2)
        offset = max(0, size - MAX_TAIL_BYTES)
        source.seek(offset)
        raw = source.read(size - offset)
    # Drop partial boundary records. A writer's trailing line is retried on the next frame.
    lines = raw.split(b"\n")
    events = []
    seen = set()
    malformed = 0
    for line in lines[bool(offset):-1]:
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            malformed += 1
            continue
        if not isinstance(row, dict) or not isinstance(kind := row.get("ev"), str):
            malformed += 1
            continue
        if kind not in VOICE_KINDS:
            continue
        wall = _clock(row.get("wall"), MAX_WALL)
        elapsed = _clock(row.get("t"), MAX_WALL)
        if wall is None or elapsed is None:
            malformed += 1
            continue
        if wall > now:
            continue
        did = row.get("did") if kind in ("delegation.start", "delegation.done") else None
        did = did if isinstance(did, str) and 0 < len(did) <= 256 else None
        key = (TraceEvent(kind, wall, elapsed), did)
        if key not in seen:
            seen.add(key)
            events.append(key)
    return events, malformed, bool(offset)


def _voice_span(kind: str, a: TraceEvent | None, b: TraceEvent | None) -> TraceSpan:
    seconds = b.elapsed - a.elapsed if a and b and a.elapsed <= b.elapsed else None
    return _span(kind, a.wall if a else None, b.wall if b else None, seconds)


def _recent(row: TraceEvent | TraceSpan) -> float:
    if isinstance(row, TraceEvent):
        return row.wall
    return max(row.start or 0, row.end or 0)


def _voice_lane(rows: list[tuple[TraceEvent, str | None]], start: float) -> tuple[TraceLane, bool] | None:
    if not any(event.wall >= start for event, _ in rows):
        return None
    opened = closed = None
    pending: dict[str, TraceEvent] = {}
    details: list[TraceEvent | TraceSpan] = []
    for event, did in sorted(rows, key=lambda row: row[0].elapsed):
        kind = event.kind
        if kind == "call.created":
            opened = opened or event
        elif kind == "call.closed":
            closed = event
        elif kind == "delegation.start":
            if did and did not in pending:
                pending[did] = event
            else:
                details.append(_voice_span("Delegation", event, None))
        elif kind == "delegation.done":
            details.append(_voice_span("Delegation", pending.pop(did, None) if did else None, event))
        else:
            details.append(event)
    details.extend(_voice_span("Delegation", event, None) for event in pending.values())
    details = sorted((row for row in details if _recent(row) >= start), key=_recent)
    span = _voice_span("Voice call", opened, closed) if opened or closed else TraceSpan(
        "Voice call", None, None, None, "Start unknown")
    return TraceLane("Voice", span, tuple(details[-MAX_ROWS_PER_CALL:])), len(details) > MAX_ROWS_PER_CALL


def load_trace(db: Path, voice_root: Path, *, hours: int = 24,
               now: float | None = None) -> TraceSnapshot:
    if not 1 <= hours <= 168:
        raise ValueError("Choose a window from 1 to 168 hours.")
    now = time.time() if now is None else now
    if not math.isfinite(now) or not 0 <= now <= MAX_WALL:
        raise ValueError("Invalid observation time.")
    start = now - hours * 3600
    notices = []
    try:
        sessions = _sessions(db, start, now)
    except (sqlite3.Error, OSError):
        sessions = []
        notices.append("Hermes session store unavailable.")
    if len(sessions) > MAX_SESSIONS:
        notices.append(f"Showing up to {MAX_SESSIONS} Hermes records.")
    lanes = sessions[:MAX_SESSIONS]
    files = []
    try:
        if not voice_root.is_dir():
            raise OSError
        for path in voice_root.glob("*/*.jsonl"):
            if path.is_symlink() or path.parent.is_symlink():
                continue
            try:
                modified = path.stat().st_mtime
                if modified >= start and path.is_file():
                    files.append((modified, path))
            except OSError:
                continue  # A concurrent rotation may remove an entry.
    except OSError:
        notices.append("Voice trace directory unavailable.")
    if len(files) > MAX_FILES:
        notices.append(f"Reading the {MAX_FILES} most recently modified voice traces in the window.")
    bad = unavailable = limited = 0
    for _, path in sorted(files, reverse=True)[:MAX_FILES]:
        try:
            rows, malformed, tail_only = _voice_rows(path, now)
        except OSError:
            unavailable += 1
            continue
        bad += malformed
        lane = _voice_lane(rows, start)
        if lane is not None:
            lanes.append(lane[0])
        limited += tail_only or (lane is not None and lane[1])
    if limited:
        notices.append(f"{limited} measured voice traces hit a read or display limit; boundaries may be missing.")
    if bad:
        notices.append(f"{bad} measured malformed records skipped in scanned voice trace data.")
    if unavailable:
        notices.append(f"{unavailable} measured voice files not readable this refresh.")

    def newest(lane: TraceLane) -> float:
        return max(_recent(lane.span), max(map(_recent, lane.details), default=0))

    return TraceSnapshot(start, now, tuple(sorted(lanes, key=newest, reverse=True)), tuple(notices))
