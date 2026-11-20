"""LLM narration over the insights payload, with a grounding check that fails closed.

The model sees one JSON payload: window, totals, top models, side tasks, voice timings and the
deterministic findings (see `stats_insights.llm_payload`). Never message text, prompts, titles,
folders or session keys. It answers with JSON. Every number in its text must match a number in
the payload (rounding tolerated); every finding it cites must exist. Otherwise the result is
`unverified` and the dashboard shows the deterministic findings instead.

Providers: the user's Hermes auxiliary model (`auxiliary.wrapped_insights` in config.yaml, else
Hermes's auto choice) when running inside Hermes, or any OpenAI-compatible URL (a llama-server,
Ollama, LM Studio) passed with `--llm-url`. Results are cached on disk by input hash.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Callable, Optional
import urllib.error
import urllib.request

PROMPT_VERSION = "insights-v2"
TASK = "wrapped_insights"
MAX_ACTIONS = 3
MAX_QUESTION = 500
MAX_TEXT = 2_000

Json = dict

SYSTEM = """You write the summary for a usage dashboard of Hermes, an AI agent. The user message is
JSON: a time window, totals, per-model rows, side tasks (compaction, approvals, titles, background
review), optional voice-call timings, and `findings`: problems a program already detected, each with
evidence numbers and a suggested action.

Rules:
- Cite numbers as slots and the program fills them in: {<finding id>.<evidence key>} for a finding's
  evidence (e.g. {side-tasks.cost}), {<finding id>.impact} for its impact, {totals.<key>} for totals,
  {voice.<key>.p50} or {voice.<key>.p90} for voice timings. A slot renders with its unit ($10.21,
  3.2%, 19.2h, 1.8s), so write no unit next to it. Prefer slots. You may also copy a number
  exactly as written in the input. Never compute a new number: no sums, differences, ratios,
  percentages, averages or projections. If a number you want is not in the input, say it in words.
- Dollar figures are API-equivalent estimates; call them that. For subscription users they are not
  money spent.
- There is no earlier period in the input: do not compare with last week, last month or before.
  Never mention dates.
- Recommend only actions that appear in `findings`. Do not add general advice.
- Name models, tools and providers exactly as written in the input.
- Plain, direct sentences. No greetings, no hedging, no exclamation marks.
"""

NARRATE = """Reply with JSON only, no code fence:
{"summary": "<2 to 4 sentences: what happened in this window and the single biggest lever>",
 "actions": [{"finding": "<a finding id from the input>", "text": "<one sentence: why it matters, with a slot>"}]}
Give at most 3 actions, most impactful first. If `findings` is empty, return "actions": []."""

ASK = """Answer the question below from the input only. Reply with JSON only, no code fence:
{"answer": "<1 to 4 sentences>", "findings": ["<ids of findings you used>"], "recorded": true}
These are not recorded, so answer that they are not recorded and set "recorded": false: message or
prompt text and why a turn failed; tool success or failure; time to first token; the cost or tokens
of a single request; remaining subscription quota; answer quality; anything outside the window; any
forecast.
Question: """


# ---------------------------------------------------------------- grounding

# Names that carry digits (claude-opus-5-5, gpt-6.2-sol, p90) are not numbers: they are removed
# before numbers are read, but only when the payload itself holds that exact name.
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]*\d[A-Za-z0-9_.:/-]*")
_NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?:\s*(%|[kKMB](?![A-Za-z])|ms(?![A-Za-z])))?")
_SCALE = {"k": 1e3, "K": 1e3, "M": 1e6, "B": 1e9}


@dataclass(frozen=True)
class Number:
    text: str
    value: float
    tolerance: float
    ms: bool  # written in milliseconds; the payload keeps seconds


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (_strings(k) + _strings(v))]
    if isinstance(node, (list, tuple)):
        return [s for v in node for s in _strings(v)]
    return []


def _leaves(node: Any) -> list[float]:
    if isinstance(node, bool):
        return []
    if isinstance(node, (int, float)):
        return [float(node)] if math.isfinite(node) else []
    if isinstance(node, dict):
        return [x for v in node.values() for x in _leaves(v)]
    if isinstance(node, (list, tuple)):
        return [x for v in node for x in _leaves(v)]
    return []


def names(payload: Any) -> frozenset[str]:
    return frozenset(m.group(0).rstrip(".:/-") for s in _strings(payload) for m in _IDENT.finditer(s))


def numbers(text: str, known: frozenset[str]) -> list[Number]:
    """Every figure in `text`, after removing the payload's own digit-bearing names."""
    def strip(m: re.Match) -> str:
        return " " if m.group(0).rstrip(".:/-") in known else m.group(0)

    found = []
    for m in _NUMBER.finditer(_IDENT.sub(strip, text)):
        whole, frac, suffix = m.group(1), m.group(2) or "", m.group(3) or ""
        scale = _SCALE.get(suffix, 1.0)
        value = float(whole.replace(",", "") + ("." + frac if frac else "")) * scale
        rounding = 0.5 * 10 ** -len(frac) * scale
        found.append(Number(m.group(0).strip(), value, max(rounding, abs(value) * 0.005), suffix == "ms"))
    return found


