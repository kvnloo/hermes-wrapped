"""Dashboard payloads in Oh My Pi's stats API shapes, built from the Hermes and z0live read models.

Pure functions: every payload is a function of the read models, a range and the observation
time. Hermes has no time-to-first-token, cost components, premium requests or tool error flags;
those fields are null, never zero. Per-call tokens and cost are shares of the session usage row
(see stats_hermes) and durations are inferred from message gaps.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs

from .stats_hermes import Call, HermesData, Session, ToolUse
from .stats_voice import VoiceCall, VoiceData

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
RANGES: dict[str, tuple[int | None, int]] = {
    "1h": (HOUR_MS, 5 * 60_000),
    "24h": (DAY_MS, HOUR_MS),
    "7d": (7 * DAY_MS, DAY_MS),
    "30d": (30 * DAY_MS, DAY_MS),
    "90d": (90 * DAY_MS, DAY_MS),
    "all": (None, DAY_MS),
}
SIDE_LABEL = {"title_generation": "title", "background_review": "review", "unmatched": "unmatched usage"}
MAX_LIMIT = 10_000

Json = dict[str, Any]


class NotFound(LookupError):
    pass


class BadRequest(ValueError):
    pass


@dataclass(frozen=True)
class Window:
    start: int | None
    end: int
    bucket_ms: int

    def has(self, at: int) -> bool:
        return (self.start is None or at >= self.start) and at <= self.end

    def bucket(self, at: int) -> int:
        if self.bucket_ms < DAY_MS:
            return at // self.bucket_ms * self.bucket_ms
        local = datetime.fromtimestamp(at / 1000)
        return int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def window(name: str, now_ms: int) -> Window:
    if name not in RANGES:
        raise BadRequest(f"Unknown range {name!r}; use one of {', '.join(RANGES)}.")
    span, bucket = RANGES[name]
    return Window(None if span is None else now_ms - span, now_ms, bucket)


def _int(value: float) -> int:
    return int(round(value))


def _quantile(values: list[float], q: float) -> float | None:
    """trace_report.py's nearest-rank quantile, so voice numbers match z0live's own report."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _throughput(calls: Iterable[Call]) -> float | None:
    output = seconds = 0.0
    for c in calls:
        if c.duration and c.output > 0:
            output += c.output
            seconds += c.duration / 1000
    return output / seconds if seconds > 0 else None


def aggregate(calls: list[Call]) -> Json:
    """AggregatedStats over model calls."""
    total = sum(c.calls for c in calls)
    failed = sum(c.calls for c in calls if c.stop == "error")
    inp = sum(c.input for c in calls)
    read = sum(c.cache_read for c in calls)
    durations = [c.duration for c in calls if c.duration is not None]
    return {
        "totalRequests": total,
        "successfulRequests": total - failed,
        "failedRequests": failed,
        "errorRate": failed / total if total else 0,
        "totalInputTokens": _int(inp),
        "totalOutputTokens": _int(sum(c.output for c in calls)),
        "totalCacheReadTokens": _int(read),
        "totalCacheWriteTokens": _int(sum(c.cache_write for c in calls)),
        "totalReasoningTokens": _int(sum(c.reasoning for c in calls)),
        "totalToolCalls": sum(c.tools for c in calls),
        "cacheRate": read / (inp + read) if inp + read else 0,
        "cacheSavings": None,
        "totalCost": sum(c.cost for c in calls),
        "unpricedRequests": sum(c.calls for c in calls if c.unpriced),
        "totalPremiumRequests": 0,
        "avgDuration": sum(durations) / len(durations) if durations else None,
        "avgTtft": None,
        "avgTokensPerSecond": _throughput(calls),
        "firstTimestamp": calls[0].at if calls else 0,
        "lastTimestamp": calls[-1].at if calls else 0,
    }


def _totals(calls: list[Call]) -> Json:
    """UsageAttributionTotals."""
    return {
        "totalRequests": sum(c.calls for c in calls),
        "totalInputTokens": _int(sum(c.input for c in calls)),
        "totalOutputTokens": _int(sum(c.output for c in calls)),
        "totalCacheReadTokens": _int(sum(c.cache_read for c in calls)),
        "totalCacheWriteTokens": _int(sum(c.cache_write for c in calls)),
        "totalCost": sum(c.cost for c in calls),
    }


def _group(items: Iterable, key: Callable) -> dict:
    groups: dict = defaultdict(list)
    for item in items:
        groups[key(item)].append(item)
    return groups


