# Mono: the editor's cursor line and selections (ink-on-ink)

Durable summary: [`.agent-kb/areas/mono-codemirror-rendering.md`](../.agent-kb/areas/mono-codemirror-rendering.md) — this file is the probe recipe and the raw numbers. Ledger row: `BUGS.md` B-018.

## The bug

In `bw` / `bw-dark` the file editor was unreadable in exactly the states you are in while editing:

- `.cm-line.cm-activeLine` measured `color: rgb(0, 0, 0)` on `background: rgb(0, 0, 0)` — the cursor line's *bare* text nodes (not spans) were never flipped. The `.mono :where(.cm-activeLine) :where(span)` rule that was meant to do it loses to `.mono .cm-content :where(span)` (app.css:528): `:where()` contributes no specificity, so the token rule's `--app-fg !important` won on the line's text nodes.
- Selecting text produced one solid ink block. `drawSelection` paints `.cm-selectionBackground` rects in `.cm-selectionLayer`, a **sibling of `.cm-content`** that sits behind the text, so the slab and the glyphs over it were the same colour and no selector could separate them.
- The caret was `--app-fg` (ink) — correct off the bar, invisible on it.

## The fix

- `cptr/frontend/src/app.css`, inside the `.mono` block (nothing outside it changed): `.cm-content .cm-activeLine` + spans flip to `--app-bg`; `.cm-content .cm-mono-selected` flips glyphs over a selection; `.cm-activeLine .cm-mono-selected` re-inverts that pair (paper slab, ink text); match/bracket frames get a paper inset frame on the bar; `.cm-cursor-primary` → `--app-bg`, `.cm-cursor-secondary`/`.cm-dropCursor` stay `--app-fg`.
- `cptr/frontend/src/lib/editor-mono.ts` (new): `monoSelectionMarks`, a mark decoration that tags every non-empty selection range with `.cm-mono-selected` — the only way to reach the characters a slab covers. Added to `FileEditor.svelte` and to the chat `OutputEditView.svelte`, both gated on `.mono`.

## How to re-run the probe

```bash
cd ~/computer
# server: cptr on :4200 (frontend is prebuilt — npm run build in cptr/frontend first, ~12 s)
for th in bw bw-dark; do
  node .cptr/harness/cdp.mjs \
    --url "http://127.0.0.1:4200/?workspace=/home/brendan/computer&file=/home/brendan/computer/Dockerfile&cb=$(date +%s)" \
    --cookie cptr_theme=$th --js .cptr/harness/probe-editor-mono-fix4.js \
    --shot /tmp/mono-$th.png --wait 15000 \
  | python3 .cptr/harness/parse-mono-fix.py
done
```

`--wait 15000` matters: at 7–9 s the app has not always mounted the editor and the probe returns `{err: 'no visible editor', editors: 0}` while the harness still exits **0**. The `&cb=` suffix defeats a cached `index.html` after a rebuild. `--js` files must `return` their value (the wrapper is `(async () => { … })()`).

## Measured (final build, both palettes)

`bw` — cursor line `color rgb(255,255,255)` on `bg rgb(0,0,0)`; a selection on the bar `color rgb(0,0,0)` on `bg rgb(255,255,255)`; `.cm-cursor-primary` `rgb(255,255,255)`, `.cm-cursor-secondary` / `.cm-dropCursor` `rgb(0,0,0)`. `bw-dark` — every one of those inverted.

Pixel scan of the same shots: the cursor-line bar (empty half) 100 % ink, the plain selected line's newline slab 100 % ink, an untouched line 0 % ink — 0.00 % of those surface pixels mid-tone, in both palettes. The sampled caret column is a single value (`255` in `bw`, `0` in `bw-dark`). On the selected plain line the glyph runs alternate paper-on-ink (`304D 306L 307D 309L …`) and on the cursor line they alternate ink-on-paper (`306L 311D 313L 318D …`), which is the inversion the palette promises.

Not 1-bit overall, and not supposed to be: 5.6 % (`bw`) / 6.1 % (`bw-dark`) of the viewport, and 12–13 % of a text band, are mid-tone pixels — **font antialiasing**, the renderer's, identical before and after this change. The fix touches only surfaces (bar, slab, frames, caret border), so it adds no grey; whether `.mono` should also set `-webkit-font-smoothing: none` is a question for the panel, not for this rule.
