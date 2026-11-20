"""Deterministic findings: what to change tomorrow, from the same read models as the dashboard.

Each detector reads session- or (session, model, task)-level totals, never a single request's
share (those are even splits, see stats_hermes). It returns findings that carry their evidence
numbers, an action naming a real Hermes setting where one exists, the addressable API-equivalent
cost (never a promised saving) and a deep link. Every detector also reports a check: fired,
quiet, or needs-data with what it is waiting for, so a fresh install shows why it is silent.

Pure functions of (HermesData, VoiceData, Window, now).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Optional

from .stats_api import DAY_MS, Window, calls as voice_calls, _group, _in
from .stats_hermes import Call, HermesData
from .stats_narrate import format_value
from .stats_voice import VoiceData

Json = dict

# Materiality: a cost finding must hold at least this share of the window's API-equivalent cost.
MIN_COST_SHARE = 0.01
# Side tasks Hermes routes through `auxiliary.<task>` in config.yaml ("auto" = the main model):
# (label, what to set). Order is the order the action lists them.
SIDE_TASKS = {
    "compression": ("Compaction", "auxiliary.compression.model (it re-reads the whole conversation; check the "
                    "summaries still hold up)"),
    "approval": ("Smart approvals", "auxiliary.approval.model (a yes/no classifier; Hermes recommends a small "
                 "model)"),
    "background_review": ("Background review", "auxiliary.background_review.model (on the main model it reuses "
                          "the prompt cache, so compare a week before and after)"),
    "title_generation": ("Titles", "auxiliary.title_generation.prefer_fast_model: true"),
}
SIDE_MIN_CALLS = 5
RUNAWAY_MIN_FAMILIES = 10
RUNAWAY_SHARE = 0.05
RUNAWAY_MEDIAN_X = 10
RUNAWAY_MAX = 3
HEAVY_CALL_X = 3  # a session's cost per call over this multiple of the typical session's: a heavy context
FANOUT_MIN_SESSIONS = 20
FANOUT_SHARE = 0.05
FAIL_MIN_TURNS = 20
FAIL_MIN_EVENTS = 3
FAIL_RATE = 0.02
LANE_MIN_RUNS = 50
LANE_MIN_ATTEMPTS = 20
REACTIONS_MIN_TURNS = 100
COMPARE_MIN_DAYS = 14


# ---------------------------------------------------------------- domain

@dataclass(frozen=True)
class Metric:
    """One evidence number. `key` names it inside its finding: the LLM cites it as {id.key}."""
    key: str
    label: str
    value: float
    unit: str  # "usd" (API-equivalent) | "pct" | "count" | "s" | "h"


@dataclass(frozen=True)
class Finding:
    id: str  # stable: detector plus subject
    detector: str
    severity: str  # "high" | "medium" | "low"
    title: str
    evidence: tuple[Metric, ...]
    action: str
    impact: Optional[Metric]  # addressable API-equivalent cost, or None when only counts are honest
    link: str  # dashboard hash route


@dataclass(frozen=True)
class Check:
    detector: str
    label: str
    status: str  # "fired" | "quiet" | "needs-data"
    note: str


def _round(value: float, unit: str) -> float:
    return round(value, 2 if unit == "usd" else 1 if unit in ("pct", "s", "h") else 0)


def metric(key: str, label: str, value: float, unit: str) -> Metric:
    """Values are rounded once, here, to what the page shows; the narration cites the same figures."""
    return Metric(key, label, _round(value, unit), unit)


def fmt(m: Metric) -> str:
    return format_value(m.value, m.unit)


def _severity(share: float) -> str:
    return "high" if share >= 0.10 else "medium" if share >= 0.03 else "low"


def _pct(part: float, whole: float) -> float:
    return 100 * part / whole if whole else 0.0


@dataclass(frozen=True)
class Context:
    data: HermesData
    voice: VoiceData
    w: Window
    range: str
    calls: tuple[Call, ...]  # in window
    cost: float  # window API-equivalent total

    def link(self, section: str, extra: str = "") -> str:
        return f"#/{section}?range={self.range}{extra}"


# ---------------------------------------------------------------- detectors

def side_tasks(ctx: Context) -> tuple[list[Finding], Check]:
    """Side tasks (compaction, approvals, titles, review) that ran on the profile's main-loop model."""
    main: dict[str, str] = {}
    for profile, group in _group((c for c in ctx.calls if c.kind == "turn"), lambda c: c.profile).items():
        main[profile] = Counter({m: sum(c.calls for c in g) for m, g in _group(group, lambda c: c.model).items()}) \
            .most_common(1)[0][0]
    # Priced calls only: a task on an included subscription costs nothing to move.
    on_main = [c for c in ctx.calls if c.kind in SIDE_TASKS and c.cost > 0 and c.model == main.get(c.profile)]
    tasks = {task: g for task, g in _group(on_main, lambda c: c.kind).items()
             if sum(c.calls for c in g) >= SIDE_MIN_CALLS}
    group = [c for g in tasks.values() for c in g]
    cost = sum(c.cost for c in group)
    label = "Side tasks on the main model"
    if not tasks or cost < MIN_COST_SHARE * ctx.cost:
        return [], Check("side-tasks", label, "quiet",
                         f"Side tasks on the main model cost under {100 * MIN_COST_SHARE:.0f}% of the window.")
    ordered = [t for t in SIDE_TASKS if t in tasks]
    by_model = Counter({m: sum(c.cost for c in g) for m, g in _group(group, lambda c: c.model).items()})
    by_profile = Counter({p: sum(c.cost for c in g) for p, g in _group(group, lambda c: c.profile).items()})
    # Name the profiles that carry the cost; a profile's own config.yaml holds its auxiliary block.
    profiles = [p for p, v in by_profile.most_common() if v >= 0.1 * cost] or [by_profile.most_common(1)[0][0]]
    if len(profiles) < len(by_profile):
        profiles.append(f"{len(by_profile) - len(profiles)} more")
    share = cost / ctx.cost
    per_task = tuple(metric(task, f"{SIDE_TASKS[task][0]} ({sum(c.calls for c in tasks[task]):,} calls)",
                            sum(c.cost for c in tasks[task]), "usd") for task in ordered)
    finding = Finding(
        "side-tasks", "side-tasks", _severity(share),
        f"Side tasks run on your main model {by_model.most_common(1)[0][0]}",
        (metric("cost", "API-equivalent cost", cost, "usd"), metric("share", "Share of the window's cost", 100 * share,
                                                                    "pct"),
         metric("calls", "Side-task calls", sum(c.calls for c in group), "count"), *per_task),
        f"Point them at a smaller model in config.yaml ({', '.join(profiles)}): "
        + "; ".join(SIDE_TASKS[t][1] for t in ordered) + ". Set each task's provider with its model.",
        metric("addressable", "Addressable API-equivalent cost", cost, "usd"),
        ctx.link("overview"))
    return [finding], Check("side-tasks", label, "fired",
                            f"{len(ordered)} side task(s) on {', '.join(sorted(by_model))}.")