def _in(data: HermesData, w: Window) -> list[Call]:
    return [c for c in data.calls if w.has(c.at)]


def overview(data: HermesData, w: Window) -> Json:
    calls = _in(data, w)
    turns = [c for c in calls if c.kind == "turn"]
    side = [c for c in calls if c.kind != "turn"]
    series = []
    for at, group in sorted(_group(calls, lambda c: w.bucket(c.at)).items()):
        series.append({"timestamp": at, "requests": sum(c.calls for c in group),
                       "errors": sum(c.calls for c in group if c.stop == "error"),
                       "tokens": _int(sum(c.tokens for c in group)), "cost": sum(c.cost for c in group)})
    return {
        "overall": aggregate(calls),
        "byAgentType": [{"agentType": agent, **_totals(group)}
                        for agent, group in sorted(_group(turns, lambda c: c.agent).items())],
        "bySideChannel": sorted(({"category": SIDE_LABEL.get(kind, kind), "decision": False, **_totals(group)}
                                 for kind, group in _group(side, lambda c: c.kind).items()),
                                key=lambda row: -row["totalRequests"]),
        "timeSeries": series,
        "profiles": list(data.profiles),
        "notices": list(data.notices),
    }


def models(data: HermesData, w: Window) -> Json:
    calls = _in(data, w)
    by_model = [{"model": m, "provider": p, **aggregate(group)}
                for (m, p), group in _group(calls, lambda c: (c.model, c.provider)).items()]
    series, performance = [], []
    for (at, m, p), group in sorted(_group(calls, lambda c: (w.bucket(c.at), c.model, c.provider)).items()):
        requests = sum(c.calls for c in group)
        series.append({"timestamp": at, "model": m, "provider": p, "requests": requests})
        performance.append({"timestamp": at, "model": m, "provider": p, "requests": requests,
                            "avgTtft": None, "avgTokensPerSecond": _throughput(group)})
    return {"byModel": sorted(by_model, key=lambda r: -r["totalRequests"]), "modelSeries": series,
            "modelPerformanceSeries": performance}


def costs(data: HermesData, w: Window) -> Json:
    calls = _in(data, w)
    day = Window(w.start, w.end, DAY_MS)
    series = [{"timestamp": at, "model": m, "provider": p, "cost": sum(c.cost for c in group),
               "unpricedRequests": sum(c.calls for c in group if c.unpriced),
               "requests": sum(c.calls for c in group)}
              for (at, m, p), group in sorted(_group(calls, lambda c: (day.bucket(c.at), c.model, c.provider)).items())]
    profiles = [{"profile": name, "cost": sum(c.cost for c in group), "requests": sum(c.calls for c in group),
                 "unpricedRequests": sum(c.calls for c in group if c.unpriced)}
                for name, group in _group(calls, lambda c: c.profile).items()]
    return {"costSeries": series, "byProfile": sorted(profiles, key=lambda r: -r["cost"])}


def folders(data: HermesData, w: Window) -> list[Json]:
    calls = _in(data, w)
    rows = [{"folder": name or "(no working directory)", **aggregate(group)}
            for name, group in _group(calls, lambda c: c.project).items()]
    return sorted(rows, key=lambda r: -r["totalRequests"])


def _tool_rows(uses: list[ToolUse], data: HermesData) -> Json:
    tokens = output = cost = unpriced = 0.0
    for use in uses:
        call = data.by_id.get(use.call) if use.call is not None else None
        if call is None or not call.tools:
            continue
        share = 1 / call.tools
        tokens += call.tokens * share
        output += call.output * share
        cost += call.cost * share
        unpriced += call.calls * share if call.unpriced else 0
    durations = [u.duration for u in uses if u.duration is not None]
    return {
        "calls": len(uses),
        "totalTokensShare": _int(tokens), "outputTokensShare": _int(output), "costShare": cost,
        "unpricedRequestsShare": unpriced, "lastUsed": max(u.at for u in uses),
        "p50Ms": _quantile(durations, 0.5), "p90Ms": _quantile(durations, 0.9),
    }


