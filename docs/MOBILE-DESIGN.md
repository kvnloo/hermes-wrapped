# Phone design: Sound Atlas's language on the stats dashboard

The stats dashboard (`hermes-wrapped stats`) is a desktop tool forked from Oh My Pi's stats page. On a phone it
used to be the desktop layout squeezed: a hamburger sidebar, a six-way range switch jammed into the top bar,
tables that scrolled sideways, hover-only chart tooltips, and 1,500 of 2,172 tap targets under 44 px. This
document records the reference it now follows, Sound Atlas, and how each part of the dashboard maps to it.
The code is one stylesheet, `stats/src/client/phone.css`, plus a handful of small component hooks. Every
rule sits under `@media (max-width: 600px)`, so the desktop layout does not change.

## The reference

Sound Atlas is Kevin's music map in fframestudio (`kvnloo/fframestudio`, `web/src/music-atlas`). Its phone
build (390 x 844 is its e2e target) was run from the static bundle and shot in its main states: the map with
the sheet at peek, the sheet at half and full height, a track card, and search.

What makes it S-tier, in order of how much it carries:

1. **One sheet, one thumb zone.** Every control lives in a bottom sheet with a grab handle and 22 px
   shoulders. Search, filters and the player sit at thumb height. The top of the screen holds only the brand
   and one status pill. Nothing a thumb needs is up there.
2. **A three-voice type system.** Italic Instrument Serif for things with names (track titles at 17 px, the
   summary line at 22 px, a now-playing title at 25 px). Archivo Black, uppercase with letter spacing, in the
   accent colour for section headers (13 px). IBM Plex Mono for every figure and caption (9 to 11 px). Body
   text is Instrument Sans at 15 px. Hierarchy comes from the voice, not from size jumps.
3. **A warm night palette.** Background `#0d1112` (green-black, not pure black), ink `#ece8df` (cream), muted
   `#8f8a80` (warm grey), hairlines `#252c2e` / `#343c3e`, panels `#161c1d`, one cyan accent `#5fd3dc`. Each
   feel (mood family) owns a colour that tints its rows' left edge, its header dot and its card.
4. **Rows, not tables.** A list row is a 36 px tinted icon tile, a serif title, a mono caption under it, and
   one figure on the right. Section headers are sticky, coloured and carry a glowing dot.
5. **Physical motion.** The sheet is a spring (`cubic-bezier(.34, 1.56, .64, 1)` for keys and pops,
   `cubic-bezier(.2, .8, .2, 1)` for glides). Pressed things scale to 0.88 to 0.97. Segmented switches slide
   a key by transform only. Rows rise in with a 22 ms stagger. Everything is transform and opacity, and
   `prefers-reduced-motion` turns it all off.
6. **Gestures with a visible affordance.** The grab handle widens while held. A sheet drags between snaps and
   rubber-bands past its ends. Filter chips scroll sideways in a masked row. Haptics mark each detent.
7. **Safe areas and real targets.** `viewport-fit=cover` with `env(safe-area-inset-*)` on the top bar and the
   sheet. Search is 42 px, transport buttons 46 to 58 px, menus 58 px rows.

Where Sound Atlas itself falls short: its filter chips and the view switch are 28 to 32 px tall, under the
44 px minimum. Wrapped does not copy that part.

## How Wrapped maps to it

