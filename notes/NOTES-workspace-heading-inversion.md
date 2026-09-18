# Workspace headings as inverted ink plates

Request: *"the workspace headings id like inverted so they really stand out"*.

## What changed

`cptr/frontend/src/lib/components/SidebarWorkspaceList.svelte` — the workspace row
(the name that heads each group of chats in the sidebar) is now an inverted plate
instead of grey text on paper:

```css
.ws-heading        { background: var(--app-fg); color: var(--app-bg); border: 1px solid transparent; }
.ws-heading-current{ border-color: var(--app-bg); }          /* paper hairline frame */
.ws-heading .ws-icon-toggle .ws-icon-chevron,
.ws-heading .ws-heading-action { color: var(--app-bg); }     /* paper icons in the plate */
.ws-heading .ws-unread         { background: var(--app-bg); color: var(--app-fg); }  /* paper chip */
```

Design notes:

- This *is* the mono palette's own "solid inversion" rule (ink surface, paper
  text), so it needs no mono-only branch: it reads correctly in `bw`, `bw-dark`,
  `light` and `dark` (grey plate with paper text / paper plate with ink text).
- `--app-bg` / `--app-fg` are set at runtime in `lib/utils/appearance.ts`, so the
  plate follows any theme without extra CSS.
- Everything *inside* the plate is forced to paper, because the sidebar's own
  greys (and the mono ramp, where grey collapses to ink) would paint ink on ink:
  chevron, the two hover actions, and the unread count.
- The open workspace keeps its plate and gains a paper hairline frame — the only
  "selected" mark that survives a pure ink/paper palette (no grey wash, no dither).
- The unread badge is a paper chip cut into the ink plate (`--app-fg` text on a
  `--app-bg` chip) — a solid inversion, not a blue wash. The scoped rule beats the
  global sky/accent utilities on specificity, in every theme.
- No hover-only styling was added: the icon chevron swap + action fade-in still
  happen, and hover has no meaning on e-ink.

## Verification (three independent checks)

Run against the live app (`http://127.0.0.1:4200`, JWT at `/tmp/jwt.txt`):

1. `notes/_scratch/probe_ws_heading.py <theme> <tag> [settings] [path]` — computed
   colours of every heading (plate/name/icons/action/badge/border). bw + bw-dark:
   16/16 rows ink plate + paper text; `cur=True` row has the paper border, others
   transparent.
2. `notes/_scratch/probe_ws_render_px.py <theme>` — screenshots **single pixels**
   of each row (1x1 CDP clips decoded by `notes/_scratch/pngpx.py`, no image lib)
   and asserts plate pixel == ink, pixel just above == paper. bw: 16/16 ink-on-paper.
   NB coloured fringes on glyph edges are Chrome subpixel AA, not fills.
3. `notes/_scratch/probe_ws_badge.py <theme>` — injects a real `ws-unread` span
   (plus a control copy outside the plate) and reads back computed colours:
   paper chip with ink text in all four themes.

Plus the mono audit (`downloads/_shots/audit.py bw|bw-dark audit-<tag>`):
`{"distinct":0,"flaggedElements":0}` — no grey/mid-tone paint anywhere.

## Gotcha worth remembering

Svelte 5 compiles component CSS to `.ws-heading.svelte-xxxx .ws-unread:where(.svelte-xxxx)`.
The `:where(.svelte-xxxx)` part still *requires* the scope class on the element,
so a probe that injects a span with only the Tailwind classes will **not** match
the scoped rule (it silently reports the utilities' colours instead). Copy a
`svelte-*` class off a real heading when injecting test nodes.

## Build

`npm run build` in `cptr/frontend` (UI-only change; the running server serves
`cptr/frontend/build` prebuilt, so no restart — just reload the browser).