def tools(data: HermesData, w: Window) -> Json:
    uses = [u for u in data.tools if w.has(u.at)]

    def model_of(use: ToolUse) -> tuple[str, str]:
        call = data.by_id.get(use.call) if use.call is not None else None
        return (call.model, call.provider) if call else ("unknown", "unknown")

    by_tool = [{"tool": name, **_tool_rows(group, data)} for name, group in _group(uses, lambda u: u.tool).items()]
    by_tool_model = [{"tool": name, "model": m, "provider": p, **_tool_rows(group, data)}
                     for (name, (m, p)), group in _group(uses, lambda u: (u.tool, model_of(u))).items()]
    series = [{"timestamp": at, "tool": name, "calls": len(group)}
              for (at, name), group in sorted(_group(uses, lambda u: (w.bucket(u.at), u.tool)).items())]
    timed = [u.duration for u in uses if u.duration is not None]
    return {"byTool": sorted(by_tool, key=lambda r: -r["calls"]),
            "byToolModel": sorted(by_tool_model, key=lambda r: -r["calls"]), "series": series,
            "durations": {"samples": len(timed), "p50Ms": _quantile(timed, 0.5), "p90Ms": _quantile(timed, 0.9)}}


def providers(data: HermesData, w: Window) -> Json:
    calls = _in(data, w)
    rows = []
    for name, group in _group(calls, lambda c: c.provider).items():
        t = _totals(group)
        rows.append({
            "provider": name, "totalRequests": t["totalRequests"],
            "failedRequests": sum(c.calls for c in group if c.stop == "error"),
            "models": len({c.model for c in group}),
            **{k: t[k] for k in ("totalInputTokens", "totalOutputTokens", "totalCacheReadTokens",
                                 "totalCacheWriteTokens", "totalCost")},
            "totalTokens": _int(sum(c.tokens for c in group)),
            "unpricedRequests": sum(c.calls for c in group if c.unpriced),
            "totalPremiumRequests": 0, "avgTokensPerSecond": _throughput(group),
        })
    hourly = [{"provider": p, "hour": hour, "totalTokens": _int(sum(c.tokens for c in group)),
               "outputTokens": _int(sum(c.output for c in group)), "requests": sum(c.calls for c in group)}
              for (p, hour), group in _group(calls, lambda c: (c.provider, datetime.fromtimestamp(c.at / 1000).hour)).items()]
    series = [{"timestamp": at, "provider": p, "totalTokens": _int(sum(c.tokens for c in group)),
               "cost": sum(c.cost for c in group), "unpricedRequests": sum(c.calls for c in group if c.unpriced),
               "requests": sum(c.calls for c in group)}
              for (at, p), group in sorted(_group(calls, lambda c: (w.bucket(c.at), c.provider)).items())]
    return {"providers": sorted(rows, key=lambda r: -r["totalTokens"]), "hourly": hourly, "series": series}


def message(c: Call) -> Json:
    """MessageStats: ids, clock, model, tokens and cost. No message content exists to send."""
    return {
        "id": c.id, "sessionFile": c.session, "entryId": str(c.id), "folder": c.project or "",
        "model": c.model, "provider": c.provider, "api": c.profile, "timestamp": c.at,
        "duration": c.duration, "ttft": None, "stopReason": c.stop, "errorMessage": c.error,
        "usage": {"input": _int(c.input), "output": _int(c.output), "cacheRead": _int(c.cache_read),
                  "cacheWrite": _int(c.cache_write), "totalTokens": _int(c.tokens),
                  "premiumRequests": 0, "cost": {"total": c.cost}},
        "costUnpriced": c.unpriced,
    }


def recent(data: HermesData, limit: int) -> list[Json]:
    turns = (c for c in reversed(data.calls) if c.kind == "turn")
    return [message(c) for _, c in zip(range(limit), turns)]


def errors(data: HermesData, w: Window, limit: int) -> list[Json]:
    # omp's Errors page and error rate count stop_reason 'error' only; aborted turns stay on Requests.
    failed = (c for c in reversed(data.calls) if c.kind == "turn" and c.stop == "error" and w.has(c.at))
    return [message(c) for _, c in zip(range(limit), failed)]


def request(data: HermesData, call_id: int) -> Json:
    call = data.by_id.get(call_id)
    if call is None or call.kind != "turn":
        raise NotFound("No such request.")
    return message(call)


# ---------------------------------------------------------------- traces

def _session_calls(data: HermesData) -> dict[str, list[Call]]:
    return _group((c for c in data.calls), lambda c: c.session)


def _label(session: Session) -> str:
    return f"{session.profile} · {session.source}"