def _families(ctx: Context) -> list[tuple[str, list[Call], int]]:
    """(root session key, its and its subagents' calls in window, active subagents) per family,
    the same grouping as the Traces page."""
    sessions = ctx.data.sessions

    def root(key: str) -> str:
        s = sessions.get(key)
        return s.parent if s is not None and s.agent == "subagent" and s.parent in sessions else key

    families = _group(ctx.calls, lambda c: root(c.session))
    return [(key, group, len({c.session for c in group if c.session != key})) for key, group in families.items()]


def _per_call(group: list[Call]) -> float:
    turns = [c for c in group if c.kind == "turn"]
    n = sum(c.calls for c in turns)
    return sum(c.cost for c in turns) / n if n else 0.0


def big_sessions(ctx: Context) -> tuple[list[Finding], Check]:
    """The window's biggest sessions (with their subagents), when a measured cause makes part of the cost
    addressable. A session that is big only because it did a lot of work at a typical cost per call is
    named in the check note, not raised as a finding: splitting it would save nothing."""
    label = "Biggest sessions"
    priced = [(k, g, subs, sum(c.cost for c in g)) for k, g, subs in _families(ctx)]
    priced = [f for f in priced if f[3] > 0]
    if len(priced) < RUNAWAY_MIN_FAMILIES:
        return [], Check("big-sessions", label, "needs-data",
                         f"Needs {RUNAWAY_MIN_FAMILIES} priced sessions in the window; has {len(priced)}.")
    typical = median(f[3] for f in priced)
    typical_call = median(x for f in priced if (x := _per_call(f[1])) > 0)
    findings, plain = [], []
    for key, group, subs, cost in sorted(priced, key=lambda f: -f[3]):
        if len(findings) + len(plain) == RUNAWAY_MAX or cost < RUNAWAY_SHARE * ctx.cost \
                or cost < RUNAWAY_MEDIAN_X * typical:
            break
        turns = [c for c in group if c.kind == "turn"]
        calls = sum(c.calls for c in turns)
        model = Counter(c.model for c in turns).most_common(1)[0][0] if turns else group[0].model
        profile = ctx.data.sessions[key].profile if key in ctx.data.sessions else group[0].profile
        hours = (max(c.at for c in group) - min(c.at for c in group)) / 3_600_000
        compactions = [c for c in group if c.kind == "compression"]
        n_compactions = sum(c.calls for c in compactions)
        sub_cost = sum(c.cost for c in group if c.session != key)
        call_cost = _per_call(group)
        # The most specific measured cause, its addressable cost, and the setting that addresses it.
        if subs >= 5 and sub_cost >= cost / 3:
            title = f"A {profile} session spent {fmt(metric('', '', sub_cost, 'usd'))} on {subs} subagents"
            addressable = metric("addressable", "Subagent API-equivalent cost", sub_cost, "usd")
            action = (f"Set delegation.model and delegation.provider in config.yaml so fan-out work runs on a "
                      f"cheaper model than {model}; empty means subagents inherit it.")
        elif call_cost >= HEAVY_CALL_X * typical_call:
            title = f"A {profile} session paid {call_cost / typical_call:.0f}x the typical cost per call"
            addressable = metric("addressable", "Cost above the typical per-call cost",
                                 (call_cost - typical_call) * calls, "usd")
            action = ("Its context was large on every call. Start a new session at natural breaks, or set "
                      "compression.proactive_prune_tokens (e.g. 48000) so old tool output stops riding along; "
                      "each prune breaks the prompt cache once.")
        elif n_compactions >= 3:
            title = f"A {profile} session compacted {n_compactions} times"
            addressable = metric("addressable", "Compaction API-equivalent cost",
                                 sum(c.cost for c in compactions), "usd")
            action = ("Start a new session at natural breaks instead of letting one conversation compact again "
                      "and again, and route compaction to a smaller model (auxiliary.compression.model).")
        else:
            plain.append(f"{fmt(metric('', '', cost, 'usd'))} ({100 * cost / ctx.cost:.1f}%) over {calls:,} calls "
                         f"at a typical {fmt(metric('', '', call_cost, 'usd'))} per call")
            continue
        findings.append(Finding(
            f"session-{key[:8]}", "big-sessions", _severity(addressable.value / ctx.cost), title,
            (metric("cost", "Session API-equivalent cost", cost, "usd"),
             metric("share", "Share of the window's cost", 100 * cost / ctx.cost, "pct"),
             metric("requests", "Model calls", calls, "count"),
             metric("perCall", "Cost per model call", call_cost, "usd"),
             metric("typicalCall", "Typical session's cost per call", typical_call, "usd"),
             metric("subagents", "Subagents", subs, "count"),
             metric("compactions", "Compactions", n_compactions, "count"),
             metric("hours", "First to last call", hours, "h")),
            action, addressable, f"#/traces?range={ctx.range}&s={key}"))
    note = (f"Typical session {fmt(metric('', '', typical, 'usd'))}, {fmt(metric('', '', typical_call, 'usd'))} per call."
            + (f" Big but ordinary: {'; '.join(plain)}." if plain else ""))
    return findings, Check("big-sessions", label, "fired" if findings else "quiet", note)


