"""Metadata-only read model over z0live call traces (`<root>/<day>/<HHMMSS>-<call>.jsonl`).

Only allowlisted event kinds are parsed, plus provider `turn.done` lines for the committing
role; every other line (other rx/tx, audio payloads) is skipped before JSON decoding. From
parsed events only timing, flag, tier, role and model fields are kept. Transcript, request,
answer, chat and lane text never reach the model.

Metrics follow z0live's `voice-adapter/trace_report.py`:
- user turn: a provider `turn.done` with role user (the commit);
- turn gap: end of user sound -> first assistant audio, per user turn (eos_to_audio);
- first answer: delegation start -> first spoken lane answer;
- Hermes answer: `delegation.done.s`, measured on the writer's monotonic clock.
Reaction flags come from `user.reaction`, which newer writers record at each commit.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import time

KINDS = frozenset({
    "call.created", "call.negotiated", "call.closed", "audio.on", "audio.off", "user.reaction",
    "delegation.start", "delegation.done", "lanes.tier", "lanes.spoken",
})
_EV = re.compile(rb'"ev":\s*"([a-z._]+)"')
_COMMIT = re.compile(rb'"ev":\s*"rx",\s*"src":\s*"provider",\s*"type":\s*"turn\.done"')
REACTION_MATCH_S = 2.0  # user.reaction follows its commit within the same handler
MAX_FILE_BYTES = 256 * 1024 * 1024
OPEN_GRACE_S = 120  # an unclosed trace written this recently is a call in progress
MAX_WALL = 253402214400


@dataclass(frozen=True)
class Lane:
    tier: str
    model: str | None  # the model that answered; None when every model failed
    first_s: float | None  # first token, from the tier's start
    s: float  # tier finished, since the delegation's lanes began
    failed: tuple[str, ...]  # models that errored (timeouts, exceptions)
    skipped: tuple[str, ...]  # models that declined on purpose (SKIP) or returned nothing
    spoken: str | None  # "first" | "update" | None


@dataclass(frozen=True)
class Handoff:
    at: int  # ms wall
    first_answer_s: float | None
    hermes_s: float | None
    closed_before_answer: bool
    lanes: tuple[Lane, ...]


@dataclass(frozen=True)
class Reaction:
    correction: bool
    frustration: bool
    repeat: bool
    barge_in: bool


@dataclass(frozen=True)
class UserTurn:
    at: int  # ms wall of the provider's commit
    gap_s: float | None  # end of user sound -> first assistant audio
    reaction: Reaction | None  # None where the writer predates user.reaction


@dataclass(frozen=True)
class VoiceCall:
    key: str
    started: int  # ms wall
    last: int  # ms wall of the newest kept event
    seconds: float | None  # recorded length; None while open or when the close is missing
    reason: str | None  # close reason, "open", or None when the trace ended without one
    engine: str
    model: str
    profile: str
    setup_s: float | None
    turns: tuple[UserTurn, ...]
    handoffs: tuple[Handoff, ...]


@dataclass(frozen=True)
class VoiceData:
    calls: tuple[VoiceCall, ...]  # ascending by start
    notices: tuple[str, ...]


def _num(value: object, upper: float = MAX_WALL) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) \
            and 0 <= value <= upper:
        return float(value)
    return None


def _name(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def _rows(path: Path) -> tuple[list[dict], int]:
    rows, bad = [], 0
    with path.open("rb") as source:
        for line in source:
            commit = _COMMIT.search(line, 0, 200) is not None
            match = None if commit else _EV.search(line, 0, 160)
            if not commit and (not match or match.group(1).decode() not in KINDS):
                continue
            if not line.endswith(b"\n"):
                continue  # a writer's partial last line is retried on the next read
            try:
                row = json.loads(line)
            except (ValueError, RecursionError):
                bad += 1
                continue
            t, wall = _num(row.get("t") if isinstance(row, dict) else None), \
                _num(row.get("wall") if isinstance(row, dict) else None)
            if t is None or wall is None:
                bad += 1
                continue
            if commit:
                event = row.get("event")
                turn = event.get("turn") if isinstance(event, dict) else None
                if not isinstance(turn, dict) or turn.get("role") != "user":
                    continue
                row = {"ev": "turn.user", "t": t, "wall": wall}  # drop everything but the clock
            rows.append(row)
    rows.sort(key=lambda r: r["t"])
    return rows, bad


def _turn_gaps(rows: list[dict]) -> dict[float, float]:
    """trace_report.py's eos_to_audio, keyed by the commit time it was measured at."""
    mic, speaker, on = [], [], None
    for r in rows:
        if r["ev"] == "audio.on" and r.get("dir") == "input":
            on = r["t"]
        elif r["ev"] == "audio.off" and r.get("dir") == "input" and on is not None:
            mic.append(_num(r.get("at")) if _num(r.get("at")) is not None else r["t"])
            on = None
        elif r["ev"] == "audio.on" and r.get("dir") == "output":
            speaker.append(r["t"])
    gaps = {}
    for r in rows:
        if r["ev"] != "turn.user":
            continue
        commit = r["t"]
        ended = [off for off in mic if off <= commit + 0.05]
        if not ended:
            continue
        eos = ended[-1]
        audio = next((t for t in speaker if t >= eos - 0.05), None)
        if audio is not None:
            gaps[commit] = round(audio - eos, 3)
    return gaps


