# Mono themes: disappearing fills / labels (app.css veil-wipe fix)

## Symptom
In `bw` / `bw-dark`, the **Open** button in `DirectoryPicker.svelte:578` rendered with no
fill at all, so its paper label sat on paper: an invisible button. The same class pattern
appears in `SaveDialog.svelte:233`, `GitBar.svelte:2133`, `AssistantMessage.svelte:446`,
`UserMessage.svelte:214`.

## Root cause
`app.css:617` — `.mono :where([class*='bg-white/'], [class*='bg-black/']) { background-color: transparent }`
— matches on a **substring**. Any element that merely *mentions* an alpha utility anywhere in
its class list (`bg-gray-900 … dark:bg-white/6`) had its solid fill wiped. The rule is
unlayered, so it also beat the Tailwind utilities layer: the element's own `bg-gray-900` could
not restore it. Paired with `.mono :where([class~='text-white']) { color: var(--app-bg) }` the
result was a transparent button with paper text — the fill was only visible **while hovered**
(`app.css:754`, `!important`).

## Fix (2 hunks, +46 lines, `cptr/frontend/src/app.css`)
Both blocks are placed **after** the wipe so they win the tie on order alone (`:where()` keeps
the specificity identical):

1. **Re-assert the ramp's two real surfaces** (`app.css:668`): a solid utility is not a wash.
   `bg-black` + `bg-gray-300…950` → `var(--app-fg)`; `bg-white` + `bg-gray-50/100/200` →
   `var(--app-bg)`.
2. **A veil overlay flips its own label** (`app.css:638`): the `bg-white/70…90` overlays of
   `BrowserPreview.svelte:379,386` carry `text-gray-500` *on the veil element itself*, which
   mono maps to `--app-fg-muted` = ink → ink label on the ink veil ("Reconnecting…",
   "Connection lost"). Scoped by plain token, so `dark:bg-white/90` (a button surface) is
   untouched, and dialog scrims are excluded: their panels still inherit ink for unstyled
   text, exactly as before.

## Verification
Headless Chrome, fresh build, `transition`/`animation` killed, `.mono` applied to
`documentElement` with the ink/paper custom properties:

* **Sweep** of all 731 distinct class strings found in the codebase, measuring computed
  background + colour per element:

  | theme | grey bg / grey text | wiped ink / paper surfaces | invisible text |
  |---|---|---|---|
  | bw | 0 → 0 | 9 / 33 → **0 / 0** | 13 → 5 |
  | bw-dark | 0 → 0 | 9 / 33 → **0 / 0** | 64 → 5 |

* **Composite probe** of the real component patterns (both themes, every case PASS):
  primary button on paper → ink fill + paper label; primary inside an ink bar → ink/paper;
  disabled secondary button → paper fill + ink label; dialog scrim → ink surface whose panel
  stays paper and whose colour does **not** leak into the panel; browser-preview veil → ink
  with paper label; input / code block / alpha-wash chip → paper surface + ink text.

* Screenshots of the probe page: `downloads/_shots/mono-theme-bw.png`,
  `downloads/_shots/mono-theme-bw-dark.png`.

The 5 remaining sweep flags are the same in both themes and are false positives (bare
`text-white` snippets that live inside ink surfaces), except one **pre-existing** bug:

## Pre-existing, not mono-specific: `SendButton.svelte:64`
The idle state is `text-white bg-gray-200 dark:text-gray-900 dark:bg-gray-700`, i.e. a
`fill="currentColor"` arrow that is white on gray-200 — **1.24:1 in the light theme too**
(measured oklab ≈ `#e5e7eb`; mono is the same pair at 1:1). Identical before and after this
change, so it is a component bug, not a palette one. One-token fix if wanted: drop `text-white`
for a mid grey (`text-gray-400 dark:text-gray-900`).

## Housekeeping
* Frontend is served prebuilt: `npm run build` in `cptr/frontend` then reload the page — the
  CSS filename hash changes, so no stale cache and **no server restart** (the live session is
  untouched).
* `npm run check` reports 1461 errors / 198 warnings in 108 files; pre-existing and unrelated
  (this change is CSS-only).
* `app.css` is not prettier-formatted at HEAD either (1297-line diff), so
  `npx prettier --check src/app.css` failing is pre-existing; the added blocks follow the
  file's existing style (tabs, `:where(` with tab-indented selectors).
* All test scaffolding (sweep/probe pages, analyser scripts, screenshot probes) was deleted.