| Sound Atlas | Wrapped on a phone | Where |
|---|---|---|
| Bottom sheet holding search, filters, player | **Tab bar**: a near-opaque slab at the bottom with five tabs (Overview, Insights, Requests, Calls, More). An accent key slides to the active tab on the spring. 64 px plus the safe area | `Shell.tsx` (`.dock`), `nav.ts` (`PHONE_TABS`) |
| One status pill at the top | The top bar keeps the brand, the snapshot's age (dot + "4h ago"), a range pill ("7d ⌄") and the theme toggle, each a 44 px target drawn as a hairline pill or ring | `Shell.tsx` (`.range-pill`), `phone.css` |
| Sheet views, all one tap away | **Page sheet**: More opens every page as a bottom sheet, grouped Usage / Activity / Insights, each group with its own colour, dot and tinted icon tiles. **Range sheet**: the range pill opens the six windows as rows with a check on the current one | `Shell.tsx` (`NavGroups`, `.sheet-nav`) |
| Grab handle, drag to dismiss | Every drawer (request detail, span detail, both sheets) is a bottom sheet with a handle. Drag the handle or header down 96 px, or fling it, to close; short of that it springs back | `Drawer.tsx` |
| Serif for names | Page titles at 38 px italic serif, sheet titles at 28 px, finding titles at 20 to 23 px | `phone.css` |
| Archivo Black section headers, one colour per feel | Card titles in 800-weight uppercase with tracking, coloured by the page's group (Usage cyan, Activity pink, Insights amber) behind a glowing dot. The system sans stands in for Archivo Black, so no second font ships | `Shell.tsx` (`data-group`), `phone.css` |
| Plex Mono captions | KPI labels, row captions and evidence labels in mono | `phone.css` |
| Night palette | Dark theme takes the Sound Atlas colours. Light theme is the same family on warm paper (`#f3f0e9`, ink `#1b2021`, muted `#686359` at 5:1) with soft card shadows | `phone.css` tokens |
| Track card tinted by its feel | The written summary card ("What to change") is tinted amber. On Overview it sits under the KPI row, clamped to four lines with Read all | `.card:has(.insight-summary)`, `Summary.tsx` |
| Masked horizontal chip row | Headline KPIs are a swipeable row; each tile is as wide as its figure and the next one peeks at the edge. Secondary KPIs stay a dense 2-column card. Toggle legends become a swipeable chip row with faded edges | `.stat-grid`, `.legend:has(> button)` |
| List rows | Every table becomes list rows. Each column has a phone slot: **title** (first column by default), **value** (right of the title: cost, status), **caption** (one mono line under the title, parts joined by middots), the default **grid** (labelled figures three to a line), or hidden (left to the detail sheet). A chevron marks rows that open a sheet; failed rows get a red leading edge | `Table.tsx` (`Column.phone`, `phoneSlots`), `phone.css` |
| Long lists stay cheap | `content-visibility: auto` on rows; tables start at 20 rows on a phone (Show more adds the rest) | `Table.tsx` (`PHONE_LIMIT`) |
| Scrub the player with a thumb | Charts draw at 75% height, take a tap or a sideways scrub (`touch-action: pan-y`) and pin the tooltip above the plot, clear of the finger. A vertical swipe scrolls the page; a tap elsewhere drops the tooltip | `Chart.tsx`, `usePhone.ts`, `.chart-tooltip` |
| Popovers rise from the sheet | The Explain popover docks above the tab bar as a card over a scrim | `.explain-pop` |
| Safe areas | `viewport-fit=cover`; top bar, dock and sheets pad with `env(safe-area-inset-*)` | `build.ts`, `phone.css` |
| 46 to 58 px controls | Every button, segment, input, tab, legend chip and nav row is at least 44 px. Inputs are 16 px so iOS does not zoom on focus | `phone.css` |
| Reduced motion | Sheets, pops and sliding keys stop animating; state still changes | `phone.css` (last block) |

The serif is Instrument Serif Italic from `assets/fonts` (OFL, already in the repo for the Wrapped cards).
`build.ts` inlines it as a data URL, so the single-file snapshot still works offline. It adds 96 KB to the
CSS.

## What stays out

- No haptics. Sound Atlas ticks on every detent; a stats page has no detents worth feeling, and iOS needs a
  hidden switch input to fake it.
- No snap points on the dock. Sound Atlas's sheet has three heights because it shares the screen with a
  map. The dashboard's content is the page itself, so the dock stays one height and the page scrolls.
- No second font for headers. Archivo Black would add 90 KB for twelve-character labels; the system sans at
  800 weight with tracking is close enough at 12 px.

## Checking it

`stats/audit/audit.mjs` renders every page of a snapshot at 390 x 844 and 430 x 932 (touch, DPR 3), light and
dark, plus five interaction states (page sheet, range sheet, chart tap, request sheet, Explain). It reports
horizontal overflow, tap targets under 44 px (a checkbox counts as its label) and console errors, and saves
every screenshot. The page clock is pinned to an hour after the snapshot, so relative times match run to run.
With `--rebundle` it keeps a published snapshot's data and swaps in the current `stats/dist`, so before and
after differ only by code. `--desktop` renders 1440 x 900, `--reduced-motion` emulates that preference, and
`stats/audit/diff.mjs` counts changed pixels between two runs.

```sh
cd stats && bun run build.ts && cd ..
node stats/audit/audit.mjs --html ~/.local/share/z0live/wrapped/stats/index.html --rebundle --out /tmp/phone
node stats/audit/audit.mjs --html <published.html> --desktop --out /tmp/desk-before            # old client
node stats/audit/audit.mjs --html <published.html> --rebundle --desktop --out /tmp/desk-after  # new client
node stats/audit/diff.mjs /tmp/desk-before /tmp/desk-after
```

`stats/audit/browser.mjs` finds Playwright and a Chromium headless shell on the machine (an npx cache works).
`PLAYWRIGHT_MODULE` and `CHROME` override it.