def allowed(payload: Any) -> list[float]:
    """The payload's numbers: numeric leaves plus figures written inside its strings."""
    known = names(payload)
    return sorted({abs(x) for x in _leaves(payload)}
                  | {n.value for s in _strings(payload) for n in numbers(s, known)})


def ungrounded(text: str, payload: Any) -> list[str]:
    """Figures in `text` that no payload number explains, within rounding."""
    pool = allowed(payload)
    known = names(payload)
    bad = []
    for n in numbers(text, known):
        value, tolerance = (n.value / 1000, n.tolerance / 1000) if n.ms else (n.value, n.tolerance)
        if not any(abs(value - v) <= tolerance for v in pool):
            bad.append(n.text)
    return bad


def format_value(value: float, unit: str) -> str:
    """How the dashboard writes a number: the one formatter for findings, slots and text."""
    if unit == "usd":
        return f"${value:,.2f}"
    if unit == "pct":
        return f"{value:.1f}%"
    if unit in ("s", "h"):
        return f"{value:.1f}{unit}"
    return f"{int(round(value)):,}"


# A slot with the unit the model typed around it: "${x.cost}", "{x.share}%", "{x.hours} hours". The
# value already carries its unit, so a matching one is absorbed and a conflicting one is rejected.
_SLOT = re.compile(r"(\$?)\{([A-Za-z0-9_.:-]+)\}(%|\s+(?:hours?|seconds?|percent)\b)?")
_UNIT_END = {"%": "%", "hour": "h", "hours": "h", "second": "s", "seconds": "s", "percent": "%"}
# The payload holds no earlier period, so any comparison with one is invented.
_COMPARISON = re.compile(r"\b(?:last|previous|prior) (?:week|month|period|year)\b|\bweek[- ]over[- ]week\b"
                         r"|\bthan (?:before|usual)\b", re.IGNORECASE)


def slots(payload: Json) -> dict[str, str]:
    """{finding.key}, {finding.impact}, {totals.key} and {voice.key.p50}, formatted for display."""
    table: dict[str, str] = {}
    for f in payload.get("findings", []):
        for m in f["evidence"]:
            table[f"{f['id']}.{m['key']}"] = format_value(m["value"], m["unit"])
        if f.get("impact"):
            table[f"{f['id']}.impact"] = format_value(f["impact"]["value"], f["impact"]["unit"])
    for key, value in payload.get("totals", {}).items():
        unit = "usd" if key.endswith("Usd") else "pct" if key.endswith("Pct") else "count"
        table[f"totals.{key}"] = format_value(value, unit)
    for key, value in payload.get("voice", {}).items():
        if isinstance(value, dict):
            table.update({f"voice.{key}.{q}": format_value(x, "s") for q, x in value.items() if x is not None})
        else:
            table[f"voice.{key}"] = format_value(value, "count")
    return table