def subagent_fanout(ctx: Context) -> tuple[list[Finding], Check]:
    label = "Subagent model"
    subs = [c for c in ctx.calls if c.agent == "subagent"]
    sessions = {c.session for c in subs}
    if len(sessions) < FANOUT_MIN_SESSIONS:
        return [], Check("subagent-model", label, "needs-data" if not subs else "quiet",
                         f"Needs {FANOUT_MIN_SESSIONS} subagent sessions in the window; has {len(sessions)}.")
    cost = sum(c.cost for c in subs)
    main = [c for c in ctx.calls if c.agent == "main" and c.kind == "turn"]
    top_main = Counter({m: sum(c.calls for c in g) for m, g in _group(main, lambda c: c.model).items()}).most_common(1)
    top_sub = Counter({m: sum(c.calls for c in g) for m, g in _group(subs, lambda c: c.model).items()}).most_common(1)
    if not top_main or not top_sub or top_main[0][0] != top_sub[0][0] or cost < FANOUT_SHARE * ctx.cost:
        return [], Check("subagent-model", label, "quiet",
                         "Subagents run on a different model than the main loop, or cost under "
                         f"{100 * FANOUT_SHARE:.0f}% of the window.")
    model = top_sub[0][0]
    share = cost / ctx.cost
    finding = Finding(
        "subagent-model", "subagent-model", _severity(share),
        f"Subagents inherit your main model {model}",
        (metric("sessions", "Subagent sessions", len(sessions), "count"),
         metric("calls", "Subagent model calls", sum(c.calls for c in subs), "count"),
         metric("cost", "API-equivalent cost", cost, "usd"),
         metric("share", "Share of the window's cost", 100 * share, "pct")),
        "Set delegation.model and delegation.provider in config.yaml to a cheaper model for delegated work "
        "(empty means subagents inherit the parent's model). Keep the main loop as it is.",
        metric("addressable", "Addressable API-equivalent cost", cost, "usd"),
        ctx.link("overview"))
    return [finding], Check("subagent-model", label, "fired", f"{len(sessions)} subagent sessions on {model}.")


