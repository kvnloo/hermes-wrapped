# Hermes Wrapped

Your week with Hermes as a 40-second video with its own soundtrack, delivered every Sunday. Spotify Wrapped for your agent.

It tells the story of your week from your own records:

- **The hook:** "11 days away. Then you came back swinging."
- **Hermes worked 33 hours. You watched 18.** Agent-hours against your hours, because helpers run in parallel.
- **You didn't use an agent. You ran a team.** Peak number of agents running at once, and when.
- **Your peak hour** on a 24-hour clock, and how much of your week happened after 11pm.
- **Your latest message** of the week ("1:58am, Thursday").
- **Your biggest job**, its minutes and its cost.
- **Your verbal tic** ("You said 'right?' 58 times.") from a fixed, safe list of phrases.
- **The receipt:** what the week cost, and how much went into upkeep (setting up, configuring and fixing Hermes) versus Hermes working for you.
- **Your archetype:** The Night Shift Manager, The 2am Debugger, The Delegator, The Config Tinkerer, The Marathoner, The Perfectionist, The Sprinter or The Steady Hand, picked by plain rules over your numbers.

Scenes with nothing interesting to say are skipped, so a quiet week makes a shorter video.

**Zero tokens to make it.** Every number, the archetype and every line are computed by code from your records: same week, same video, no model calls. (In the Desktop app the finished video is posted by one short agent reply, the only model call involved.) The soundtrack is synthesized on your machine for each week (no bundled audio, no downloads). Landscape (16:9) or vertical (9:16) for stories and reels, in two looks taken from Hermes's own releases: `crt` (a glowing amber terminal with pixel type) and `blue` (navy and cobalt with film grain and an italic serif).

## Why a plugin, not a prompt

| | Skill + prompt | Hermes Wrapped |
|---|---|---|
| Data | The model can only see the current chat | Every session in the period, read from Hermes's session store, plus a `post_tool_call` hook that logs exact step timing and failures |
| Accuracy | Numbers are estimated or invented | Counted, never generated |
| Cost | Tokens every time, across dozens of sessions | Zero tokens: the story, archetype and soundtrack are made by code |
| Privacy | Hope the model leaves keys out | API keys, tokens, JWTs, emails and home paths are stripped before rendering |
| Every week | You have to remember to ask | `hermes wrapped schedule` adds a no-agent cron job that delivers it to Telegram (or anywhere) every Sunday |
| Same data, same video | No | Yes |

## Quick reference

**A bare `/wrapped` gives you:** your last 7 days, landscape (16:9), CRT amber-terminal theme. Change those defaults with the `period`, `format` and `theme` settings below.

| You type | You get |
|---|---|
| `/wrapped` | week, landscape, CRT |
| `/wrapped month` / `/wrapped year` | last 30 / 365 days (also `7d`, `14d`... any 1-365) |
| `/wrapped vertical` | 9:16 for stories and reels (`landscape` for 16:9) |
| `/wrapped blue` | navy and cobalt theme with film grain (`crt` for amber) |
| `/wrapped month vertical blue` | any combination; words can go in any order |
| `/wrapped session [id]` | one session as a short video (latest if no id) |
| `/wrapped insights [7d]` | what to change, as text, with no model call (also `24h`, `30d`, `90d`, `all`) |

**Choosing a theme:** say `crt` or `blue` in the command for that video, or set `theme` in `config.yaml` to change your default.

**What to expect:** a first render takes a few minutes (it draws every frame in headless Chrome). The first `/wrapped` replies "Rendering..."; run the same command again and the video appears instantly. The blue theme and the year period are the slowest; use `quality: draft` for faster previews.

## Install

```bash
hermes plugins install devpras22/hermes-wrapped
hermes wrapped doctor        # checks Node 22+, npx and ffmpeg
hermes wrapped schedule      # every Sunday 18:00 to your Telegram home channel
```

Needs **Node.js 22+, npx and ffmpeg** on the machine running Hermes. The first render downloads the pinned HyperFrames CLI and a headless Chrome (about 150 MB, once).

## Use

In any chat:

```
/wrapped                  # your default (last 7 days unless you change `period`)
/wrapped month            # your last 30 days
/wrapped year             # your last 365 days
/wrapped month vertical   # any period, 9:16 for stories and reels
/wrapped year landscape   # or 16:9, whatever your default is
/wrapped blue             # the navy and cobalt look (or `crt`, the default)
/wrapped session [id]     # one session as a short video
/wrapped insights         # what to change this week, as text
```

Or ask: "show me my week with you". The agent calls `wrapped_make` and sends the video.

From the terminal:

```bash
hermes wrapped week [--vertical] [--send telegram]
hermes wrapped month
hermes wrapped year --vertical --theme blue
hermes wrapped session <id>
hermes wrapped insights --range 7d [--narrate] [--json]
hermes wrapped schedule --deliver telegram --when "0 18 * * 0"
hermes wrapped list
```

