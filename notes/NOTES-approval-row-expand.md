# Approval rows: read the whole request

## The complaint

On the board (workspace dashboard), a pending approval showed as one ellipsised
line. The half of the sentence that says *what is being asked for* was the half
cut off:

> Refresh the prod image — aijly-app:prod was built 2026-09-18, two days behind aijly-app:dev (2026-09-20), so unpromoted dev fixes are missing. Prod is no longer…

381 characters, of which ~64% were painted (scrollWidth 2366 vs clientWidth 551).
There was a `title=` tooltip, but that is hover-only — no use on a touch device,
and no use on the e-ink tablet.

## Where the truncation lives

`cptr/frontend/src/lib/components/WorkspaceDashboard.svelte`, and nowhere else:
the pending-request list is the only place in the app that renders
`pending_requests` (the API returns them through `getTodos`). Two clipped
classes:

- `.row-label` — the title, `nowrap` + `ellipsis` (pending requests, and todo rows)
- `.row-detail` — the status line under a todo row, `nowrap` + `ellipsis`. This
  one can carry a job's `last_error`, i.e. the one string you most need whole.

## The fix

The label is now a `<button class="row-title-btn">` with a chevron
(`chevron-right` closed → `chevron-down` open). Clicking it toggles one row at a
time (`expandedRowId` / `expandedRequestId`, both `$state<string | null>`), and
while open `.row-label.expanded` / `.row-detail.expanded` drop `white-space:
nowrap`, `overflow: hidden` and `text-overflow: ellipsis` for `normal` /
`visible` / `clip` plus `overflow-wrap: anywhere`.

Notes on the shape of it:

- The button inherits `font`/`color` and paints nothing (`background:
  transparent; border: 0`), so the row looks the same as before. The only added
  ink is the chevron, which uses `var(--app-fg-muted)` — solid ink in the mono
  themes (`appearance.ts` sets it to `monoPalette.foreground`), so no grey.
- The hover affordance is an underline, not a fill: an inversion would fight the
  `.todo-row.attention` ink bar and the mono themes have no mid-tones to spend.
- `.todo-row.expanded` switches to `align-items: flex-start` so Approve/Reject
  stay level with the first line instead of floating down the middle of a
  paragraph.

## Verified

Probes in `.cptr/harness/` (against the live instance, `/home/brendan/infra`,
13 pending requests):

- `probe-prod-expand.js` — the exact row from the report: 381 chars, clipped
  before (scrollWidth 2366 / clientWidth 551, one painted line), open after
  (5 lines, 98px, `clipped: false`), Approve still hit-tested as itself.
- `probe-pending-expand.js` — a second click collapses again
  (`aria-expanded: false`, clipped again); the Approve button stays hittable
  while open.
- `probe-todo-row-expand.js` — todo rows: title 1 line → 2, status line
  unclipped, checkbox intact.
- `probe-touch-expand.js` + a real finger tap (`cdp.mjs --touch --tap-on`) at a
  420px viewport: `aria-expanded: true`, `clipped: false`, row grows to 137px.

`cdp.mjs` gained `--tap-on <file>/*`: `Input.dispatchTouchEvent` start/end at the
element's centre. A `TouchEvent` dispatched from `--js` is not a gesture Chrome
will turn into a click, so it can never distinguish "this control ignores taps"
from "the probe was not a tap".

## Harness gotcha

The dashboard takes ~15–20s to paint its rows in the harness browser (the SPA
boot, not the API — `/api/todos` answers in ms). A probe that waits for
`.dashboard-loading` to clear passes straight through, because the element does
not exist yet; wait for the row you want (`.row-label.pending`) with a 45s
budget, and note that `--click-on` / `--tap-on` run *before* `--js`, so they need
a long `--wait` to see anything at all.