def sessions(data: HermesData, limit: int, q: str | None) -> list[Json]:
    by_session = _session_calls(data)
    children = _group((s for s in data.sessions.values() if s.parent), lambda s: s.parent)
    rows = []
    for s in data.sessions.values():
        if s.agent == "subagent" and s.parent in data.sessions:
            continue  # folded into its parent's trace
        family = [s, *children.get(s.key, [])]
        calls = [c for member in family for c in by_session.get(member.key, [])]
        ends = [x for member in family for x in (member.ended, member.steps[-1].at if member.steps else None) if x]
        rows.append({
            "file": s.key, "folder": s.project, "title": _label(s), "startedAt": s.started,
            "endedAt": max([s.started, *ends]), "requests": sum(c.calls for c in calls if c.kind == "turn"),
            "toolCalls": sum(c.tools for c in calls), "subagents": len(family) - 1,
            "totalTokens": _int(sum(c.tokens for c in calls)), "costTotal": sum(c.cost for c in calls),
            "unpricedRequests": sum(c.calls for c in calls if c.unpriced),
            "models": sorted({c.model for c in calls}),
        })
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in r["title"].lower() or needle in r["folder"].lower()
                or any(needle in m.lower() for m in r["models"])]
    rows.sort(key=lambda r: -r["endedAt"])
    return rows[:limit]


def _track(session: Session, track_id: str, parent: str | None, data: HermesData) -> Json:
    spans: list[Json] = []
    turn_start: int | None = None
    turn_end = 0
    turn_index = 0

    def close_turn() -> None:
        if turn_start is not None:
            spans.append({"id": f"{track_id}:turn{turn_index}", "kind": "turn", "start": turn_start,
                          "end": max(turn_end, turn_start), "label": f"Turn {turn_index}"})

    issued: dict[int, int] = {}
    for step in session.steps:
        if step.role == "user":
            close_turn()
            turn_index += 1
            turn_start, turn_end = step.at, step.at
            continue
        turn_end = max(turn_end, step.at)
        call = data.by_id.get(step.call) if step.call is not None else None
        if step.role == "assistant" and call is not None:
            issued[call.id] = step.at
            start = step.at - int(call.duration) if call.duration is not None else step.at
            spans.append({"id": f"{track_id}:{step.id}", "kind": "model", "start": start, "end": step.at,
                          "label": call.model, "entryId": str(step.id), "model": call.model,
                          "tokens": _int(call.tokens), "cost": call.cost, "isError": call.stop == "error",
                          "detail": f"{call.provider} · {call.stop}"})
        elif step.role == "tool":
            start = issued.get(step.call, step.at) if step.call is not None else step.at
            spans.append({"id": f"{track_id}:{step.id}", "kind": "tool", "start": start,
                          "end": max(step.at, start), "label": step.tool or "tool",
                          "entryId": str(step.id), "toolCallId": str(step.id)})
    close_turn()
    spans.sort(key=lambda s: (s["start"], s["end"]))
    model = next((s["model"] for s in spans if s["kind"] == "model"), None)
    return {"id": track_id, "parentId": parent, "label": "main" if parent is None else _label(session),
            "agent": session.source, "model": model, "file": session.key, "spans": spans, "markers": []}


def trace(data: HermesData, key: str) -> Json:
    root = data.sessions.get(key)
    if root is None:
        raise NotFound("No such session.")
    children = sorted((s for s in data.sessions.values() if s.parent == key and s.agent == "subagent"),
                      key=lambda s: s.started)
    tracks = [_track(root, "main", None, data)]
    for i, child in enumerate(children, 1):
        track = _track(child, f"subagent{i}", "main", data)
        tracks.append(track)
        if track["spans"]:
            tracks[0]["spans"].append({
                "id": f"main:subagent{i}", "kind": "subagent", "start": track["spans"][0]["start"],
                "end": max(s["end"] for s in track["spans"]), "label": track["label"],
                "childTrackId": track["id"]})
    tracks[0]["spans"].sort(key=lambda s: (s["start"], s["end"]))
    every = [s for t in tracks for s in t["spans"]]
    start = min([root.started, *(s["start"] for s in every)])
    end = max([start, *(s["end"] for s in every)])
    covered, cursor = 0, start
    for s in sorted(every, key=lambda s: s["start"]):
        if s["end"] > cursor:
            covered += s["end"] - max(s["start"], cursor)
            cursor = s["end"]
    main = tracks[0]["spans"]
    calls = [data.by_id[s_id] for t in [root, *children] for step in t.steps
             if step.role == "assistant" and (s_id := step.call) is not None and s_id in data.by_id]
    tool_spans = [s for s in every if s["kind"] == "tool"]
    tool_stats = [{"tool": name, "calls": len(group), "errors": 0,
                   "totalMs": sum(s["end"] - s["start"] for s in group),
                   "maxMs": max(s["end"] - s["start"] for s in group)}
                  for name, group in _group(tool_spans, lambda s: s["label"]).items()]
    return {
        "file": key, "title": _label(root), "cwd": root.project or None, "startedAt": start, "endedAt": end,
        "mtimeMs": end, "etag": "snapshot", "tracks": tracks,
        "summary": {
            "wallMs": end - start,
            "modelMs": sum(s["end"] - s["start"] for s in main if s["kind"] == "model"),
            "toolMs": sum(s["end"] - s["start"] for s in main if s["kind"] == "tool"),
            "idleMs": (end - start) - covered,
            "turns": sum(1 for s in main if s["kind"] == "turn"),
            "requests": len(calls), "toolCalls": len(tool_spans), "subagents": len(children),
            "totalTokens": _int(sum(c.tokens for c in calls)), "costTotal": sum(c.cost for c in calls),
            "unpricedRequests": sum(1 for c in calls if c.unpriced),
            "toolStats": sorted(tool_stats, key=lambda r: -r["totalMs"]),
        },
    }


