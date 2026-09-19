# The question line under the chat title

Request: under the chat title, show the first line of the question whose answer is
currently being viewed — truncated with an ellipsis if it doesn't fit.

## Where it lives

`cptr/frontend/src/lib/components/chat/ChatPanel.svelte`, all of it. The header is
the `h-7` bar at the top of the conversation view (`{#if !isLanding}`), the one
carrying `displayChatTitle` and the new-chat / status buttons.

- The transcript list is `visiblePath` (the last `visibleCount` entries of
  `activePath`); each user row now sits in a wrapper div carrying
  `data-question={msg.meta?.internal ? undefined : questionLine(msg.content)}`.
  Internal rows (timer, async subagent) deliberately have no attribute, so they
  never become the label.
- The label itself is an absolutely positioned `top-full` button inside the bar,
  so it paints over the transcript without changing the bar's height. Its
  `aria-label` is `chat.previousQuestion`.
- `questionLine()` reduces a message to one plain line: the first non-empty line,
  with markdown list/heading/quote markers stripped and the two file-mention forms
  (`[label](file://path)`, TipTap `[@ id="…" label="…"]`) collapsed to their label.
  Truncation is CSS (`truncate`), so a long line gets a real ellipsis; the full
  text is on the element's `title`.

## When it shows

`measureScrolledQuestion()` walks the `[data-question]` rows in visual order and
keeps the last one whose bottom is above `readableTop()` — the top of the
readable transcript. That is "the question whose answer fills the screen". A
question still on screen ends the walk, so the line never repeats text already
visible; in a short chat that fits without scrolling it never appears at all.

`readableTop()` is the bottom edge of the bar's own scrim element
(`bind:this={headerVeilEl}`): the scrim already paints over the transcript, so
its bottom is exactly where readable content begins. Reading the element keeps
the bar height, its `-mb-12` and the scrim's `-bottom-10` out of this file's
arithmetic — an earlier fix read the transcript's `pt-16` via `getComputedStyle`,
which meant a style flush mid-measure *and* a second place to update whenever the
padding changed. Before the ref lands it falls back to the scroll box's top. One
`getBoundingClientRect` per element per measure, and the measure is rAF-coalesced.

## Clicking it

The button walks one question further back: `goToShownQuestion()` scrolls that
row to just below the readable top (`QUESTION_LANDING_PAD = 8`). The scroll is
instant, not smooth — on an e-ink panel a smooth scroll repaints as a smear — and
because it moves upward it disengages auto-scroll (see `handleMessagesScroll`), so
the transcript stays where the jump put it. The line then names the question
*before* the one now on screen, so repeated clicks climb back up the history;
above the first question there is nothing to name and the line disappears.

Re-measure is rAF-coalesced and fires from: `handleMessagesScroll`, an `$effect`
on `visiblePath`/`active` (branch switch, reload, compaction, another history page
loaded), the click itself, and on destroy the pending frame is cancelled.

Colour is `text-gray-600 dark:text-gray-400` → `--app-fg-muted`, which the mono
themes set to solid ink, so the line stays pure ink-on-paper in `bw`/`bw-dark`
(paper-on-ink in `bw-dark`) with no grey.

## Verification

Headless Chrome driving the real build in a second cptr instance on `:4300` with
its own data dir (`~/.cptr-verify`), against a seeded six-question chat
(`notes/_scratch/make-verify-chat.py`: every question followed by a multi-screen
answer). Opening the chat lands at the bottom, scroll height 11 335 / viewport
782, bar scrim bottom at y=104 — then the line was clicked repeatedly:

| click | line said | scrollTop | landed row top | line then said |
| --- | --- | --- | --- | --- |
| 1 | Question 6 | 10553 → 9321 | 112 | Question 5 |
| 2 | Question 5 | 9321 → 7450 | 112 | Question 4 |
| 3 | Question 4 | 7450 → 5580 | 112 | Question 3 |
| 4 | Question 3 | 5580 → 3709 | 112 | Question 2 |
| 5 | Question 2 | 3709 → 1839 | 112 | Question 1 |
| 6 | Question 1 | 1839 → 0 | (clamped at the top) | — line gone |

Every jump lands the named question 8 px below the scrim (`112 = 104 + 8`), so it
arrives in view rather than balanced on the header's edge, and the only rows left
below it are the ones that follow — the visible set shrinks to just that question
on the first jump and grows back as the walk climbs. After the sixth click the
transcript is at the top and there is nothing left to name: the line renders empty
and the button is not there to click again (`walk.js` reports `line gone`).

Mono purity was measured on the same build with the real preference API
(`PUT /api/state/preferences`, `appearance.theme`) — written while the app was
*not* loaded, because a running tab re-persists its own preferences and clobbers
the change. Hover was applied with a real
`Input.dispatchMouseEvent` rather than `CSS.forcePseudoState` — the CDP-forced
`:hover` did **not** take (computed style stayed at rest), so the harness now
moves the mouse onto the element's centre:

| theme | state | button background | button colour |
| --- | --- | --- | --- |
| `bw` (paper #fff / ink #000) | at rest | transparent over the paper bar | `rgb(0,0,0)` |
| `bw` | hovered | `rgb(0,0,0)` | `rgb(255,255,255)` |
| `bw-dark` (ink #000 / paper #fff) | at rest | transparent over the ink bar | `rgb(255,255,255)` |
| `bw-dark` | hovered | `rgb(255,255,255)` | `rgb(0,0,0)` |

Both hover states come from `app.css:754` (`.mono :where([class*='hover:bg-']):hover`),
the rule that turns a Tailwind hover wash into a solid inversion, and the label,
chevron and bar scrim all follow. A pixel scan of the captured band
(`downloads/_shots/chat-question-line-bw-hover.png`) is 95.0 % pure ink, 0.08 %
pure paper and 0.5 % grey, with the remaining 4.4 % confined to rows 70–79 of the
first 210 px — the glyphs' own subpixel-antialiasing fringes, not a fill.

Scaffolding: the CDP driver and probe scripts live in the gitignored
`.cptr/harness/` (`cdp.mjs`, `walk.js`, `line-state.js`, `hover-line.js`,
`mono-set.js`), and `notes/_scratch/make-verify-chat.py` seeds the chat. Note that
`CSS.forcePseudoState` did not drive `:hover` in this Chrome build; `cdp.mjs`
moves a real mouse onto the element instead (`--mouse-on`).
