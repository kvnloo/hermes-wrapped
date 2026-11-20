"""Offline HTML view for the Wrapped stats snapshot."""

from .stats import StatsSnapshot


STYLE = """
:root {
  color-scheme: dark;
  --ink: #07070d; --panel: #0f0f18; --line: #1a1a30;
  --text: #e8e4dc; --muted: #9a968e; --gold: #ffd700;
  --mono: 'JetBrains Mono', 'Cascadia Mono', 'Fira Code', 'SFMono-Regular', Menlo, monospace;
  font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--ink); color: var(--text);
}
* { box-sizing: border-box; }
body { margin: 0; }
header { border-bottom: 1px solid var(--line); }
.shell { max-width: 70rem; margin-inline: auto; padding-inline: clamp(1rem, 4vw, 3rem); }
.mast { display: flex; justify-content: space-between; flex-wrap: wrap; gap: .7rem;
        align-items: center; padding-block: 1rem; }
.brand, .tag, .eyebrow { font: .75rem var(--mono); text-transform: uppercase; letter-spacing: .1em; }
.brand, .eyebrow { color: var(--gold); }
.tag { color: var(--muted); }
main { padding-block: clamp(2rem, 5vw, 4rem); }
h1 { margin: .25em 0 .12em; font-size: clamp(2.75rem, 8vw, 5.5rem); letter-spacing: -.045em; }
h2 { margin: 0; font-size: 1.2rem; }
p { line-height: 1.5; }
.sub, .note { color: var(--muted); }
.sub { margin: 0; }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 10rem), 1fr));
           gap: 1px; background: var(--line); border: 1px solid var(--line);
           border-radius: 10px; overflow: hidden; margin: 2rem 0; }
.metric { background: var(--panel); padding: 1.05rem; }
dt { font-size: .82rem; color: var(--muted); }
dd { margin: .55rem 0 0; font: 1.2rem var(--mono); overflow-wrap: anywhere; }
.metric:first-child dd { color: var(--gold); }
.metric small { display: block; margin-top: .4rem; color: var(--muted); font-size: .72rem; }
section { border: 1px solid var(--line); background: var(--panel); border-radius: 10px; padding: 1.25rem; }
.chart { height: 7.5rem; display: flex; gap: 3px; align-items: end; margin-block: 1.2rem .4rem;
         border-bottom: 1px solid var(--muted); }
.bar { flex: 1; height: var(--height); min-width: 0; background: var(--gold); border-radius: 2px 2px 0 0; }
.axis { display: flex; justify-content: space-between; gap: .5rem; }
.axis, td, time { font: .8rem var(--mono); }
.note, .axis { color: var(--muted); font-size: .82rem; }
details { margin-top: 1rem; }
summary { cursor: pointer; color: var(--gold); }
.scroll { overflow-x: auto; margin-top: .7rem; }
table { border-collapse: collapse; width: 100%; white-space: nowrap; }
th, td { padding: .65rem .55rem; text-align: right; border-bottom: 1px solid var(--line); }
th { font-size: .78rem; color: var(--muted); }
th:first-child, td:first-child { text-align: left; }
:focus-visible { outline: 2px solid var(--gold); outline-offset: 3px; }
"""


def render_stats(snapshot: StatsSnapshot) -> str:
    counts = snapshot.total
    first, last = snapshot.daily[0][0], snapshot.daily[-1][0]
    as_of = snapshot.as_of.strftime("%Y-%m-%d %H:%M UTC")
    cost = "Not recorded" if counts.estimated_usd is None else f"${counts.estimated_usd:,.2f}"
    coverage = (f"<small>{counts.estimated_sessions:,} of {counts.sessions:,} records</small>"
                if counts.sessions else "")
    metrics = "".join(f'<div class="metric"><dt>{label}</dt><dd>{value}</dd>{detail}</div>'
                      for label, value, detail in (
                          ("Session records", f"{counts.sessions:,}", ""),
                          ("Tool calls", f"{counts.tool_calls:,}", ""),
                          ("Input tokens", f"{counts.input_tokens:,}", ""),
                          ("Output tokens", f"{counts.output_tokens:,}", ""),
                          ("Estimated cost · USD", cost, coverage),
                      ))
    if counts.sessions:
        peak = max(c.sessions for _, c in snapshot.daily)
        bars = "".join(f'<span class="bar" style="--height:{c.sessions / peak:.3%}"></span>'
                       for _, c in snapshot.daily)
        rows = "".join(f'<tr><td>{day}</td><td>{c.sessions:,}</td><td>{c.tool_calls:,}</td>'
                       f'<td>{c.input_tokens:,}</td><td>{c.output_tokens:,}</td></tr>'
                       for day, c in snapshot.daily)
        end_label = f"<span>{last:%b %d}</span>" if first != last else ""
        gap = 3 if len(snapshot.daily) <= 31 else 0
        activity = f'''<div class="chart" style="gap:{gap}px" aria-hidden="true">{bars}</div>
        <div class="axis" aria-hidden="true"><span>{first:%b %d}</span>{end_label}</div>
        <details><summary>Daily totals</summary>
          <div class="scroll" tabindex="0" role="region" aria-label="Daily totals by UTC start day">
            <table><thead><tr><th scope="col">Day (UTC)</th><th scope="col">Records</th>
            <th scope="col">Tool calls</th><th scope="col">Input tokens</th>
            <th scope="col">Output tokens</th></tr></thead><tbody>{rows}</tbody></table>
          </div>
        </details>'''
    else:
        activity = '<p class="note">No session records started in this range.</p>'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Usage overview · Hermes Wrapped</title><style>{STYLE}</style></head>
<body><header><div class="shell mast"><span class="brand">Hermes / Wrapped</span>
<span class="tag">Local snapshot</span></div></header>
<main class="shell"><div class="eyebrow">01 / Usage</div><h1>Usage overview</h1>
<p class="sub">{first} to {last} · UTC · One profile</p>
<dl class="metrics">{metrics}</dl>
<section aria-labelledby="activity"><h2 id="activity">Session starts</h2>{activity}</section>
<p class="note">Lifetime counters are grouped by each record’s UTC start day. Includes archived
records and continuation records; not a count of distinct conversations. Cost is a recorded estimate, not a bill.</p>
<p class="note">Snapshot taken <time datetime="{snapshot.as_of.isoformat()}">{as_of}</time>.
Run <code>hermes wrapped stats</code> again to refresh.</p></main></body></html>'''