def entry(data: HermesData, key: str, entry_id: str) -> Json:
    session = data.sessions.get(key)
    step = next((s for s in session.steps if str(s.id) == entry_id), None) if session else None
    if step is None:
        raise NotFound("No such entry.")
    call = data.by_id.get(step.call) if step.call is not None else None
    meta: Json = {"role": step.role, "timestamp": step.at}
    if step.tool:
        meta["tool"] = step.tool
    if call is not None and step.role == "assistant":
        meta.update(model=call.model, provider=call.provider, stopReason=call.stop,
                    durationMs=call.duration, toolResults=call.tools)
    return {"entry": meta}


# ---------------------------------------------------------------- voice

def _voice_in(voice: VoiceData, w: Window) -> list[VoiceCall]:
    return [c for c in voice.calls if w.has(c.started) or w.has(c.last)]


def _p(values: list[float]) -> Json:
    return {"n": len(values), "p50": _quantile(values, 0.5), "p90": _quantile(values, 0.9)}


def calls(voice: VoiceData, w: Window) -> Json:
    rows = _voice_in(voice, w)
    turns = [t for c in rows for t in c.turns if w.has(t.at)]
    handoffs = [h for c in rows for h in c.handoffs if w.has(h.at)]
    lanes = [lane for h in handoffs for lane in h.lanes]
    tiers = []
    for tier, group in _group(lanes, lambda lane: lane.tier).items():
        wins = _group((lane for lane in group if lane.model), lambda lane: lane.model)
        failures: dict[str, int] = defaultdict(int)
        skips: dict[str, int] = defaultdict(int)
        for lane in group:
            for model in lane.failed:
                failures[model] += 1
            for model in lane.skipped:
                skips[model] += 1
        tiers.append({
            "tier": tier, "runs": len(group), "answered": sum(1 for lane in group if lane.model),
            "spokenFirst": sum(1 for lane in group if lane.spoken == "first"),
            "spokenUpdate": sum(1 for lane in group if lane.spoken == "update"),
            "firstToken": _p([lane.first_s for lane in group if lane.first_s is not None]),
            "finish": _p([lane.s for lane in group]),
            "wins": sorted(({"model": m, "count": len(g)} for m, g in wins.items()), key=lambda r: -r["count"]),
            "failures": sorted(({"model": m, "count": n} for m, n in failures.items()), key=lambda r: -r["count"]),
            "skips": sorted(({"model": m, "count": n} for m, n in skips.items()), key=lambda r: -r["count"]),
        })
    order = {"fast": 0, "medium": 1, "slow": 2}
    series = defaultdict(lambda: {"calls": 0, "turns": 0, "handoffs": 0})
    for c in rows:
        if w.has(c.started):
            series[w.bucket(c.started)]["calls"] += 1
    for t in turns:
        series[w.bucket(t.at)]["turns"] += 1
    for h in handoffs:
        series[w.bucket(h.at)]["handoffs"] += 1
    return {
        "overall": {
            "calls": len(rows), "open": sum(1 for c in rows if c.reason == "open"),
            "talkSeconds": sum(c.seconds or 0 for c in rows), "userTurns": len(turns),
            "handoffs": len(handoffs),
            "answered": sum(1 for h in handoffs if h.hermes_s is not None),
            "closedBeforeAnswer": sum(1 for h in handoffs if h.closed_before_answer),
            "turnGap": _p([t.gap_s for t in turns if t.gap_s is not None]),
            "firstAnswer": _p([h.first_answer_s for h in handoffs if h.first_answer_s is not None]),
            "hermesAnswer": _p([h.hermes_s for h in handoffs if h.hermes_s is not None]),
            "setup": _p([c.setup_s for c in rows if c.setup_s is not None]),
        },
        "series": [{"timestamp": at, **v} for at, v in sorted(series.items())],
        "lanes": sorted(tiers, key=lambda r: order.get(r["tier"], 9)),
        "calls": [{
            "key": c.key, "startedAt": c.started, "seconds": c.seconds, "reason": c.reason,
            "engine": c.engine, "model": c.model, "profile": c.profile, "turns": len(c.turns),
            "handoffs": len(c.handoffs),
            "turnGapP50": _quantile([t.gap_s for t in c.turns if t.gap_s is not None], 0.5),
            "firstAnswerP50": _quantile([h.first_answer_s for h in c.handoffs if h.first_answer_s is not None], 0.5),
            "hermesAnswerP50": _quantile([h.hermes_s for h in c.handoffs if h.hermes_s is not None], 0.5),
        } for c in sorted(rows, key=lambda c: -c.started)],
        "notices": list(voice.notices),
    }


