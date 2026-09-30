# System info is a full-screen dialog

`SystemInfoModal.svelte` used to be a 420 px card floating over the app, so the
process list — a command line per row — was cut off after a few rows and the
rest needed the page behind it to scroll. It is now a full-bleed panel.

## What changed

- `Modal.svelte` gained a `full` prop. In that mode the panel's own base classes
  are *built* (`w-full h-full overflow-hidden border shadow-2xl`) instead of
  laid over the card's, because `class="rounded-none"` from the call site does
  not reliably beat the base `rounded-3xl` — utility order in the generated
  stylesheet decides, not attribute order. Non-full callers are untouched.
- `SystemInfoModal` passes `full class="flex flex-col"` and splits into three
  bands: a pinned header (title, hostname, a ✕ — a full-bleed panel has no
  overlay left to click, so closing needed a control of its own), a
  **scrolling** body (`flex-1 min-h-0 overflow-y-auto overscroll-contain`), and
  a pinned footer with the restart controls. The negative-margin padding trick
  keeps the scrollbar at the panel edge while the content stays inset.

## Verified

Harness probe `.cptr/harness/probe-system-info-fullscreen.js` (opens the dialog
from the sidebar footer menu, measures it, closes it with ✕):

| viewport | panel | body | scroll |
| --- | --- | --- | --- |
| 1280×900 | 1280×900, radius 0 | 751 px | content fits |
| 420×740 | 420×740, radius 0 | 603 px vs 896 px | scrolls, header/footer stay put |
| 400×720 | 400×720 | 583 px vs 813 px | 0 children wider than the panel |

Computed styles in the user's `bw` (mono) theme: panel `#fff` on a 1 px `#000`
edge, title and buttons pure `#000`, no grey fills — paper and ink, as the mono
themes require. Closing the dialog via ✕ works; Escape is unchanged.

Frontend-only: `npm run build` (exit 0) is enough, no server restart.