def _call(path: Path, rows: list[dict], now: float) -> VoiceCall | None:
    if not rows:
        return None
    created = next((r for r in rows if r["ev"] == "call.created"), None)
    closed = next((r for r in rows if r["ev"] == "call.closed"), None)
    negotiated = next((r for r in rows if r["ev"] == "call.negotiated"), None)
    base = created or rows[0]
    gaps = _turn_gaps(rows)

    commits = [r for r in rows if r["ev"] == "turn.user"]
    reactions: dict[int, Reaction] = {}
    for r in rows:
        if r["ev"] != "user.reaction":
            continue
        owner = max((i for i, c in enumerate(commits) if c["t"] <= r["t"] + 0.05), default=None,
                    key=lambda i: commits[i]["t"])
        if owner is not None and r["t"] - commits[owner]["t"] <= REACTION_MATCH_S:
            reactions.setdefault(owner, Reaction(
                r.get("correction") is True, r.get("frustration") is True,
                r.get("repeat") is True, r.get("barge_in") is True))
    turns = tuple(UserTurn(int(c["wall"] * 1000), gaps.get(c["t"]), reactions.get(i))
                  for i, c in enumerate(commits))

    starts: dict[str, dict] = {}
    done: dict[str, dict] = {}
    first_spoken: dict[str, float] = {}
    spoken: dict[tuple[str, str], str] = {}
    tiers: dict[str, list[dict]] = {}
    for r in rows:
        did = _name(r.get("did"))
        if did is None:
            continue
        kind = r["ev"]
        if kind == "delegation.start":
            starts.setdefault(did, r)
        elif kind == "delegation.done":
            done.setdefault(did, r)
        elif kind == "lanes.tier":
            tiers.setdefault(did, []).append(r)
        elif kind == "lanes.spoken":
            tier = _name(r.get("tier")) or "unknown"
            first = r.get("first") is True
            spoken.setdefault((did, tier), "first" if first else "update")
            if first:
                first_spoken.setdefault(did, r["t"])

    handoffs = []
    for did, start in starts.items():
        lanes = []
        for r in tiers.get(did, []):
            tier = _name(r.get("tier")) or "unknown"
            errors = r.get("errors")
            errors = errors if isinstance(errors, dict) else {}
            declined = {k for k, v in errors.items() if v in ("SKIP", "empty")}
            failed = tuple(sorted(k for k in errors if _name(k) and k not in declined))
            skipped = tuple(sorted(k for k in declined if _name(k)))
            s = _num(r.get("s"))
            if s is None:
                continue
            lanes.append(Lane(tier, _name(r.get("model")), _num(r.get("first_s")), s, failed, skipped,
                              spoken.get((did, tier))))
        finish = done.get(did)
        first = first_spoken.get(did)
        handoffs.append(Handoff(
            int(start["wall"] * 1000),
            round(first - start["t"], 3) if first is not None and first >= start["t"] else None,
            _num(finish.get("s")) if finish else None,
            bool(finish and finish.get("closed") is True),
            tuple(lanes)))

    last_wall = max(r["wall"] for r in rows)
    if closed is not None:
        seconds, reason = _num(closed.get("seconds")), _name(closed.get("reason")) or "closed"
    elif now - last_wall <= OPEN_GRACE_S:
        seconds, reason = None, "open"
    else:
        seconds, reason = None, None
    setup = _num(negotiated.get("setup_s")) if negotiated else None
    return VoiceCall(
        hashlib.sha1(path.name.encode()).hexdigest()[:16], int(base["wall"] * 1000),
        int(last_wall * 1000), seconds, reason,
        _name(base.get("engine")) or "unknown", _name(base.get("model")) or "unknown",
        _name(base.get("profile")) or "unknown", setup, turns,
        tuple(sorted(handoffs, key=lambda h: h.at)))


def load(root: Path, *, now: float | None = None) -> VoiceData:
    now = time.time() if now is None else now
    calls, notices = [], []
    if not root.is_dir():
        return VoiceData((), ("z0live trace directory not found.",))
    bad = unreadable = oversized = 0
    for path in sorted(root.glob("*/*.jsonl")):
        if path.is_symlink() or path.parent.is_symlink():
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                oversized += 1
                continue
            rows, malformed = _rows(path)
        except OSError:
            unreadable += 1  # a concurrent rotation may remove an entry
            continue
        bad += malformed
        call = _call(path, rows, now)
        if call is not None:
            calls.append(call)
    if bad:
        notices.append(f"{bad} malformed z0live trace records skipped.")
    if unreadable:
        notices.append(f"{unreadable} z0live trace files were not readable.")
    if oversized:
        notices.append(f"{oversized} z0live trace files over {MAX_FILE_BYTES >> 20} MB skipped.")
    calls.sort(key=lambda c: c.started)
    return VoiceData(tuple(calls), tuple(notices))
