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
