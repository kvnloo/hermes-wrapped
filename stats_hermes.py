"""Metadata-only read model over every Hermes profile's session store.

Hermes records token usage per (session, model, provider, task), not per API call. Each
assistant message is one model call and receives an equal share of its usage row, so a
call's tokens and cost are averages while every total reconciles with the store. A call's
duration is inferred from the gap to the previous message in the same session.

Selected: ids, timestamps, roles, model and provider names, token counts, costs, tool names,
finish and error kinds, source and working-directory basename. Never selected: message
content, titles, prompts, reasoning or tool arguments.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import sqlite3
import time

MAX_GAP_MS = 30 * 60 * 1000  # A longer gap is idle time, not one model call.
WINDOW_SLACK_MS = 5_000  # Usage first_seen/last_seen are written around, not at, the message.

# Hermes finish_reason -> omp stopReason vocabulary.
STOP = {"stop": "stop", "tool_calls": "toolUse", "length": "length", "incomplete": "aborted",
        "verify_hook_continue": "stop"}
ERROR_LABEL = {"error": "Failed turn", "aborted": "Interrupted or incomplete"}


@dataclass(frozen=True)
class Call:
    """One model call (kind "turn") or a batch of side-channel calls (kind = Hermes task)."""
    id: int
    at: int  # ms epoch; a batch sits at its last_seen
    profile: str
    session: str  # opaque session key
    project: str
    model: str
    provider: str
    kind: str
    agent: str  # "main" | "subagent"
    calls: int
    input: float
    output: float
    cache_read: float
    cache_write: float
    reasoning: float
    cost: float
    unpriced: bool  # tokens recorded but no API-equivalent price
    stop: str
    duration: float | None  # ms, inferred
    tools: int  # tool results that followed this call

    @property
    def tokens(self) -> float:
        return self.input + self.output + self.cache_read + self.cache_write

    @property
    def error(self) -> str | None:
        return ERROR_LABEL.get(self.stop)


@dataclass(frozen=True)
class ToolUse:
    at: int
    tool: str
    call: int | None  # Call.id of the turn that requested it
    duration: float | None  # ms from that turn to the result, inferred


@dataclass(frozen=True)
class Step:
    id: int  # message rowid within its profile
    role: str  # "user" | "assistant" | "tool"
    at: int
    tool: str | None
    call: int | None  # Call.id of an assistant message, or of the turn a tool result answers


@dataclass(frozen=True)
class Session:
    key: str
    profile: str
    source: str
    project: str
    agent: str
    parent: str | None
    started: int
    ended: int | None
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class HermesData:
    calls: tuple[Call, ...]  # ascending by time
    by_id: dict[int, Call]
    tools: tuple[ToolUse, ...]
    sessions: dict[str, Session]
    profiles: tuple[str, ...]
    notices: tuple[str, ...]


@dataclass
class _Turn:
    mid: int
    at: int
    row: "_Usage"
    stop: str
    duration: float | None
    tools: int = 0


@dataclass(frozen=True)
class _Usage:
    model: str
    provider: str
    task: str
    calls: int
    input: int
    output: int
    cache_read: int
    cache_write: int
    reasoning: int
    cost: float
    first: int | None
    last: int | None

    @property
    def unpriced(self) -> bool:
        return self.cost == 0 and self.input + self.output + self.cache_read + self.cache_write > 0


def root_of(home: Path) -> Path:
    """The Hermes root that holds every profile, from any profile's HERMES_HOME."""
    return home.parent.parent if home.parent.name == "profiles" else home


def stores(home: Path) -> list[tuple[str, Path]]:
    """The default profile's store, then each named profile's, by name. `home` is the Hermes root."""
    found = [("default", home / "state.db")] if (home / "state.db").is_file() else []
    found += [(db.parent.name, db) for db in sorted(home.glob("profiles/*/state.db")) if db.is_file()]
    return found