def frustration(voice: VoiceData, w: Window) -> Json:
    rows = _voice_in(voice, w)
    turns = [(c, t) for c in rows for t in c.turns if w.has(t.at)]
    flagged = [(c, t) for c, t in turns if t.reaction is not None]

    def counts(items: list) -> Json:
        reactions = [t.reaction for _, t in items if t.reaction is not None]
        return {"turns": len(items), "recorded": len(reactions),
                "corrections": sum(r.correction for r in reactions),
                "frustration": sum(r.frustration for r in reactions),
                "repeats": sum(r.repeat for r in reactions),
                "bargeIns": sum(r.barge_in for r in reactions),
                "flagged": sum(r.correction or r.frustration or r.repeat for r in reactions)}

    first = min((t.at for _, t in flagged), default=None)
    return {
        "overall": counts(turns),
        "recordedSince": first,
        "series": [{"timestamp": at, **counts(group)}
                   for at, group in sorted(_group(flagged, lambda ct: w.bucket(ct[1].at)).items())],
        "byCall": [{"key": c.key, "startedAt": c.started, "model": c.model, **counts(group)}
                   for c, group in ((c, [(c, t) for t in c.turns if w.has(t.at)]) for c in
                                    sorted(rows, key=lambda c: -c.started)) if group],
    }


# ---------------------------------------------------------------- routing

def _limit(params: dict[str, list[str]], default: int) -> int:
    raw = params.get("limit", [str(default)])[0]
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_LIMIT:
        raise BadRequest(f"limit must be 1..{MAX_LIMIT}.")
    return int(raw)


def route(path: str, query: str, data: HermesData, voice: VoiceData, now_ms: int) -> Any:
    """GET /api/... -> JSON-ready payload. Raises NotFound or BadRequest."""
    params = parse_qs(query, keep_blank_values=True)

    def w() -> Window:
        return window(params.get("range", ["24h"])[0], now_ms)

    def one(name: str) -> str:
        value = params.get(name, [""])[0]
        if not value:
            raise BadRequest(f"{name} is required.")
        return value

    routes: dict[str, Callable[[], Any]] = {
        "/api/stats/overview": lambda: overview(data, w()),
        "/api/stats/model-dashboard": lambda: models(data, w()),
        "/api/stats/costs": lambda: costs(data, w()),
        "/api/stats/folders": lambda: folders(data, w()),
        "/api/stats/tools": lambda: tools(data, w()),
        "/api/stats/providers": lambda: providers(data, w()),
        "/api/stats/recent": lambda: recent(data, _limit(params, 50)),
        "/api/stats/errors": lambda: errors(data, w(), _limit(params, 50)),
        "/api/stats/calls": lambda: calls(voice, w()),
        "/api/stats/frustration": lambda: frustration(voice, w()),
        "/api/sessions": lambda: sessions(data, _limit(params, 100), params.get("q", [None])[0]),
        "/api/session/trace": lambda: trace(data, one("file")),
        "/api/session/entry": lambda: entry(data, one("file"), one("id")),
    }
    if path in routes:
        return routes[path]()
    if path.startswith("/api/request/"):
        raw = path.removeprefix("/api/request/")
        if not raw.isdigit():
            raise BadRequest("Request ids are integers.")
        return request(data, int(raw))
    raise NotFound(f"No route {path}.")
