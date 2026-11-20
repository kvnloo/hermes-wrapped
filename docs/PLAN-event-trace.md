# Hermes Wrapped: stats and event traces

## What exists today

Measured by source inspection. Wrapped v0.1.2 registers `/wrapped`, `wrapped_make`, a CLI, and a `post_tool_call` hook. It creates a deterministic week, month, year, or session video with local music and HyperFrames. `story.py` and `wrapped.py` compute the recap; `timeline.py` reads session history and reconstructs tool steps; `store.py` retains 30 days of measured tool timing and status in a plugin-owned `events.db`. The video answers “what stood out?” It does not provide an interactive usage page, a live timeline, or voice ingestion.

Hermes already has `/api/analytics/usage` and `/api/analytics/models` in `hermes_cli/web_routers/analytics.py`. `d40794140b` is on the checked `origin/main` and uses `session_model_usage` for per-model attribution across model switches. That commit is not Kevin's. Kevin's [#99142](https://github.com/NousResearch/hermes-agent/issues/99142) asks for an inspectable tool-call timeline. [#158091](https://github.com/NousResearch/hermes-agent/pull/158091) is the z0live catalog work, not the parity backend. The exact earlier `/stats` issue and Kevin-authored backend PR set remain unverified. Author/keyword GitHub searches, local git history, and SQLite FTS against profile stores (`mode=ro`) did not establish the link. `hermes sessions --help` confirms an existing session-store stats command, not an Oh My Pi parity page. No private text is quoted here.

[ephemeral-ui](https://github.com/kvnloo/ephemeral-ui) at `d5e4a5b` has the existing UI work: `stats-lib.js` for cards, tables, charts, and drawers; `stats-routes.js` for route compositions; `stats-section.js` for the fixture-backed demo; `stats-trace.js` for the shared viewport, waterfall, and minimap; `src/stats.css` for styles. Its components consume Oh My Pi API shapes. Adapt those shapes at the view boundary and reuse the pieces after auditing their host events. Do not reuse the recorded fixtures or forward selection events to an agent.

## Parity and data gaps

Measured from [Oh My Pi's stats package](https://github.com/can1357/oh-my-pi/tree/2be7255/packages/stats). Its JSONL watcher and SQLite hourly rollups feed live status and range changes over SSE. The UI has keyboard range and route shortcuts, searchable and sortable tables, and trace drill-down. The route registry also includes Spotlight, which the package README omits.

| Oh My Pi page | Hermes source and proposed parity |
| --- | --- |
| Overview | First slice: daily activity and session-level counters. Later: request-time usage, range presets, previous-period comparison, and freshness. |
| Models, Providers, Costs | Reuse Hermes analytics and `session_model_usage` before adding breakdowns. Separate actual, estimated, and unpriced, and show coverage. Never price a subscription as a bill. |
| Requests, Errors | Add a request-level read model sourced from runtime events. TTFT, latency, throughput, and errors need request timestamps and outcomes, not session averages. |
| Tools | Join hook timings and outcomes to session evidence by call ID. Show counts, errors, and percentiles with sample counts and timing coverage. |
| Projects | Group by recorded repo or working-directory metadata. Display local aliases; suppress raw paths by default. |
| Traces | Build a live waterfall with filters, minimap, zoom, idle compression, wall-time/turn/call axes, search, accessible event list, span drawer, and session comparison. |
| Gain, Frustration, Spotlight | Map compression savings and tool-specific economics only where Hermes records comparable evidence. Mark unsupported metrics explicitly. A model judge is not part of the local plugin and must not receive chat history. |

## First slice and host integration

Use an immutable `StatsSnapshot` with UTC buckets, an observation time, totals, and cost coverage. `load_stats` reads a single profile's SQLite session summary columns using `mode=ro`. It selects no messages, titles, paths, session IDs, or account identities. The slice counts non-hidden session records started in the range, including archived records and compaction rows. It attributes each row's lifetime counters to its start day. This is a cohort view, not request-time usage or a count of distinct conversation families.

Two hosts are viable. A dashboard extension inherits navigation and theme but needs a verified auth and local-data boundary before it can serve data. A self-contained HTML snapshot proves the reader and presentation without a listener, plugin activation, or a restart. Ship the snapshot first through `hermes wrapped stats`. The page has no scripts or remote assets. Later, add a Hermes dashboard tab at `/stats` and a local-only slash entry that opens it where supported. Never post a private page or path to chat. Verify unauthenticated denial, profile isolation, and remote-client denial on the installed version before registering data routes. Dashboard docs and the bundled Kanban guide describe different plugin auth behavior; inspect the actual middleware and test the route rather than assuming either applies.

Use the Hermes website dark gold, ink, and warm text from `website/src/css/custom.css`. Use its Inter and JetBrains Mono stacks with the dashboard's local monospace fallbacks from `web/src/index.css`. In the dashboard host, inherit the active theme's semantic colors and fonts from `web/src/themes`, including light mode. The standalone snapshot does not fetch or redistribute fonts.

## Live event model

Keep provider events separate from the renderer. Normalize to a versioned `TraceEvent` with `id`, `origin`, `kind`, `profile_key`, `trace_key`, `lane_key`, optional `parent_span_id`, `wall_time`, optional `source_elapsed`, and allowlisted counters or status. Pair start/end into `TraceSpan` with a provenance field and optional duration. Represent missing starts, finishes, and correlations as unknown. Use explicit IDs, never fuzzy transcript matches.

- Hermes sessions supply session boundaries and lineage. Distinguish continuation and compression from real delegation before grouping. Use runtime tool events and Wrapped's hook store for timing. Recovered timestamps from message metadata are inferred; label them. Do not pass bodies, arguments, outputs, or reasoning text through the adapter.
- z0live reads `~/.local/share/z0live/voice-traces/*/*.jsonl` without touching call services. The local files inspected had `ev`, `t`, `wall`, `did`, and, on some rows, content fields. Start with `call.*`, `lanes.tier`, `lanes.spoken`, `delegation.start` and `delegation.done`. Pair by `did` within a trace, use `t` for same-trace elapsed time after validating units, and use `wall` for calendar placement after schema validation. Link an explicit Hermes session reference only when verified; otherwise keep the voice and Hermes lanes separate. Skip `rx`/`tx` payloads, transcripts, chats, owner text, audio, and arbitrary nested fields. Aggregate audio-rate telemetry only if it answers a visible question.
- Tail complete newline-terminated records with bounded buffers and per-file generation/offset cursors. Check rotation and truncation, retain partial lines for retry, and count malformed lines without logging their contents. Make inserts idempotent. Keep a bounded replay window and use a version invalidation plus cursor-based resync on reconnect. Group lanes by call or session and nest delegations only when the parent link is known.

Keep all trace and stats rendering local. No remote analytics, exports by default, or uploads. If an explicit voice diagnostic export is added later, it can target only the provider that already saw those voice events. Never bundle Telegram, chat history, or owner messages with it. Keep private paths and raw records out of commits, screenshots sent off-host, and logs. Existing Wrapped video delivery is a separate feature; do not reuse it for stats or traces.

## Verification gates

1. Snapshot: fixture counts reconcile at UTC boundaries, missing cost differs from a recorded zero, and a real profile renders without exposing private fields or opening a listener.
2. Dashboard and parity: verify access controls, keyboard and narrow layouts, active light/dark theme, cross-page reconciliation, and per-metric coverage. Replay synthetic request and model-switch cases against Hermes's own analytics.
3. Trace: use synthetic overlapping calls, missing end events, delegation, reconnect, partial JSONL writes, and rotation. Check stable IDs and no duplicate spans. Compare an opt-in local call's metadata to the timeline without restarting Studio or sending trace data away.
