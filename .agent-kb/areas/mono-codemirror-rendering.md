---
area: mono-codemirror-rendering
title: CodeMirror under the mono palettes — the cursor line, selections and carets
aliases: [cm-activeLine, cm-mono-selected, mono-editor, ink-on-ink, FileEditor, selectionLayer]
updated: 2026-10-02
---

# CodeMirror under the mono palettes — cursor line, selections, carets

## Map
- `cptr/frontend/src/app.css` — the `.mono` block (~:380 `/* @mono:begin */` … `:600`), which is where every CodeMirror mono rule lives. `.mono` is applied to `<html>`, next to `bw`/`bw-dark`.
- `cptr/frontend/src/lib/editor-mono.ts` — `monoSelectionMarks`, the one shared CodeMirror extension (a mark decoration) both editors need in mono.
- `cptr/frontend/src/lib/components/FileEditor.svelte` — the file editor. `initEditor` (~:900) builds the extension list; a `MutationObserver` on `document.documentElement`'s class re-runs it on a theme swap (~:1010).
- `cptr/frontend/src/lib/components/chat/OutputEditView.svelte` — the chat message-output JSON editor; the other `@codemirror` mount in the tree (grep `@codemirror` finds no third).
- `.cptr/harness/probe-editor-mono-fix4.js` — the probe; `.cptr/harness/parse-mono-fix.py` prints its JSON; `notes/NOTES-mono-codemirror-inversion.md` has the numbers.
- `notes/NOTES-mono-theme-veil-fix.md` — the other mono-app.css trap (the `[class*='bg-white/']` wipe); `BUGS.md` B-006.

## Facts
- [verified 2026-10-02 DOM + computed style] A selection in CodeMirror is **three** things at once: `.cm-selectionBackground` rects in `drawSelection`'s own layer, the native `::selection` it suppresses, and the characters themselves. The slab is painted in `.cm-editor > .cm-scroller > .cm-selectionLayer`, a **sibling of `.cm-content`** — it sits *behind* the text, so a selector that reaches the slab can never reach the glyphs it covers. Two inks therefore read as one block.
- [verified 2026-10-02] `.cm-cursorLayer` is likewise a sibling of `.cm-content` (`.cm-cursor.cm-cursor-primary` / `.cm-cursor.cm-cursor-secondary` / `.cm-dropCursor`, all 1px `border-left`). No selector can tell which line a caret is on, so a caret cannot be themed per surface.
- [verified 2026-10-02 cptr/frontend/src/app.css] `.mono :where(...)` contributes **zero** specificity: `:where()` is stripped for specificity, so `.mono :where(.cm-activeLine) :where(span)` (0,1,0… actually 1 class) loses to `.mono .cm-content :where(span)` — which exists at :528 to kill syntax colour. Any mono rule that must beat it has to name `.cm-content` **and** the line class outside `:where()`.
- [ran 2026-10-02 both palettes, screenshot + computed style] After the fix, `FileEditor` on `Dockerfile`: cursor line `color: rgb(255,255,255)` on `background: rgb(0,0,0)` in `bw` (`bw-dark` exactly inverted); a selection *on* the bar is a paper slab `rgb(255,255,255)` with ink text; a selection on a plain line is paper glyphs over the ink slab; `.cm-cursor-primary` paper, `.cm-cursor-secondary`/`.cm-dropCursor` ink. Pixel scan of those shots: every **surface** the fix touches is pure — the cursor-line bar and the selection slab 100 % ink, an untouched line 0 % ink, and 0.00 % of those pixels mid-tone in both palettes. The shot is not 1-bit overall: 5.6 % (`bw`) / 6.1 % (`bw-dark`) of the viewport is mid-tone, 12–13 % of the glyph band — all of it **font antialiasing**, which is the renderer's, not the palette's, and unchanged by this fix. Nothing in the mono block sets `-webkit-font-smoothing`, so text edges stay antialiased on the panel.
- [verified 2026-10-02] The mono rules in app.css reach **every** `.cm-editor` under `.mono` (they are global, not scoped to FileEditor) — so `OutputEditView`'s active line was already flipped by the same rules, while its *selection* needed the mark extension the CSS keys off.
- [verified 2026-10-02] `?file=<path>` does **not** decide which file the pane shows: the open tabs come from persisted workspace state and the harness profile keeps them. Several persisted tabs stay mounted at full size (`.persisted-tab-hidden` only hides them — `getBoundingClientRect()` is non-zero), so `document.querySelector('.cm-editor')` can return an invisible one. Filter `!el.closest('.persisted-tab-hidden')`, then take the largest.
- [ran 2026-10-02 cdp harness] `--js` fires on a wall-clock `--wait` and the app can still be mounting: `--wait 9000` twice reported `no visible editor` (`editors: 0`) while `--wait 12000…15000` on the same build found it. The harness exits **0** in both cases, and its `--js` file must `return` its value (the wrapper is `(async () => { … })()`), otherwise the run prints `undefined`.