def failed_turns(ctx: Context) -> tuple[list[Finding], Check]:
    label = "Failed and truncated turns"
    turns = [c for c in ctx.calls if c.kind == "turn"]
    findings = []
    for (model, provider), group in sorted(_group(turns, lambda c: (c.model, c.provider)).items()):
        for stop, what, action in (
                ("error", "failed", "Open Errors for this model: failures bunched in time point at the provider; "
                                    "spread out, add a fallback_providers entry in config.yaml."),
                ("length", "stopped at the output limit", "Raise this model's output token limit or trim what "
                                                          "goes into its context.")):
            events = sum(1 for c in group if c.stop == stop)
            if len(group) < FAIL_MIN_TURNS or events < FAIL_MIN_EVENTS or events < FAIL_RATE * len(group):
                continue
            rate = events / len(group)
            findings.append(Finding(
                f"turns-{stop}-{model}-{provider}", "failed-turns", "high" if rate >= 0.1 else "medium",
                f"{events} turns on {model} {what}",
                (metric("events", f"Turns that {what}", events, "count"),
                 metric("turns", "Turns on this model", len(group), "count"),
                 metric("rate", "Rate", 100 * rate, "pct")),
                action, None, ctx.link("errors")))
    total = sum(1 for c in turns if c.stop in ("error", "length"))
    return findings, Check("failed-turns", label, "fired" if findings else "quiet",
                           f"{total} failed or truncated turns of {len(turns)}; flags a model at "
                           f"{FAIL_MIN_EVENTS}+ events and {100 * FAIL_RATE:.0f}%+.")