def verify(text: str, payload: Json) -> tuple[str, list[str]]:
    """Fill slots, then list every problem: unknown slots, ungrounded figures, invented comparisons."""
    table = slots(payload)
    problems: list[str] = []

    def fill(m: re.Match) -> str:
        dollar, name, unit = m.groups()
        value = table.get(name)
        if value is None:
            problems.append("{" + name + "}")
            return " "
        if (dollar and not value.startswith("$")) or (unit and not value.endswith(_UNIT_END[unit.strip()])):
            problems.append(f"unit {m.group(0).strip()}")
        return value

    filled = _SLOT.sub(fill, text)
    problems += ungrounded(filled, payload)
    problems += [f'"{m.group(0)}"' for m in _COMPARISON.finditer(filled)]
    return filled, problems


# ---------------------------------------------------------------- providers

@dataclass(frozen=True)
class Llm:
    """One way to reach a model. `identity` names it in the cache key and the UI."""
    identity: str
    complete: Callable[[list], "tuple[str, str]"]  # messages -> (text, model that answered)


def hermes_llm() -> Optional[Llm]:
    """The user's Hermes auxiliary model, when running inside Hermes."""
    try:
        from agent.auxiliary_client import call_llm  # type: ignore
    except Exception:
        return None

    def complete(messages: list) -> tuple[str, str]:
        resp = call_llm(task=TASK, messages=messages, temperature=0.0, max_tokens=900, timeout=120)
        text = resp.choices[0].message.content or ""
        return text, str(getattr(resp, "model", "") or "auxiliary model")

    return Llm(f"hermes auxiliary ({TASK})", complete)


def url_llm(url: str, model: str = "", api_key_env: str = "") -> Llm:
    """Any OpenAI-compatible /chat/completions endpoint, e.g. a local llama-server."""
    base = url.rstrip("/")
    endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
    key = os.environ.get(api_key_env, "") if api_key_env else ""

    def complete(messages: list) -> tuple[str, str]:
        body = {"messages": messages, "temperature": 0, "max_tokens": 900, **({"model": model} if model else {})}
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
        req = urllib.request.Request(endpoint, json.dumps(body).encode(), headers, method="POST")
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"] or "", str(data.get("model") or model or base)

    return Llm(f"{endpoint}" + (f" ({model})" if model else ""), complete)


def pick_llm(url: str | None = None, model: str = "", api_key_env: str = "") -> Optional[Llm]:
    return url_llm(url, model, api_key_env) if url else hermes_llm()


# ---------------------------------------------------------------- narration

def _parse(text: str) -> Optional[Json]:
    """The outermost JSON object in the reply; tolerates a code fence or a stray sentence."""
    raw = text.strip()
    start, end = raw.find("{"), raw.rfind("}")
    try:
        parsed = json.loads(raw[start:end + 1]) if start >= 0 else None
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _sent(payload: Json) -> Json:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return {"keys": sorted(payload), "bytes": len(body.encode()), "payload": payload}