Videos are saved to `~/.hermes/cache/videos/`.

## Stats dashboard

Run `hermes wrapped stats` (or `bin/hermes-wrapped stats` from a checkout) and open http://127.0.0.1:8800/. `--port` picks another port. It is the [Oh My Pi](https://github.com/can1357/oh-my-pi) `omp stats` dashboard, forked from `packages/stats` (MIT, see `stats/LICENSE`), with its data layer swapped for Hermes and z0live:

- Usage: Overview, Models, Providers and Costs, across every Hermes profile (`~/.hermes/state.db` and `~/.hermes/profiles/*/state.db`).
- Activity: Requests, Errors and Traces, plus Calls for z0live voice calls (turn gap, hand-off latency p50/p90, lane timings).
- Insights: What to change (see below), Tools, Frustration (from z0live's per-turn `user.reaction` flags) and Projects.

The dashboard reads metadata only: ids, timestamps, model and provider names, token counts, costs, tool names, error kinds and counts. It never selects message content, titles, prompts, tool arguments or transcripts, and it opens every store read-only. The server binds 127.0.0.1 and answers only to loopback Host headers.

What Hermes does not record stays unknown rather than zero:

- Hermes stores token usage and cost per session and model, not per request. Each assistant message gets an equal share of its session's usage, so totals reconcile exactly with the store.
- Request duration is the gap from the previous message (idle gaps over 30 minutes count as unknown). There is no time to first token.
- Cost is the recorded actual cost, else the estimate, with no input/output/cache split.
- Omp's Gain and Spotlight pages and its subscription-window panels are dropped: Hermes has no data for them. Frustration does not judge typed chats, because that would mean reading message text.

`hermes wrapped stats --snapshot PATH` writes one self-contained HTML file with every page's data inline and no server. The snapshot shows when it was built and does not refresh.

### What to change

The **What to change** page (key `i`) turns a window into findings. A finding names a measured cause, its evidence numbers, the Hermes setting to change, the API-equivalent cost it touches, and a link to the page that shows it. Five detectors run:

- Side tasks on the main model: compaction, approvals, background review and titles. The action names `auxiliary.<task>.provider` and `.model`.
- Subagents that inherit the main model. The action names `delegation.model` and `delegation.provider`.
- Big sessions with a measured cause: many subagents, repeated compactions, or a context much heavier than your typical session's. A session that is big only because it did a lot of work is not a finding.
- Failed turns per model and provider.
- z0live lanes in which one model rarely answers. This detector runs only for voice users.

Each detector also reports a check: found, fine, or waiting for more data. Dollar figures are API-equivalent estimates. On a subscription they are not money you spent or will save. Requests with no price are left out of every dollar figure, and a note says how many there are.

A model can write a short summary with three actions, and answer questions on the live page. It gets the findings and totals only: counts, costs, timings, and model, provider and profile names. It never gets messages, prompts, titles, tool arguments, folder names or session ids. The page shows exactly what was sent. Every number in a reply must match a number the model was given. If one does not, the reply is hidden and marked unverified, and the findings stand alone. Replies are cached by their input.

- The model is your Hermes auxiliary model. Set `auxiliary.wrapped_insights` in `config.yaml` as you would for any auxiliary task. If it is unset, Hermes picks one.
- To use a local OpenAI-compatible server instead, such as llama-server, pass `--llm-url http://127.0.0.1:8080/v1`.
- `hermes wrapped insights` and `/wrapped insights` print the findings with no model call. They add a summary only if one is already cached. `hermes wrapped insights --narrate` writes one.
- `hermes wrapped stats --snapshot PATH --narrate` writes summaries into the snapshot. A snapshot cannot answer questions.

From a checkout, summaries need Hermes's runtime. Run `HERMES_AGENT=~/.hermes/hermes-agent <Hermes's python> bin/hermes-wrapped ...`, or pass `--llm-url`. [docs/ADVISOR-INSIGHTS.md](docs/ADVISOR-INSIGHTS.md) records the review behind these detectors and guards.

The client lives in `stats/`. Rebuild it after changing it with `(cd stats && bun install && bun run check && bun run build)`. The built `stats/dist` is committed so the plugin needs no JavaScript toolchain.

To run the offline tests from a checkout, run `python3 -m unittest discover -s tests`.

## How "upkeep" is decided

A session counts as upkeep when it ran inside `~/.hermes`, its title is about Hermes itself (Hermes, gateway, plugins, profiles, providers, config, API keys, changing models), at least 40% of its steps touched Hermes (`~/.hermes`, `config.yaml`, `hermes config|model|auth|plugins|…`), or its title is about setup or fixing and at least 20% of its steps touched Hermes. The video shows the reason next to each upkeep session, so you can see exactly why it counted.

## How it works

1. `timeline.py` reads sessions through Hermes's public read-only `hermes_state.SessionDB`: requests, tool calls and results, timings, tokens and cost.
2. `wrapped.py` totals the period, compares it with the period before, and classifies upkeep.
3. `redact.py` strips secrets and private paths.
4. `render.py` writes a [HyperFrames](https://github.com/heygen-com/hyperframes) composition (HTML + GSAP) and renders it with a pinned `hyperframes` CLI.

## Settings

Under `plugins.entries.hermes-wrapped.settings` in `config.yaml`:

| Setting | Default | What it does |
|---|---|---|
| `show_cost` | `true` | Show what the week cost. Turn off before posting publicly if you prefer. |
| `music` | `true` | Add the generated soundtrack. |
| `theme` | `crt` | `crt` (amber terminal) or `blue` (navy, cobalt, film grain). Or ask per video: `/wrapped blue`. |
| `period` | `week` | What a bare `/wrapped` covers: `week`, `month` or `year`. |
| `format` | `landscape` | `landscape` (16:9) or `portrait` (9:16). Or ask per video: `/wrapped vertical`. |
| `timezone` | `""` | IANA name such as `America/New_York` for the clock scenes. Empty uses Hermes's own `timezone` setting. |
| `show_titles` | `true` | Show your biggest project's name. Turn off to show "Your biggest project" instead. |
| `quotes` | `false` | Show your latest late-night message, only if it is short, clean and names nobody. |
| `quality` | `standard` | `draft`, `standard` or `high`. |

Times use the plugin's `timezone` setting, then Hermes's own (`timezone` in `config.yaml` or `HERMES_TIMEZONE`), then the server clock. When it falls back to a UTC server clock, the caption says so and `hermes wrapped doctor` tells you how to fix it.

## Disclosures

- **Reads outside plugin data:** your session history (`state.db`), read-only, through `hermes_state.SessionDB` and a read-only SQLite connection. Nothing leaves your machine unless you deliver a video or ask a remote model for an insights summary. That summary gets aggregates only, as listed under What to change.
- **Shell commands / subprocesses:** `npx --yes hyperframes@0.8.217 render` (pinned) to render, `hermes send` to deliver. `hermes wrapped schedule` runs `hermes cron create`.
- **Third-party network calls:** the first render downloads the HyperFrames CLI from npm (`hyperframes@0.8.217` is pinned; its own dependencies are resolved by npm) and a headless Chrome build chosen by HyperFrames. The fonts (Silkscreen, VT323, Pathway Gothic One, Instrument Serif and IBM Plex Mono, all SIL Open Font License) ship in `assets/fonts`, so nothing is fetched for type. No TTS calls. A model call happens only when you ask for an insights summary or answer: the **Write a summary** button, the **Ask** box, `--narrate`, and the video reply below. No telemetry.
- **Writes:** tool-timing events to `~/.hermes/plugin-data/hermes-wrapped/events.db` (kept 30 days), cached insights replies to `~/.hermes/plugin-data/hermes-wrapped/insights-cache/`, a temporary render folder under the same directory (deleted after each render), MP4s to `~/.hermes/cache/videos/`, and, only if you run `hermes wrapped schedule`, one script at `~/.hermes/scripts/hermes-wrapped-weekly.sh`.
- **Background work:** `/wrapped` answers at once and renders in a background thread, so the chat is never blocked (Desktop slash commands time out after about 30 seconds). In messaging apps the video is sent with `hermes send` when it is done. In the Desktop app, TUI and CLI a slash command can only print text, so when the video is ready the plugin hands it to the same chat with `inject_message`, and the agent's one-line reply plays it. Apart from the insights summaries you ask for, that reply is the only model call Wrapped causes. It needs `allow_gateway_injection: true` under `plugins.entries.hermes-wrapped` in config.yaml. A video already rendered today is reused, and a cross-process file lock (`render.lock`) means only one render runs at a time, however many sessions ask.
- **Credentials:** none.
- **Privacy on screen:** in the weekly video, the biggest project's name is shown only after secrets, paths, IPs, logins and domains are removed (turn it off with `show_titles: false`), and your messages are never shown unless `quotes` is on. `/wrapped session` is different: it shows that session's first request, its step labels and the final answer, with secrets and home paths stripped.

## Related

For polished demo videos of a single session, with a storyboard editor, use [Showreel](https://github.com/Duffey234/hermes-showreel). Wrapped's `/wrapped session` is a quick, no-frills version; Wrapped is about the week.

## Credits

Rendering by HyperFrames (HeyGen, Apache-2.0). Animation by GSAP 3.14.2, bundled in `assets/` under the [GSAP standard license](https://gsap.com/standard-license); see `NOTICE`.

MIT licensed, except `assets/gsap.min.js` (see `LICENSE` and `NOTICE`).