def _key(*parts: object) -> str:
    return hashlib.sha1("\0".join(map(str, parts)).encode()).hexdigest()[:16]


def _id(*parts: object) -> int:
    return int(_key(*parts)[:13], 16)  # 52 bits: exact as a JS number


def _ms(value: object, upper: float) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) \
            and 0 < value <= upper:
        return int(value * 1000)
    return None


def _count(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def _project(cwd: object) -> str:
    if not isinstance(cwd, str) or not cwd.strip("/"):
        return ""
    path = Path(cwd)
    return "~" if path == Path.home() else path.name


def _pick(rows: list[_Usage], at: int) -> _Usage | None:
    """The main usage row whose window holds `at`; else the nearest window."""
    if len(rows) < 2:
        return rows[0] if rows else None

    def distance(row: _Usage) -> float:
        lo = (row.first if row.first is not None else at) - WINDOW_SLACK_MS
        hi = (row.last if row.last is not None else at) + WINDOW_SLACK_MS
        return 0 if lo <= at <= hi else min(abs(at - lo), abs(at - hi))

    return min(rows, key=lambda row: (distance(row), -(row.first or 0)))


def _load_profile(profile: str, path: Path, upper: float) -> tuple[list[Call], list[ToolUse], list[Session], int]:
    uri = path.expanduser().resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
        session_rows = db.execute("""
            SELECT id, source, model, billing_provider, parent_session_id, started_at, ended_at, cwd
            FROM sessions""").fetchall()
        usage_rows = db.execute("""
            SELECT session_id, model, billing_provider, task, api_call_count, input_tokens, output_tokens,
                   cache_read_tokens, cache_write_tokens, reasoning_tokens, estimated_cost_usd,
                   actual_cost_usd, first_seen, last_seen
            FROM session_model_usage""").fetchall()
        message_rows = db.execute("""
            SELECT session_id, id, role, timestamp, finish_reason, display_kind, tool_name,
                   CASE WHEN json_valid(display_metadata)
                        THEN json_extract(display_metadata, '$.interrupted') END,
                   message_uid
            FROM messages
            WHERE role IN ('user', 'assistant', 'tool') AND COALESCE(_compressed_summary, 0) = 0
            ORDER BY session_id, id""").fetchall()

    usage: dict[str, list[_Usage]] = defaultdict(list)
    for sid, model, provider, task, calls, i, o, cr, cw, r, est, actual, first, last in usage_rows:
        cost = actual if isinstance(actual, (int, float)) and actual > 0 else est
        cost = float(cost) if isinstance(cost, (int, float)) and math.isfinite(cost) and cost > 0 else 0.0
        usage[sid].append(_Usage(str(model or "unknown"), str(provider or "unknown"), str(task or ""),
                                 _count(calls), _count(i), _count(o), _count(cr), _count(cw), _count(r),
                                 cost, _ms(first, upper), _ms(last, upper)))

    messages: dict[str, list[tuple]] = defaultdict(list)
    skipped = 0
    # Compaction archives a row (active = 0, compacted = 1) and carries it forward as a copy with the
    # same message_uid, so one message can have several rows. The first row is the event.
    seen: set[tuple[str, str]] = set()
    for sid, mid, role, ts, finish, kind, tool, interrupted, uid in message_rows:
        if uid:
            if (sid, uid) in seen:
                continue
            seen.add((sid, uid))
        at = _ms(ts, upper)
        if at is None:
            skipped += 1
            continue
        messages[sid].append((mid, role, at, finish, kind, tool, bool(interrupted)))

    calls: list[Call] = []
    tools: list[ToolUse] = []
    sessions: list[Session] = []
    for sid, source, model, provider, parent, started, ended, cwd in session_rows:
        start = _ms(started, upper)
        if start is None:
            skipped += 1
            continue
        key = _key(profile, sid)
        project = _project(cwd)
        source = str(source or "unknown")
        agent = "subagent" if source == "subagent" else "main"
        rows = usage.get(sid, [])
        main = [row for row in rows if row.task == ""]
        fallback = _Usage(str(model or "unknown"), str(provider or "unknown"), "", 0, 0, 0, 0, 0, 0, 0.0, None, None)

        turns: list[_Turn] = []
        steps: list[Step] = []
        previous: int | None = None
        owner: _Turn | None = None  # the assistant message the next tool results answer
        for mid, role, at, finish, kind, tool, interrupted in messages.get(sid, []):
            if role == "assistant":
                gap = at - previous if previous is not None else None
                stop = ("error" if kind == "failed_turn" else "aborted" if interrupted
                        else STOP.get(finish or "", "unknown"))
                owner = _Turn(mid, at, _pick(main, at) or fallback, stop,
                              gap if gap is not None and 0 <= gap <= MAX_GAP_MS else None)
                turns.append(owner)
                steps.append(Step(mid, role, at, None, _id(profile, "m", mid)))
            elif role == "tool":
                name = str(tool or "unknown")
                gap = at - owner.at if owner is not None else None
                if owner is not None:
                    owner.tools += 1
                tools.append(ToolUse(at, name, _id(profile, "m", owner.mid) if owner else None,
                                     gap if gap is not None and 0 <= gap <= MAX_GAP_MS else None))
                steps.append(Step(mid, role, at, name, _id(profile, "m", owner.mid) if owner else None))
            else:
                owner = None
                steps.append(Step(mid, role, at, None, None))
            previous = at

        shares: dict[int, int] = defaultdict(int)
        for turn in turns:
            shares[id(turn.row)] += 1
        for turn in turns:
            row, n = turn.row, shares[id(turn.row)]
            calls.append(Call(_id(profile, "m", turn.mid), turn.at, profile, key, project, row.model,
                              row.provider, "turn", agent, 1, row.input / n, row.output / n,
                              row.cache_read / n, row.cache_write / n, row.reasoning / n, row.cost / n,
                              row.unpriced, turn.stop, turn.duration, turn.tools))
        # Usage nobody's messages can carry stays countable as one batch at its last sighting.
        for row in rows:
            if row.task == "" and shares.get(id(row)):
                continue
            at = row.last or row.first or start
            calls.append(Call(_id(profile, "u", sid, row.model, row.provider, row.task), at, profile, key,
                              project, row.model, row.provider, row.task or "unmatched", agent,
                              max(row.calls, 1), row.input, row.output, row.cache_read, row.cache_write,
                              row.reasoning, row.cost, row.unpriced, "stop", None, 0))
        end = _ms(ended, upper)
        sessions.append(Session(key, profile, source, project, agent,
                                _key(profile, parent) if parent else None, start,
                                end if end is not None and end >= start else None, tuple(steps)))
    return calls, tools, sessions, skipped


def load(found: list[tuple[str, Path]], *, now: float | None = None) -> HermesData:
    upper = (time.time() if now is None else now) + 86_400
    calls: list[Call] = []
    tools: list[ToolUse] = []
    sessions: dict[str, Session] = {}
    notices: list[str] = []
    profiles: list[str] = []
    for profile, path in found:
        try:
            c, t, s, skipped = _load_profile(profile, path, upper)
        except sqlite3.Error:
            notices.append(f"Profile {profile}: session store unreadable or on an unsupported schema.")
            continue
        profiles.append(profile)
        calls += c
        tools += t
        sessions.update((session.key, session) for session in s)
        if skipped:
            notices.append(f"Profile {profile}: {skipped} rows with invalid timestamps skipped.")
    calls.sort(key=lambda c: (c.at, c.id))
    tools.sort(key=lambda t: t.at)
    return HermesData(tuple(calls), {c.id: c for c in calls}, tuple(tools), sessions, tuple(profiles),
                      tuple(notices))