def input_hash(payload: Json, llm_identity: str, instruction: str = NARRATE) -> str:
    """Everything that shapes a reply: the prompt text itself (so editing it invalidates old replies),
    the model route, and the payload."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256("\0".join((PROMPT_VERSION, SYSTEM, instruction, llm_identity, body)).encode()).hexdigest()


class Cache:
    """Results by input hash, one small JSON file each. A `None` dir caches nothing."""

    def __init__(self, directory: Path | None) -> None:
        self.dir = directory

    def get(self, key: str) -> Optional[Json]:
        if self.dir is None:
            return None
        try:
            return json.loads((self.dir / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, key: str, value: Json) -> None:
        if self.dir is None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{key}.tmp"
        tmp.write_text(json.dumps(value), encoding="utf-8")
        os.replace(tmp, self.dir / f"{key}.json")


UNAVAILABLE = "No model configured. Run inside Hermes, or pass --llm-url for a local server."


def _reply(llm: Llm, payload: Json, instruction: str, cache: Cache, key: str,
           cached_only: bool) -> Optional[Json]:
    """The model's parsed reply for this exact input: from the cache, or one call. The cache keeps
    the raw reply, never a verdict, so the grounding check always runs with the current code. A
    failed call is not cached; asking again retries. None means not generated (cached_only)."""
    hit = cache.get(key)
    if hit is not None and isinstance(hit.get("parsed"), dict):
        return {**hit, "cached": True}
    if cached_only:
        return None
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(payload, separators=(",", ":")) + "\n\n" + instruction}]
    try:
        text, model = llm.complete(messages)
    except Exception as exc:  # any provider failure becomes a visible status, never a crash
        return {"parsed": None, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "cached": False}
    reply = {"parsed": _parse(text), "model": model or None, "generatedAt": int(time.time() * 1000)}
    if reply["parsed"] is not None:
        cache.put(key, reply)
    return {**reply, "cached": False}


def _meta(reply: Json, key: str) -> Json:
    return {"model": reply.get("model"), "generatedAt": reply.get("generatedAt"),
            "cached": reply.get("cached", False), "inputHash": key}


def _failed(reply: Json) -> Json:
    return {"status": "failed", "reason": reply.get("error") or "The model did not return the JSON asked for."}


def narrate(payload: Json, llm: Optional[Llm], cache: Cache, *, cached_only: bool = False) -> Json:
    """Summary plus up to three actions for one window. Status: verified, unverified, failed,
    unavailable or not_generated (cached_only and nothing cached)."""
    base = {"sent": _sent(payload), "provider": llm.identity if llm else None}
    if llm is None:
        return {**base, "status": "unavailable", "reason": UNAVAILABLE}
    key = input_hash(payload, llm.identity, NARRATE)
    reply = _reply(llm, payload, NARRATE, cache, key, cached_only)
    if reply is None:
        return {**base, "status": "not_generated"}
    parsed = reply["parsed"]
    return {**base, **(check_narrative(parsed, payload) if parsed is not None else _failed(reply)),
            **_meta(reply, key)}


def _unverified(problems: list[str]) -> Json:
    """Fail closed: the model's text is dropped here, so no client can show it; only what failed."""
    return {"status": "unverified", "rejected": problems,
            "reason": "The model's text cited figures or findings that are not in its input."}


def check_narrative(parsed: Json, payload: Json) -> Json:
    ids = {f["id"] for f in payload.get("findings", [])}
    summary = parsed.get("summary")
    actions = parsed.get("actions")
    if not isinstance(summary, str) or not summary.strip() or not isinstance(actions, list):
        return {"status": "failed", "reason": "The model's reply was missing a summary or actions."}
    picked = [a for a in actions[:MAX_ACTIONS] if isinstance(a, dict) and isinstance(a.get("text"), str)]
    problems = [f"finding {a.get('finding')!r}" for a in picked if a.get("finding") not in ids]
    summary, bad = verify(summary[:MAX_TEXT], payload)
    problems += bad
    filled = []
    for a in picked:
        text, bad = verify(a["text"][:MAX_TEXT], payload)
        problems += bad
        filled.append({"finding": a["finding"], "text": text})
    return _unverified(problems) if problems else {"status": "verified", "summary": summary, "actions": filled}


def ask(payload: Json, question: str, llm: Optional[Llm], cache: Cache) -> Json:
    """Answer one question over the same payload, with the same grounding check."""
    question = " ".join(question.split())[:MAX_QUESTION]
    base = {"sent": _sent(payload), "provider": llm.identity if llm else None, "question": question}
    if not question:
        return {**base, "status": "failed", "reason": "Ask a question."}
    if llm is None:
        return {**base, "status": "unavailable", "reason": UNAVAILABLE}
    instruction = ASK + json.dumps(question)
    key = input_hash(payload, llm.identity, instruction)
    reply = _reply(llm, payload, instruction, cache, key, False) or {}
    parsed = reply.get("parsed")
    if parsed is None or not isinstance(parsed.get("answer"), str):
        result: Json = _failed(reply)
    else:
        ids = {f["id"] for f in payload.get("findings", [])}
        cited = [i for i in parsed.get("findings") or [] if isinstance(i, str)]
        answer, bad = verify(parsed["answer"][:MAX_TEXT], payload)
        # A number the user typed in the question may be echoed back.
        echoed = {n.text for n in numbers(question, frozenset())}
        problems = [b for b in bad if b not in echoed] + [f"finding {i!r}" for i in cited if i not in ids]
        result = _unverified(problems) if problems else {
            "status": "verified", "answer": answer, "findings": cited,
            "recorded": parsed.get("recorded") is not False}
    return {**base, **result, **_meta(reply, key)}
