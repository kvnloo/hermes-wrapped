# Insights: the advisor's review and what we adopted

The Insights page, the Overview banner and `/wrapped insights` follow this review. A second model critiqued the plan before any code was written. It saw the README, `stats_api.py`, the client types, a signal inventory (`SIGNALS.md`, a list of the metadata Hermes records and what it does not) and dashboard screenshots. It saw no data. The run used `advisor-lane --model sonnet`. Its full answer is below the decisions, unchanged.

## Decisions

### Adopted

- Detectors read only session totals or (session, model, task) totals. Per-request values are even splits of a session's usage, so no finding uses them.
- Four of the advisor's "adopt now" detectors ship, as `stats_insights.DETECTORS`:
	- `side_tasks`: compaction, approvals, background review and titles that run on a profile's main model.
	- `big_sessions`: the advisor's "runaway family". A big session becomes a finding only with a measured cause: subagents, repeated compactions, or a context much heavier than the typical session's. A session that is big only because it did a lot of work is not a finding.
	- `subagent_fanout`: subagents that inherit the main model.
	- `failed_turns`.
- A materiality floor applies. A cost finding must hold at least `MIN_COST_SHARE` (1%) of the window's API-equivalent cost. The advisor proposed 2%. Both are guesses until more installs report.
- Every detector reports a check: found, fine or waiting for data. A fresh install sees why the page is empty.
- Unpriced requests get a note, and the note says every dollar figure leaves them out.
- Dollar figures say "API-equivalent" everywhere. No finding says "save".
- Narration uses slots. The model writes `{side-tasks.cost}` and the code fills in `$28.77`. A slot carries its unit, so a model that writes `{x.calls}%` fails the check.
- The comparison guard is in. Phrases such as "last week", "week over week" and "than usual" fail the check, because the input holds no earlier period.
- Failure is closed. An unverified reply keeps only the rejected figures, never the model's text, so no client can show it.
- Narration is opt-in. `hermes wrapped insights` and `/wrapped insights` never call a model. They show a cached summary when one exists. The page has a **Write a summary** button, and `--snapshot --narrate` precomputes summaries for a snapshot.
- The cache key holds the prompt text, the model route and the payload. Editing the prompt invalidates old replies. The cache stores the raw reply, and every read runs the grounding check again.

### Checked where the advisor could not

The advisor could not verify any Hermes config key. Each action now names a key read from `hermes_cli/config_defaults.py`: `auxiliary.<task>.provider` and `.model`, `delegation.model` and `delegation.provider`, `compression.proactive_prune_tokens` and `compression.threshold_tokens`.

### Adopted differently

- **Number grounding.** The advisor asked for zero numerals outside slots. Slots are preferred, but the model may also copy a number exactly as the input writes it. Every number then has to match an input number within rounding. Reason (judgment): totals and model rows have no slot names, and the check still rejects any number that is not in the input.
- **Ask box.** The advisor asked for an intent router. The box takes a free question over the same payload and passes the same grounding check. The answer carries a `recorded` flag, so a question about message text gets "Not recorded by Hermes". Reason (judgment): an intent router is a second query engine to maintain. The payload is about 9 KB, and the check fails closed.
- **"Explain this" on charts.** The advisor asked to cut it, because it invites free-form claims about smeared numbers. It stays, without a model. It lists the findings and checks for that page, as written by the detectors.
- **Voice lanes** shipped now instead of later. Voice users are the second audience here. The detector waits until a lane has 50 runs, and a SKIP counts as a decline, not an error.

### Not adopted