def voice_lanes(ctx: Context) -> tuple[list[Finding], Check]:
    label = "Voice hand-off lanes"
    payload = voice_calls(ctx.voice, ctx.w)
    if not payload["overall"]["handoffs"]:
        return [], Check("voice-lanes", label, "needs-data", "No z0live hand-offs in the window.")
    findings = []
    for lane in payload["lanes"]:
        if lane["runs"] < LANE_MIN_RUNS:
            continue
        tally: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # wins, skips, failures
        for i, field in enumerate(("wins", "skips", "failures")):
            for row in lane[field]:
                tally[row["model"]][i] += row["count"]
        rates = {m: (t[0] / sum(t), t) for m, t in tally.items() if sum(t) >= LANE_MIN_ATTEMPTS}
        if len(rates) < 2:
            continue
        best = max(rates, key=lambda m: rates[m][0])
        for model, (rate, (wins, skips, fails)) in sorted(rates.items()):
            if model == best or rate >= rates[best][0] / 2:
                continue
            attempts = wins + skips + fails
            best_wins, best_attempts = rates[best][1][0], sum(rates[best][1])
            findings.append(Finding(
                f"lane-{lane['tier']}-{model}", "voice-lanes", "medium",
                f"{model} rarely answers in the {lane['tier']} lane",
                (metric("answered", "Answers it gave", wins, "count"),
                 metric("attempts", "Hand-offs it was in", attempts, "count"),
                 metric("declined", "Declined (SKIP or empty)", skips, "count"),
                 metric("errors", "Errors", fails, "count"),
                 metric("rate", "Answer rate", 100 * rate, "pct"),
                 metric("mateRate", f"{best} answer rate", 100 * rates[best][0], "pct"),
                 metric("mateAnswered", f"Answers {best} gave", best_wins, "count"),
                 metric("mateAttempts", f"Hand-offs {best} was in", best_attempts, "count")),
                f"Review the {lane['tier']} lane in z0live: {model} declines most hand-offs that {best} answers. "
                f"Replace or drop it if the declines are not by design. Lane token cost is not recorded.",
                None, ctx.link("calls")))
    return findings, Check("voice-lanes", label, "fired" if findings else "quiet",
                           f"{payload['overall']['handoffs']} hand-offs; flags a lane model answering under half "
                           f"as often as its tier-mate ({LANE_MIN_ATTEMPTS}+ attempts).")


DETECTORS = (side_tasks, big_sessions, subagent_fanout, failed_turns, voice_lanes)


# ---------------------------------------------------------------- report

def _coverage(ctx: Context, history_days: float) -> list[Check]:
    """Signals we cannot judge yet, said out loud instead of left blank."""
    checks = []
    if history_days < COMPARE_MIN_DAYS:
        checks.append(Check("period-compare", "Week-over-week changes", "needs-data",
                            f"Needs {COMPARE_MIN_DAYS} days of history to compare periods; has {history_days:.1f}."))
    measured = sum(1 for c in ctx.voice.calls for t in c.turns if ctx.w.has(t.at) and t.reaction is not None)
    if any(ctx.w.has(t.at) for c in ctx.voice.calls for t in c.turns):
        checks.append(Check("voice-reactions", "Voice frustration", "needs-data" if measured < REACTIONS_MIN_TURNS
                            else "quiet", f"Needs {REACTIONS_MIN_TURNS} turns with reaction flags; has {measured}."
                            if measured < REACTIONS_MIN_TURNS else f"{measured} turns with reaction flags."))
    checks.append(Check("tool-failures", "Tool failure rates", "needs-data",
                        "Hermes does not store tool success or failure; nothing to judge."))
    return checks


def report(data: HermesData, voice: VoiceData, w: Window, range_name: str, now_ms: int) -> Json:
    calls = tuple(_in(data, w))
    ctx = Context(data, voice, w, range_name, calls, sum(c.cost for c in calls))
    findings: list[Finding] = []
    checks: list[Check] = []
    for detector in DETECTORS:
        found, check = detector(ctx)
        findings += found
        checks.append(check)
    first = data.calls[0].at if data.calls else now_ms
    history_days = (now_ms - first) / DAY_MS
    checks += _coverage(ctx, history_days)
    rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (rank[f.severity], -(f.impact.value if f.impact else 0)))
    unpriced = sum(c.calls for c in calls if c.unpriced)
    requests = sum(c.calls for c in calls)
    notes = []
    if unpriced:
        models = sorted({c.model for c in calls if c.unpriced})
        notes.append(f"{unpriced:,} of {requests:,} requests ({_pct(unpriced, requests):.1f}%) have no price "
                     f"({', '.join(models[:4])}); every dollar figure excludes them.")
    return {
        "range": range_name,
        "window": {"start": w.start, "end": w.end,
                   "days": round((w.end - w.start) / DAY_MS, 1) if w.start is not None else None},
        "historyDays": round(history_days, 1),
        "totals": {"requests": requests, "costUsd": round(ctx.cost, 2), "unpricedRequests": unpriced,
                   "sessions": len({c.session for c in calls})},
        "findings": [finding_json(f) for f in findings],
        "checks": [check.__dict__ for check in checks],
        "notes": notes,
    }


