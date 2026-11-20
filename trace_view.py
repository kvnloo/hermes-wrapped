"""Render and publish a metadata-only event timeline, without a server of its own."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
import time

from .event_trace import TraceEvent, TraceSnapshot, TraceSpan, load_trace
from .stats_view import STYLE
from .store import hermes_home

REFRESH_SECONDS = 15
TRACE_STYLE = """
.trace-controls { display: flex; flex-wrap: wrap; align-items: center; gap: .7rem; margin: 1.5rem 0; }
.trace-controls label { border: 1px solid var(--line); padding: .45rem .85rem; border-radius: 2rem; }
.trace-controls input { accent-color: var(--gold); }
.trace-controls .note { margin-inline-start: auto; }
.trace-controls:has(#only-voice:checked) ~ #frame .hermes,
.trace-controls:has(#only-hermes:checked) ~ #frame .voice { display: none; }
.timeline { padding: 0; overflow: hidden; }
.axis.trace-axis { padding: .8rem 1rem; border-bottom: 1px solid var(--line); }
.lane { margin: 0; border-bottom: 1px solid var(--line); padding: .8rem 1rem; }
.lane:last-child { border-bottom: 0; }
.lane summary { color: var(--text); }
.lane summary::marker { color: var(--gold); }
.lane-label { font: .85rem var(--mono); }
.lane-meta { color: var(--muted); font: .78rem var(--mono); margin-inline-start: .65rem; }
.voice { --signal: #82d9ca; }
.hermes { --signal: var(--gold); }
.track { position: relative; display: block; height: .7rem; margin: .45rem 0 0 .9rem;
         border-radius: 2px; background: repeating-linear-gradient(to right, var(--line) 0 1px,
         transparent 1px 25%); box-shadow: inset 0 0 0 1px var(--line); }
.mark { position: absolute; inset: .16rem auto .16rem min(var(--left), calc(100% - 3px));
        width: max(3px, var(--width)); max-width: calc(100% - min(var(--left), calc(100% - 3px)));
        border-radius: 2px; background: var(--signal); }
.tick { inset-block: .02rem; width: 2px; min-width: 0; background: var(--text); opacity: .75; }
.time-row { text-align: left; font: .8rem var(--mono); }
.lane table th, .lane table td { text-align: left; }
.empty { padding: .4rem 1rem; }
.watch-note { display: none; }
html[data-watch] .watch-note { display: inline; }
"""

REFRESH = f"""
async function refresh() {{
  const frame = document.querySelector('#frame');
  const canUpdate = () => !document.hidden && !document.querySelector('#hold').checked
    && !frame.querySelector('details[open]') && !frame.contains(document.activeElement);
  try {{
    if (!canUpdate()) return;
    const response = await fetch(location.href, {{cache: 'no-store', redirect: 'error'}});
    if (!response.ok || !canUpdate()) return;
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    if (!canUpdate()) return;
    const next = doc.querySelector('#frame');
    if (next) frame.replaceWith(next);
  }} catch {{ /* The last observed time stays visible during a failed fetch. */ }}
  finally {{ setTimeout(refresh, {REFRESH_SECONDS * 1000}); }}
}}
setTimeout(refresh, {REFRESH_SECONDS * 1000});
"""


def _time(value: float | None, fmt: str = "%b %d %H:%M:%S") -> str:
    if value is None:
        return "Unknown"
    d = datetime.fromtimestamp(value, timezone.utc)
    return f'<time datetime="{d.isoformat()}">{d:{fmt}}</time>'


def _mark(snapshot: TraceSnapshot, start: float | None, end: float | None,
          tick: bool = False) -> str:
    point = start if start is not None else end
    if point is None or (end is None and point < snapshot.start):
        return ""
    # Wall time places marks. An inverted wall clock is a point, not a negative bar.
    width = snapshot.as_of - snapshot.start
    left = min(1.0, max(0.0, (point - snapshot.start) / width))
    right = left if end is None else min(1.0, max(left, (end - snapshot.start) / width))
    classes = "mark tick" if tick else "mark"
    return f'<span class="{classes}" style="--left:{left:.4%};--width:{right-left:.4%}"></span>'


def _duration(span: TraceSpan, origin: str) -> str:
    if span.seconds is None:
        return "Duration unknown"
    basis = "measured elapsed" if origin == "Voice" else "inferred from wall time"
    return f"{span.seconds:,.2f} s · {basis}"


def _row(row: TraceEvent | TraceSpan, origin: str) -> str:
    if isinstance(row, TraceEvent):
        start, end, timing = row.wall, None, f"+{row.elapsed:,.3f} s · measured since trace opened"
        state = "Event"
    else:
        start, end, timing, state = row.start, row.end, _duration(row, origin), row.state
    return (f'<tr><th scope="row">{row.kind}</th><td>{_time(start)}</td>'
            f'<td>{"—" if isinstance(row, TraceEvent) else _time(end)}</td>'
            f'<td>{state}</td><td>{timing}</td></tr>')


def render_trace(snapshot: TraceSnapshot, *, watch: bool = False) -> str:
    rows = []
    totals: Counter[str] = Counter()
    for lane in snapshot.lanes:
        totals[lane.origin] += 1
        span = lane.span
        chart = _mark(snapshot, span.start, span.end)
        chart += "".join(_mark(snapshot, row.wall, None, tick=True) if isinstance(row, TraceEvent)
                         else _mark(snapshot, row.start, row.end, tick=True) for row in lane.details)
        table = "".join(_row(row, lane.origin) for row in (span, *lane.details))
        rows.append(f'''<details class="lane {lane.origin.lower()}">
<summary><span class="lane-label">{lane.origin} {totals[lane.origin]} · {span.kind}</span>
<span class="lane-meta">{span.state} · {_duration(span, lane.origin)}</span>
<span class="track" aria-hidden="true">{chart}</span></summary>
<div class="scroll" tabindex="0" role="region" aria-label="{lane.origin} {totals[lane.origin]} event details">
<table><thead><tr><th scope="col">Event or span</th><th scope="col">Start (UTC)</th>
<th scope="col">End (UTC)</th><th scope="col">State</th><th scope="col">Timing</th></tr></thead>
<tbody>{table}</tbody></table></div></details>''')
    notices = "".join(f'<p class="note">{notice}</p>' for notice in snapshot.notices)
    digest = base64.b64encode(hashlib.sha256(REFRESH.encode()).digest()).decode()
    csp = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    if watch:
        csp += f"; script-src 'sha256-{digest}'; connect-src 'self'"
    return f'''<!doctype html>
<html lang="en"{' data-watch' if watch else ''}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<title>Event timeline · Hermes Wrapped</title><style>{STYLE}{TRACE_STYLE}</style></head>
<body><header><div class="shell mast"><span class="brand">Hermes / Wrapped</span>
<span class="tag">{'Live refresh' if watch else 'Local snapshot'} · Metadata only</span></div></header>
<main class="shell"><div class="eyebrow">02 / Traces</div><h1>Event timeline</h1>
<p class="sub">Hermes session boundaries and z0live voice events on a shared UTC axis. Lanes are not linked.</p>
<div class="trace-controls" role="group" aria-label="Timeline controls">
<label><input type="radio" name="source" checked> Both</label>
<label><input type="radio" name="source" id="only-hermes"> Hermes</label>
<label><input type="radio" name="source" id="only-voice"> Voice</label>
<label class="watch-note"><input type="checkbox" id="hold"> Pause refresh</label></div>
<div id="frame"><p class="note">Observed {_time(snapshot.as_of)} UTC · Measured in this view:
{totals['Hermes']} Hermes records · {totals['Voice']} voice traces</p>{notices}
<section class="timeline" aria-label="Event waterfall, latest lanes first">
<div class="axis trace-axis"><span>{_time(snapshot.start)} UTC</span><span>{_time(snapshot.as_of)} UTC</span></div>
{''.join(rows) if rows else '<p class="empty note">No trace metadata in this window.</p>'}</section></div>
<p class="note">Select a lane for exact recorded times. Voice durations use the trace’s monotonic clock.
Hermes durations are inferred from session boundaries, not time spent working.
A short mark can be a single event or a span with a missing boundary.</p>
{'<p class="note">Updates pause while a lane is open. Leave it closed to resume.</p>' if watch else ''}
</main>{f'<script>{REFRESH}</script>' if watch else ''}</body></html>'''


def publish_trace(output: Path, snapshot: TraceSnapshot, *, watch: bool = False) -> None:
    output = output.expanduser()
    draft = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as file:
            draft = Path(file.name)
            file.write(render_trace(snapshot, watch=watch))
        draft.replace(output)
    finally:
        if draft is not None:
            draft.unlink(missing_ok=True)


def setup_cli(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", type=Path, required=True, help="Write to this HTML file")
    parser.add_argument("--db", type=Path, help="Session database (default: active profile)")
    parser.add_argument("--voice-dir", type=Path, default=Path.home() / ".local/share/z0live/voice-traces")
    parser.add_argument("--hours", type=int, default=24, help="Rolling UTC window, 1–168 hours")
    parser.add_argument("--watch", action="store_true", help="Refresh while this command runs; serve via a trusted local host")


def run_cli(args: argparse.Namespace) -> int:
    db = args.db or hermes_home() / "state.db"
    try:
        while True:
            snapshot = load_trace(db, args.voice_dir.expanduser(), hours=args.hours)
            publish_trace(args.output, snapshot, watch=args.watch)
            if not args.watch:
                print(args.output.expanduser())
                return 0
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        return 0
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    except OSError:
        print("Could not write the local timeline.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    setup_cli(parser)
    raise SystemExit(run_cli(parser.parse_args()))
