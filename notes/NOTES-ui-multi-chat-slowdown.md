# Why the UI crawls when many chats are open

Diagnosed 2026-09-20. Symptom: "if I have multiple chats running, it slows down the UI."

Two independent causes are covered here: the **mount storm** (why load takes ~32 s, still
open as a design decision) and the **per-block Shiki highlighter** (fixed — see "Second
cause" below). Read the TL;DR for the first, then jump to the second if you are here
because of load time with code-heavy transcripts.

## TL;DR

Every chat tab in a workspace is **eagerly mounted on page load and never unmounted**.
With 43 chat tabs open, the app spends **~32 s of sustained main-thread saturation at
2–7 fps** while mounting them, on every load. After the mount storm finishes the UI is a
clean 60 fps and stays there.

So it is not "streaming from many chats is expensive" and not steady-state layout cost.
It is a **one-shot mount storm whose length is proportional to the number of chat tabs**,
paid on every page load/refresh. Cost ≈ **0.7–0.9 s of stall per chat tab** (headless;
the app's own telemetry suggests ~0.15–0.5 s per tab on real hardware).

## How it was measured

Headless Chromium (CDP harness `.cptr/harness/cdp.mjs`), same browser for all runs, so
numbers are comparable to each other. Workspaces with different tab counts were loaded
and `requestAnimationFrame` gap latencies sampled in 1 s buckets.

**Control:** a 2-tab workspace measures 60.3 fps / p50 16.7 ms in this same browser, so
headless rAF latency is a trustworthy metric here — the low numbers below are the app,
not the harness.

### Time to reach a stable 60 fps

| chat tabs | tabs total | peak DOM nodes | mount-storm fps | time to 60 fps |
|----------:|-----------:|---------------:|----------------:|---------------:|
| 1  | 2  | 1,359  | 5 | ~2 s  |
| 6  | 7  | 2,789  | 3 | ~5 s  |
| 11 | 12 | 7,564  | 3 | ~7 s  |
| 43 | 44 | 29,934 | 2–7 | **~32 s** |

`/home/brendan/computer` is the worst case (43 chat tabs). 1-second buckets for it:

```
1s:60fps/47n      9s:5.6fps/16829n   17s:2fps/27061n    25s:2.9fps/29319n  33s:3.4fps/29746n
2s:60fps/47n     10s:6.5fps/20665n   18s:2.2fps/27102n   26s:2.5fps/29358n  34s:3.8fps/29834n
3s:45fps/468n    11s:7.1fps/23465n   19s:2.3fps/27137n   27s:3.8fps/29372n  35s:3.3fps/29868n
4s:14fps/2714n   12s:7.1fps/25808n   20s:3.4fps/27677n   28s:3.9fps/29414n  36s:3.6fps/29923n
5s:48fps/3446n   13s:2fps/26920n     21s:5.3fps/29274n   29s:3.8fps/29430n  37s:60.3fps/29934n
6s:10.9fps/5445n 14s:2.4fps/26967n   22s:3.7fps/29281n   30s:3.7fps/29555n  38s:60.2fps
7s:3.6fps/8406n  15s:2.3fps/27007n   23s:3.1fps/29291n   31s:4.1fps/29638n  39s:60.1fps
8s:6fps/12831n   16s:1.9fps/27031n   24s:2fps/29311n     32s:3.6fps/29685n  40s:60.1fps
```

Note that the DOM count **plateaus at ~27k nodes by t≈13 s, but the UI stays at 2–4 fps
until t≈37 s**. So the remaining ~24 s is not incremental DOM insertion — it is
per-panel mount work (component init, ProseMirror/TipTap editor construction, effects,
initial layout) running back to back on the main thread.

### Steady state is fine

Once mounted, 43 chat tabs sit at a stable 60 fps with no user input. Idle steady state
is *not* the problem, which is what makes this hard to catch interactively: by the time
you start poking at it, the storm is over.

| after settle | value |
|---|---|
| steady fps, 43 chat tabs | 59.6–60.1 |
| steady fps, 11 chat tabs | 60.0 |
| DOM nodes, 43 chat tabs | 29,934 (≈94% inside hidden tabs) |
| TipTap/ProseMirror editors alive | 43 (one per chat tab) |
| JS heap | 320–630 MB (vs 71 MB at 2 tabs) |

## Root cause in code

`cptr/frontend/src/routes/+page.svelte:1432` — no `{#if}`, no laziness, one `ChatPanel`
per chat tab, all constructed on first render:

```svelte
{#each group.tabs.filter((tab) => tab.type === 'chat') as tab (tab.id)}
  <div class="persisted-tab" class:persisted-tab-hidden={tab.id !== group.activeTabId}
       use:perfMount={{ label: tab.type, active: tab.id === group.activeTabId }}>
    <ChatPanel ... active={tab.id === group.activeTabId} />
  </div>
{/each}
```

`cptr/frontend/src/routes/+page.svelte:1672` — hidden means *unpainted*, not *unmounted*:

```css
.persisted-tab        { position: absolute; inset: 0; z-index: 1; overflow: hidden; }
.persisted-tab-hidden { visibility: hidden; z-index: 0; pointer-events: none; }
```

`visibility: hidden` keeps the subtree in style recalc, in layout, and in memory. All 43
panels stay live, each holding a full `ChatPanel` instance, a TipTap editor, and its own
`~700` DOM nodes.

Already-gated work (so these are **not** the problem):

- `ChatPanel.svelte:928` — history load only on `active && !prevActive`.
- `ChatPanel.svelte:943` — command-session poller gated on `active && docVisible`.
- Streaming deltas gated server-side and client-side on chat visibility.

So the previous perf work correctly removed the *ongoing* per-hidden-tab costs. What it
did not address is the *construction* cost of every panel at once.

## Independent corroboration from the app's own telemetry

`ui_events` over 72 h agrees, and gives real-hardware magnitudes:

- `mount`/chat: p50 127 ms, p95 492 ms, max 2,468 ms (n=2,116) — per-panel cost.
- `tab_switch`/chat: p50 154 ms, p95 989 ms, max 2,355 ms (n=209).
- `long_task`: 6,901 samples, p50 379 ms, p95 671 ms, **6,891 of 7,015 with
  `focused=true`, `hidden=false`** — real foreground stalls, matching the headless
  observation.
- Latency rises with `total_tabs` (bucketed): `mount` p95 goes from ~50 ms at 1–4 tabs to
  >1 s near 35 tabs.

43 tabs × ~0.3 s ≈ 13 s of stall on real hardware — the same shape as the 32 s measured
in headless, scaled down.

## Fixes, in order of leverage

1. **Mount on first activation.** Wrap the chat (and terminal/browser/file) branches in a
   `seen.has(tab.id)` check so a panel is only constructed once its tab has actually been
   shown. The user has 43 chats and looks at one at a time; this removes ~100% of the
   storm for the ~42 never opened. Panels stay mounted afterwards, so tab switching is
   still instant for anything already visited.
2. **Chunk the mount work.** If all panels must exist, don't build them in one synchronous
   burst: mount them through `requestIdleCallback`/staggered `requestAnimationFrame` so the
   UI keeps painting between panels. Turns a 13 s freeze into a background trickle.
3. **Cap mounted panels (LRU).** Keep the N most-recently-used chat panels mounted and
   unmount the rest, re-hydrating on activate (`ChatPanel.svelte:928` already reloads
   history on becoming active, so this is nearly free). Bounds both stall and the
   320–630 MB resident heap. Useful safety net given every workspace grows to N chats.
4. **Consider `content-visibility: auto` + `contain-intrinsic-size`** on
   `.persisted-tab-hidden` as a cheap mitigation for the steady state. Honest caveat: at
   steady state the app already runs at 60 fps, so this buys little — it is not a
   substitute for (1).

Recommend (1) as the core change, (3) as a bounded-memory backstop.

**Status:** the *highlighter* half of this is fixed ("Second cause" below) and shipped in
the running build. Items (1)–(4) above are **still proposals** — tabs are still eagerly
mounted, deliberately: the user keeps all tabs open and rejected lazy-mount, so the
steady-state cost is accepted and the target is the load/streaming cost instead.

## Second cause: one Shiki highlighter per code block (fixed)

The mount storm above is one axis. A second, independent multiplier was found while
profiling the same load: **every `CodeBlock` instance built its own Shiki highlighter.**

`CodeBlock.svelte` (and `SyntaxDiffLine.svelte`, a near copy) each called
`createHighlighterCore({ langs: [...31 grammars...] })` inside the component. On a
45-tab page that is **77 highlighters**, each one registering ~31 grammars on the same
JS thread, and the work is paid on every page load.

### A/B, same 25 s load, same browser, 45 chat tabs, ~70 code blocks

| | highlighters built | first tokens | coverage settled | long tasks | Σ long-task ms | max |
|---|---:|---:|---:|---:|---:|---:|
| **ARM A** (shared singleton) | **1** | 5.6 s | 12.3 s | 64 | **7,969** | 840 ms |
| **ARM B** (HEAD, per-instance) | **77** | 22.0 s | 59.2 s | 198 | **53,098** | 902 ms |

6.7× less long-task time, and the transcript starts highlighting **4× sooner**. The
DOM/mount shape is identical in both arms, so this is purely highlighter construction.

### What landed

`cptr/frontend/src/lib/utils/highlighter.ts` (new) — one module-scope promise, so
concurrent callers share a single highlighter, and a failed creation is not cached:

- `getHighlighter()` — `createHighlighterCore({ langs: [] })`, both themes, oniguruma
  WASM engine. Measured 138 ms once, for the whole page.
- `resolveLanguage()` / `highlightCode()` — map a fence label to a grammar and load it
  **lazily** via `loadLanguage()` (~8 ms each) the first time that label is seen.
- `cacheTokens()` / `takeCachedTokens()` — token cache shared with `SyntaxDiffLine`.

`CodeBlock.svelte` and `SyntaxDiffLine.svelte` now import from it instead of each
constructing a highlighter; a duplicate local token cache in `SyntaxDiffLine` was
dropped with it.

Shiki 3.x details worth keeping (all verified against the installed version):

- Grammars must be imported as `shiki/langs/<name>.mjs`; `langs: []` is legal and the
  built-in `text` grammar works with zero grammars loaded.
- An unknown language **throws** `ShikiError`, so the fallback to `text` must be
  explicit — do not let a fence label reach `codeToTokens` unfiltered.
- `loadLanguage()` resolves aliases itself: bash/sh/zsh → `shellscript`,
  ts/cts/mts → `typescript`, js/cjs/mjs → `javascript`, md → `markdown`,
  docker → `dockerfile`, make → `makefile`.
- Aliases in `shiki/langs/*.mjs` are flat arrays in `default[0]`, not nested.
- `xml → java` and `scss → css` alias entries in Shiki 3.x are **wrong** (they produce
  Java/CSS tokenisation for XML/SCSS). Don't rely on those two.
- The preflight/import must run from `frontend/` so `node_modules` resolves.

A small probe surface is kept on `window.__cptrShiki` in `highlighter.ts`
(`{ highlighters, hl }`): it counts creations in the one place a highlighter can be
created, which is what makes "must stay 1" checkable from the harness.

### Verification (final build, 45 tabs, 69 blocks in DOM)

```
Shiki highlighters built      : 1
grammars loaded lazily        : 20, including aliases (bash/sh/zsh, js/ts/cjs/…)
tokenised / plain fallback    : 30 / 39       (39 = console/transcript fences, by design)
theme vars on <code>          : 67
leaked <span> as text         : 1             (false positive: a CSS fence whose
                                               *source* contains <span class="…">)
painted token colours         : 7 distinct, 0 spans with no colour
```

The colour check reads `getComputedStyle` on token spans rather than trusting the DOM:
in the dark theme 404 sampled spans painted 7 distinct colours (e.g. `rgb(249,117,131)`
= `--shiki-dark` punctuation), which is what proves the `--shiki-*` vars resolve and
the highlight is actually visible. The one "leaked span" hit was checked back against
the source message in `app.db` and is faithful rendering of authored markup text.

## Streaming phase

Re-highlighting is reactive on the `code` prop, so a streaming block is invalidated on
every delta. Coalesced in `CodeBlock.svelte` (leading edge + trailing pass):

- `MIN_HIGHLIGHT_GAP_MS = 90` — at most one pass per 90 ms per block, so a delta storm
  (the app coalesces socket deltas at 50 ms) cannot exceed ~11 passes/s.
- The trailing pass guarantees the final text is never left un-highlighted, which is
  the failure mode a naive debounce has when the last delta lands just after a pass.
- A pass is dropped if the element was torn down or the block moved on
  (`!el.isConnected || el !== codeEl`), so unmounted/streamed-past work is not applied.

Per-block cost is the thing to watch here: ~27 ms warm for a growing block, so 11 passes/s
on *several* simultaneously streaming blocks is still ~30 % of a core each. The mount
storm dominates on load; this becomes the dominant term during active work, which is
why it is worth a cap per block rather than per page.

**Still open / not yet measured on hardware:** how many blocks actually stream at once in
a typical session, and whether the 90 ms window should scale with block size. A
`MutationObserver` on the `<code>` elements (Shiki rewrites `innerHTML` per pass) counts
passes without any source change — that is the probe to run against a real stream.

## Caveat on the numbers

Headless Chromium is slower than the user's real browser, so the absolute seconds above
are an upper bound; use the *shape* (cost ∝ number of chat tabs, all paid up front) and
cross-check magnitudes against `ui_events`. The 2-tab control run in the same browser
(60 fps) establishes that the harness is not itself the bottleneck.

## Reproducing

Probe scripts are in `notes/_scratch/`: `probe-settle.js` (load → usable time series +
reveal cost), `probe-curve.js` (peel hidden panels), `probe-fix.js` (candidate CSS),
`probe-resp.js` / `probe-steady.js` (steady-state fps).

Highlighter-specific probes: `scripts/count-highlighters.mjs` is the one to run (it
reloads the page and prints highlighters / token coverage / long tasks in one shot),
plus `notes/_scratch/hl-painted.js` (computed colours → is the highlight visible?) and
`notes/_scratch/hl-integrity.js` (visible entities/tags → did tokens eat the source?).

```sh
cd /home/brendan/computer && node scripts/count-highlighters.mjs --seconds 20
```

The harness browser has its own profile, so its session cookie goes stale on restart and
it silently lands on the Sign In page with an empty DOM — which looks exactly like a
broken UI. Refresh it before blaming the app:

```sh
cd /home/brendan/computer && .venv/bin/python .cptr/harness/mint-cookie.py
```

(That signs a token with the server's own JWT secret via `create_token`, so no password
is needed. `--user <username>` picks a non-admin; `--data-dir` targets another lane.)

```sh
cd /home/brendan/computer/.cptr/harness
node cdp.mjs --url "http://127.0.0.1:4200/?workspace=/home/brendan/computer" \
  --js /home/brendan/computer/notes/_scratch/probe-settle.js --wait 500
```

## Method note

An earlier run of this investigation was misled by a time confound: candidate CSS fixes
were tried in sequence on a page that was still mounting, so each trial looked better than
the last and `visibility:hidden` appeared to "fix itself". Peeling panels away looked like
a clean cliff at 23–33 panels, which was really just the moment mounting finished.
Only the load-time series separates mount cost from steady state. Always settle the page
(or bucket by time) before A/B-ing rendering strategies on this app.