## Built
- `cptr/frontend/src/lib/editor-mono.ts` — `monoSelectionMarks`: a `Decoration.mark({ class: 'cm-mono-selected' })` over every non-empty selection range, gated by the caller on `.mono` being present. Imported by `FileEditor.svelte` and `OutputEditView.svelte`.
- app.css, all inside the `.mono` block: `.cm-content .cm-activeLine` + its spans flip to `--app-bg` (no `:where()` on the line); `.cm-content .cm-mono-selected` flips the glyphs over a selection, and `.cm-activeLine .cm-mono-selected` inverts the pair the other way (paper slab, ink text); `.cm-searchMatch`/`.cm-selectionMatch`/`.cm-matchingBracket` get a paper inset frame when they land on the bar; `.cm-cursor-primary` flips to paper, `.cm-cursor-secondary`/`.cm-dropCursor` stay ink.

## Decisions
- Mark the selected ranges rather than recolour the selection slab: the slab is behind the text, so it is the only handle on the characters — and one mark drives both halves (ink slab off the bar, paper slab on it).
- Keep the mark in one shared module instead of duplicating 8 lines per editor: a CodeMirror extension value is immutable and reusable across editor instances.
- Split the caret by primary/secondary instead of a global flip: one colour makes the caret vanish either on the bar (`--app-fg`) or on the paper lines (`--app-bg`).
- A selection that lands on the bar is inverted *again* (paper slab) rather than left as the bar alone: with no slab the selected characters would be indistinguishable from the rest of the cursor line.

## Dead ends
- Recolouring `.cm-selectionBackground`: the slab is behind the glyphs, so making it ink is exactly what produced the ink-on-ink block. It only helps as the slab *under* a paper glyph colour.
- A `.mono :where(.cm-activeLine) :where(span)` rule (as it was written): `:where()` zeroes its own specificity, so the token rule at :528 wins and the wrapped line's bare text nodes stay ink. Same trap for any future mono CM rule — check the rule against `.cm-content :where(span)` before assuming it applies.
- `.cm-content ::selection` / `:window-inactive` rules: CodeMirror hides the native selection (`.cm-content ::selection { background: transparent }`) and paints its own layer, so these do nothing.
- `scripts/generate-mono-css.py`: app.css:383 names it in a comment, but `ls scripts/` and a filesystem-wide `find` find no such file. Do not plan around regenerating the `@mono:end` block.

## Do not redo
- "Is the bar painted?" — it always was; the defect was only that the text on it was not flipped. Probe the *colour pair*, not the background.
- Probing for the editor with a bare `.cm-editor` query, or with a short `--wait`: filter `.persisted-tab-hidden` and give the app 12–15 s, then confirm the probe actually returned an object with `activeLine` in it before believing a negative.
- Re-deriving that `OutputEditView`'s active line needed the mark: its *active line* was already handled by the global rules; only its selection was not.

## Open
- The inner-edge `box-shadow: inset 0 0 0 1px var(--app-bg)` on a match that lands on the bar is reasoned, not separately probed (no search-match on the cursor line was captured).
- Glyph antialiasing is untouched, so the panel still dithers text edges (5.6 % of the viewport, measured). Disabling it (`-webkit-font-smoothing: none` under `.mono`) would trade legibility for a hard 1-bit edge — not attempted, and a question for the panel rather than a bug.
- The mono block is hand-maintained (no generator): a new CodeMirror subclass that paints its own surface (`cm-gutterElement` tooltips? other view plugins) will need the same ink/paper pair by hand.
- `svelte-check` still reports ~1.5k pre-existing errors; the build (`npm run build`) is the gate that matters here, and it passes in 12 s.