- Billing leak (#4), hook tool failures (#7), typed-chat intervention rate (B1) and cron spend (B2). These are left for later.
- Aliasing profile and model names before they reach the model. Reason: names are the user's own config, and the same auxiliary model already reads full transcripts when it compacts sessions. Folder names and session ids are never sent.
- Bans on causal words ("because") and quantifiers ("most", "almost all"). A cause already comes from the finding's own action text. Quantifiers are not checked. The live summaries do use "almost all", so this is an open gap.
- One automatic retry. A failed call is not cached, so **Try again** retries. An unverified reply is cached and stays unverified for the same input.
- Closed local days. Insights use the same rolling windows as the rest of the dashboard.
- The section F demotions (the cost column in **Latest requests**, the latency tiles and others). These are outside this change.

## The advisor's answer

### Design critique

- **Per-request values are not real data.** Hermes records usage per (session, model, task) and the dashboard splits it evenly across messages. That is why Overview "Latest requests" repeats 276,231 tokens / $0.0950 on row after row. Detectors may read only session-level or (session, model, task) rows. They must not read per-request values, per-day buckets or Tools "Attr. cost".
- **Keep three ledgers separate and never sum them:** metered dollars, API-equivalent estimate, and unpriced (tokens only). 1,290 of 13,006 requests (9.9%) are unpriced here. "98.3% of estimate" is therefore a share of the priced part only.
- **Token share is a weak impact unit.** About 96% of tokens here are cache reads. Impact is a share of the priced estimate where priced, and a request/session count where not.
- **The LLM layer earns little.** Findings, ranking and actions are deterministic. The model is left with connective prose. That is acceptable, but it must be opt-in. The README sells zero tokens, so Insights must work fully without it.
- **Silence is a valid output.** Fresh installs have 1–2 days of data. Each detector declares a minimum sample. Below it, the page says "needs N more" instead of firing.
- **Use closed local days, not rolling ranges.** This keeps output stable and cacheable.
- **Install-wide materiality floor.** A finding must be at least 2% of install-wide window usage, or at least $5 metered. Rank by share of install, then ledger (metered first). This stops a $0.10 profile from firing. All thresholds below are proposals to tune on real installs.
- **Finding schema.** It needs `impact{value, unit, ledger}`, and the impact may be null. The `delta` field is null unless a baseline exists.
- **Read-model work.** Detectors 1 and 2 need fields the current payloads don't expose: side-channel model and compression counters. Detectors B1–B3 need rewind/steer/orphan fields, so they are extra read-model work.
- **Config keys.** I could not verify any Hermes config key from these files. All action text names the setting generically until someone checks.

### A. Detectors, ranked by "changes behavior tomorrow"

| # | Detector | Exact rule | Action template | Impact | Deep link |
|---|---|---|---|---|---|
| 1 | **Side-channel on the main-loop model** (approval, title, compression, background_review) | Per (profile, task, model): ≥20 requests, model equals that profile's main-loop model, and the task is ≥1% of that profile's window usage. Screenshot: Review (75 req, 23M tokens, about 307k per request, 1.2% install-wide) passes the volume floor. It fires only if it ran on the main model, which the By-source panel doesn't show. Approval (1,596 req, 0.1%) does not fire. | "`{task}` ran {n}× on `{model}` in `{profile}`. Point this task at a cheaper auxiliary model." | Share of the priced estimate. A repriced what-if appears only with a dated price table (see C). | Overview By-source row (new task filter needed) |
| 2 | **Runaway session family** (absorbs compaction counts) | At least 10 families in the window. Fires when a family is ≥25% of its profile's usage **and** ≥5× that profile's median family. Cause tags: ≥5 subagents; `compression_ineffective_count` ≥1 or `compression_fallback_streak` ≥2 or ≥3 compaction rows; `end_reason` is an orphan reap. | "Family `{cwd}` used {share}% of `{profile}`. Cause: {tags}." Each tag maps to a catalog action, e.g. a compaction tag says "split the task or change the compression model". | Its own share. Never "would have saved". | Traces, session id |
| 3 | **Subagent fan-out** | ≥20 child sessions in the window. A parent family has ≥5 children, with the child model equal to the parent's top-tier model. | "{n} subagents ran on `{model}` ({share}% of estimate). Set the delegation model lower." | Share of estimate. The repriced what-if covers subagents only. | Traces, parent session |
| 4 | **Billing leak** (user #1) | A model has both subscription-included rows and metered rows in the window. Fires at ≥$1 metered or ≥5% of that model's requests. Screenshot: opus via `anthropic` is 6 requests / $0.19, so it correctly does not fire. | "{n} requests of `{model}` were billed to `{provider}` ({usd}) while the rest ran on subscription. Check `{profile}`'s provider." | Actual dollars, the only real money here. | Costs, model×provider |
| 5 | **Failed, truncated or refused turns** | Drop failures with provider `unknown` **and** 0 tokens first (3 of the 5 screenshot failures). They become a data-quality note. Per (model, provider): ≥20 turns and ≥3 events from `failed_turn`, `finish_reason` length/incomplete or `content_refused`, at ≥2%, or ≥3 events in 1 hour. | Length: "{n} turns on `{model}` stopped at the output limit. Raise the limit or trim context." Failed: "{n} failed turns on `{model}`." | Event count only. Per-turn tokens are smeared shares, so they are not "wasted tokens". | Errors, filtered |
| 6 | **Voice lane answer rate** (voice users only) | Per tier, ≥50 runs. Lead with the tier's answered/runs (fast: 46/129). Flag a model whose answers/(answers+skips+errors) is under half its tier-mate's, or one with ≥3 errors. Attempts differ per model (haiku 116, luna 67 of 129), so the lane structure is unknown. | "`{model}` answered {a} of {n} fast-lane attempts; `{other}` answered {b} of {m}. Review lane membership." SKIP is not failure, and lane cost is unrecorded. | None. No seconds claim. | Calls, Lane timings |
| 7 | **Tool failure/blocked rate** (hook-only) | Only tools with hook coverage ≥200 calls. Fires at ≥10 failures and ≥5%. Always print "since {date}, {n} of {m}". | "`{tool}` failed {n}/{m} since {date}." | Count. Seconds only if hook-timed. | Tools |

**Cut, with reasons**
- **Spend concentration standalone.** One model above 90% is normal (SIGNALS.md). It is only the denominator in #1–#4.
- **Main-loop what-if swap.** Nothing says a cheaper model would have done the job. It is allowed only as the impact line of #1 and #3.
- **Cache-rate drops.** The rate is 98.8%, daily values are smeared, and there is no action.
- **Error/timeout clusters by provider.** 5 failures in 13,006 requests, and no timeout field exists. #5 replaces this.
- **Week-over-week as a detector.** The screenshots show 3 active days. It stays only as the optional `delta` field, shown when both periods have ≥3 active days and ≥30 samples.
- **Voice p50/p90 regressions: cut.** "Turn gap p50 1.80s" has no action, and the voice side owns those numbers. The exact signals I dropped are 3 of 13 calls ending at the duration limit and 9 of 129 hand-offs neither answered nor hung up. Show them as plain counts on Calls, not as findings.
- **Frustration hot spots.** Only 45 measured turns exist. Show a count line only at ≥100 measured turns and a flagged rate above 20%.
- **Slow tools from inferred gaps.** `clarify` p50 90s is the human waiting. Use hook durations only.

### B. Missing detectors the fields support

1. **Typed-chat intervention rate.** Count `steer`, `interrupted` and `rewind_count` per (profile, project), excluding cron and subagent sources, at ≥20 sessions. Fire when steer+interrupt exceeds 10% of user turns, or rewinds exceed 1 per 5 sessions. Compare a project only with that user's other projects. Action: "You steered {pct}% of turns in `{cwd}`. Put recurring guidance in project instructions." Impact is a turn count only. It is metadata-only, so it fits the README rule.
2. **Cron spend.** Sessions with `source` cron or `end_reason` `cron_complete` that are ≥20% of a profile's usage. Action: lower the frequency or the model. It is per profile and cwd, because job ids don't exist.
3. **Unpriced usage, as a banner.** "{n} requests ({pct}%) on `{model}` have no price. Every dollar figure excludes them." Not ranked.
4. **Cut:** cache-write waste has no honest action without per-component prices (`cacheSavings` is null), and the orphan-reap count is low value. Compression health is merged into #2.

### C. Cost findings for subscription users

- Subscription findings lead with a **share of the API-equivalent estimate**, not "$X". Each carries its ledger label.
- Quota windows are not recorded. Wrapped cannot say whether you'd hit a limit, and the UI says so.
- A what-if may claim only this: "the same recorded tokens, repriced at list prices dated {D}, change the API-equivalent estimate by {delta}." It shows the unpriced exclusion.
- It must **never** claim:
  - that you'd save money, because the subscription is flat;
  - that the cheaper model would succeed or match quality;
  - that tokens transfer across models, because tokenizers differ;
  - that cache behavior carries over;
  - anything about the main loop.
- Without a per-component price table, show shares only and no dollar what-if.
- User #2 has one profile and real dollars. #1–#5 apply to them, except #4, which needs a subscription-included model. The materiality floor keeps a quiet week empty.

### D. LLM narration: failure modes and guards

The grounding check as designed (every output number must appear in the input) is too weak. A real number can attach to the wrong entity, small integers match almost anything, and model names contain digits (`claude-opus-5-5`, `gpt-6.2-sol`).

**Replace it with slots.** The model writes no numerals. Output is JSON:
`{"summary":[{"finding":"f3","text":"… {f3.share} … {f3.model} …"}],"actions":[{"finding":"f3","action_id":"route_side_channel"}]}`
Code fills typed slots (ms formatted as seconds, fractions as percents, the window as a slot). Tolerance is zero, because the model writes no numerals. Rounding is the formatter's job. Ranking and action text come from the catalog.

| Failure | Guard |
|---|---|
| Hallucinated numbers | Reject any digit, number word ("two", "dozens"), or quantifier ("most", "doubled", "half") outside slots. Slots must exist in the cited finding. |
| Derived numbers (differences, percents) | Precomputed fields only. The model cannot compute. |
| Seconds vs ms, fraction vs percent | Slot types carry the unit. Only the formatter renders them. |
| Phantom "last week" | Comparison words (than, vs, last, increase, drop, trend) are allowed only in a sentence citing a finding whose `delta` is non-null. Otherwise reject. The window is a slot, so "last {window}" is fine. |
| Wrong causality | Ban "because", "due to", "caused" unless the finding has computed `cause_tags`. The model may cite only those tags. |
| Generic advice | Each action needs a catalog `action_id` and ≥1 entity slot (model, task or profile). An action with no firing finding is invalid. |
| Prompt injection via names | Model, profile and cwd strings are user-controlled. Alias them (`profile_1`, `project_2`) in the model input and unalias in code. This also keeps real names off a remote endpoint. |
| Failed grounding | One retry at most. Then render the deterministic template summary with an "unverified / template" badge. Never show partial LLM text. |

Cache key: hash of findings JSON, closed-day window, detector version, prompt version, price-table version and model id. A remote auxiliary model sends data off the machine, so require explicit confirmation naming the endpoint.

### E. Ask-a-question box

Make it an **intent router** to fixed queries over findings and aggregates. Do not use free-form answering.

**Answer "not recorded":**
- why a turn failed, or anything about message content;
- tool success without hook coverage;
- time to first token;
- per-request cost or tokens (only session grain exists);
- typed-chat frustration beyond steer/interrupt/rewind counts;
- quota remaining or subscription spend;
- answer quality or success;
- anything before the data start, or before the hook was installed.

**Refuse:**
- forecasts;
- "why did cost rise?", answered instead with a decomposition into model, profile and task;
- anything needing message text.

### F. Cut or demote

- Overview "Latest requests" tokens/cost columns: smeared. Demote to Requests and drop the cost column.
- Tokens/s tile and the "Avg latency" tile. Both are inferred from message gaps.
- The 0.04% error-rate tile as a headline, and the "Output 0%" token-mix line.
- Tools: the "Attr. cost/tokens" columns, per-tool trend sparklines, and the 70-row tool×model table (collapse it).
- Models/Providers performance series where TTFT is null.
- Frustration page: fold it under Calls until there are ≥100 measured turns.
- Chart "explain this": cut. It invites free-form claims about smeared numbers.
- The Overview banner shows the top 3 findings plus a coverage line, not a narrative by default.

### Adopt now / Later / Never

| Adopt now | Later | Never |
|---|---|---|
| Detectors 1–5, deterministic, with the materiality floor | Voice lane (#6), hook tool failures (#7) | Standalone main-loop what-if swap |
| Slot-based narration, opt-in | Typed-chat intervention rate (B1), cron spend (B2) | Cache-rate drop detector |
| Coverage / "needs N more" line | Price table with components | Error clusters at this volume |
| Ledger-split cost labels | Week-over-week `delta` | Free-form ask box, chart "explain this" |
| `/wrapped insights` text output | Frustration count line (≥100 turns) | Per-request cost surfacing |
