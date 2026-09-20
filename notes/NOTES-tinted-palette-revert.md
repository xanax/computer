# The b&w commit's tinted-palette damage, reverted

Request: *undo what the e-ink (b&w) work cost `light` / `dark`, without touching
`bw` / `bw-dark`.*

## Audit — every deletion in `de1773b` ("b&w", 18 Sep)

16 files lost lines, all frontend. They sort into three buckets:

1. **Gated, so the tinted palette keeps its old value in the ternary** —
   FileEditor (added/modified line marks), Terminal (cursor blink, ANSI palette),
   OutputEditView (`oneDark`), MermaidBlock (`dark`/`default`), CodeBlock's
   `:global(.mono)` diff rows. Nothing to do.
2. **Literal → shared `--app-*` var that *is* that literal** — `--app-fg-muted`
   (62% mix), `--app-fg-subtle` (48%), `--app-hover` (6%), `--app-active` (7%),
   `--app-checker` (= `--app-hover`), `--app-bar-scrim` (its fallback is the old
   gradient), `--app-border`, `--app-scrim`. Took over DropdownMenu, SaveDialog,
   ChatInput, both suggestion popups, ImagePreview, SvgPreview. Identical in
   light/dark (only SaveDialog's 7% hover became `--app-active`, and ChatInput's
   `pre` 5% became `--app-hover`'s 6% — a 1% nudge). Nothing to do.
3. **Genuinely different in light/dark** — the list below.

| Surface | `de1773b` did | Verdict |
| --- | --- | --- |
| ChatPanel chat-header bar | `color-mix(--app-fg 8%)` → `var(--app-border)` (1.5%) | reverted |
| CodeBlock `<pre>` class | `text-[#24292e] dark:text-[#e1e4e8]` → `text-gray-800 dark:text-gray-200` | **kept** (see below) |
| GitBar | deleted one of *two* maximize buttons (`toggleMaximize` sat at 1603 and 1968) | deliberate dedup |
| Settings → Appearance | theme picker grew `bw` / `bw-dark` chips | intended |
| `utils/appearance.ts` | `Theme` union + `mono` branch | intended |

CodeBlock is the interesting one: the grey-ramp class is what makes the block
collapsible to ink/paper in mono (`.mono` re-points `--color-gray-800/-200` at
`--app-fg`/`--app-bg`, per app.css), while the literal would leak `#24292e` (a
grey, not ink) onto `bw` paper. In light/dark the two differ by a hair
(gray-800 ≈ `#1e2939`, gray-200 ≈ `#e5e7eb`), so the literal was *not* worth
restoring.

The header bar is the reverse: it carries a `border-color` but no `border-width`,
so 8% vs 1.5% never painted. It is reverted for faithfulness, and it is the hook
mono now uses: `appearance.ts` sets `--app-bar-border: <ink>` under `mono` and
removes it otherwise, and ChatPanel asks for
`var(--app-bar-border, color-mix(in oklab, var(--app-fg) 8%, transparent))`.

## The one that *was* visible: the workspace heading plate

Not `de1773b` itself — the inverted plate arrived with `95bfbf4` ("Show each
workspace's share of tracked time") and was pinned in all four themes. It reads
as a heavy bar in `light`/`dark`; see NOTES-workspace-heading-inversion.md for
the mono half.

`SidebarWorkspaceList.svelte`:

```css
.ws-heading          { color: var(--app-fg-muted); }        /* quiet label */
.ws-heading:hover    { color: var(--app-fg); }
.ws-heading-current  { border-color: var(--app-fg-muted); color: var(--app-fg); }

/* mono keeps the old treatment, now that it is the only palette that needs it */
:global(.mono) .ws-heading         { background: var(--app-fg); color: var(--app-bg); }
:global(.mono) .ws-heading-current { border-color: var(--app-bg); }
:global(.mono) .ws-heading .ws-unread { background: var(--app-bg); color: var(--app-fg); }
```

So in light/dark the heading is a Settings-style muted row, and the open
workspace is marked by the same hairline *outline* ChatItem uses for the open
chat — no fill. In mono nothing moved: ink plate, paper name/icons, paper
hairline, paper unread chip.

## Verification

`npm run build`, then `notes/_scratch/probe-theme-revert.py <theme...>` against
the running server (JWT in `/tmp/jwt.txt`; shots in `downloads/_shots/theme-revert-<theme>.png`).
It reads computed colours back through a 1×1 canvas (so `color-mix` and alpha
arrive as pixels) and compares the bar's edge against a reference swatch built
from the pre-`de1773b` declaration.

| theme | heading (current / other) | bar edge | code text |
| --- | --- | --- | --- |
| light | `--app-fg` + `--app-fg-muted` outline / muted, no fill | `[77,77,77,0.078]` = 8% mix ✔ | `#525252` |
| dark | `--app-fg` + `--app-fg-muted` outline / muted, no fill | `[217,217,217,0.078]` = 8% mix ✔ | `#d4d4d4` |
| bw | ink plate + paper outline / ink plate | `#000` (solid ink) | `#000` |
| bw-dark | paper plate + ink outline / paper plate | `#fff` (solid paper) | `#fff` |

`matchesRef: true` for light and dark — the bar's edge is bit-for-bit the
colour it had before `de1773b`. Mono reports solid ink/paper in all four
surfaces, i.e. `--app-bar-border` is doing its job and the plate is untouched.

## Staging note

The working tree also carried another session's perf/telemetry WIP
(`stores/chat.ts`, `utils/perf.ts`, and the `markActivity` / `recordIfSlow`
blocks in ChatPanel). Only the single theme line of ChatPanel.svelte was
committed, staged as a hand-built one-hunk patch (`git apply --cached`); the
perf work is still uncommitted.