def finding_json(f: Finding) -> Json:
    return {"id": f.id, "detector": f.detector, "severity": f.severity, "title": f.title,
            "evidence": [m.__dict__ for m in f.evidence], "action": f.action,
            "impact": f.impact.__dict__ if f.impact else None, "link": f.link}


# ---------------------------------------------------------------- LLM payload

def llm_payload(rep: Json, data: HermesData, voice: VoiceData, w: Window) -> Json:
    """What a model may see: names, counts, costs and timings. No text, folders, titles or session keys."""
    calls = list(_in(data, w))
    cost = rep["totals"]["costUsd"]
    models = []
    for (model, provider), group in _group(calls, lambda c: (c.model, c.provider)).items():
        c_cost = sum(c.cost for c in group)
        models.append({"model": model, "provider": provider, "requests": sum(c.calls for c in group),
                       "costUsd": round(c_cost, 2), "sharePct": round(_pct(c_cost, cost), 1)})
    models.sort(key=lambda r: -r["costUsd"])
    side = [{"task": task, "model": model, "calls": sum(c.calls for c in group),
             "costUsd": round(sum(c.cost for c in group), 2)}
            for (task, model), group in _group((c for c in calls if c.kind != "turn"),
                                               lambda c: (c.kind, c.model)).items()]
    side.sort(key=lambda r: -r["costUsd"])
    payload: Json = {
        "window": {"range": rep["range"], "days": rep["window"]["days"], "historyDays": rep["historyDays"]},
        "totals": dict(rep["totals"], cachePct=round(_cache_pct(calls), 1),
                       failedTurns=sum(c.calls for c in calls if c.kind == "turn" and c.stop == "error")),
        "models": models[:8],
        "sideTasks": side[:8],
        "findings": [{k: f[k] for k in ("id", "severity", "title", "evidence", "action", "impact")}
                     for f in rep["findings"]],
        "notYetJudged": [{"check": c["label"], "note": c["note"]} for c in rep["checks"]
                         if c["status"] == "needs-data"],
        "notes": rep["notes"],
    }
    voice_payload = voice_calls(voice, w)["overall"]
    if voice_payload["handoffs"]:
        payload["voice"] = {
            "calls": voice_payload["calls"], "handoffs": voice_payload["handoffs"],
            "firstSpokenAnswerS": {k: voice_payload["firstAnswer"][k] for k in ("p50", "p90")},
            "hermesAnswerS": {k: voice_payload["hermesAnswer"][k] for k in ("p50", "p90")},
            "turnGapS": {k: voice_payload["turnGap"][k] for k in ("p50", "p90")},
        }
    return payload


def _cache_pct(calls: list[Call]) -> float:
    read = sum(c.cache_read for c in calls)
    inp = sum(c.input for c in calls)
    return _pct(read, read + inp)


# ---------------------------------------------------------------- text

def as_text(rep: Json, narrative: Optional[Json] = None, limit: int = 5) -> str:
    """`/wrapped insights` and `hermes wrapped insights`: plain text for a chat or a terminal."""
    lines = [f"Hermes insights, last {rep['range']}: {rep['totals']['requests']:,} requests, "
             f"${rep['totals']['costUsd']:,.2f} API-equivalent."]
    if narrative and narrative.get("status") == "verified":
        lines += ["", narrative["summary"]]
    findings = rep["findings"][:limit]
    lines.append("")
    if not findings:
        lines.append("Nothing to change this window.")
    for i, f in enumerate(findings, 1):
        evidence = ", ".join(f"{m['label'].lower()} {fmt(Metric(**m))}" for m in f["evidence"][:3])
        lines += [f"{i}. [{f['severity']}] {f['title']}", f"   {evidence}", f"   Do: {f['action']}"]
    waiting = [c for c in rep["checks"] if c["status"] == "needs-data"]
    if waiting:
        lines += ["", "Not judged yet: " + "; ".join(f"{c['label']} ({c['note']})" for c in waiting)]
    lines += [f"Note: {n}" for n in rep["notes"]]
    return "\n".join(lines)
