# The theme lives in a cookie now

## Why

The theme was only ever a *server* preference: `theme` / `appearance.theme` in
`GET /api/state/preferences`. The store therefore had to start on a hard-coded default
(`writable<Theme>('dark')`) and repaint when that response landed. Two consequences:

* **A flash of the wrong palette on every load.** The bundle evaluates → store is `dark` →
  `applyAppearance` paints dark → preferences arrive → paint `bw` (or whatever). On the e-ink
  panel that is a screen of the wrong theme before the right one, and `bw`/`bw-dark` are the
  palettes that exist *because* the display is unforgiving about it.
* **One preference for every device.** The server copy is per account, so an iPad, a laptop and
  an e-ink panel all fight over one value — and the last device to save wins. Observed directly
  while testing: the server's theme moved under a browser that was merely loading, because
  another tab had persisted its own.

A cookie fixes both: it is readable **synchronously** (while the bundle is still evaluating, and
from an inline script in `app.html` *before* the bundle is even fetched), and it is per browser
profile, which is the granularity a display preference actually has.

## What changed

| file | change |
|---|---|
| `cptr/frontend/src/lib/utils/theme-cookie.ts` | **new**: `THEME_COOKIE = 'cptr_theme'`, `readThemeCookie()`, `writeThemeCookie()` |
| `cptr/frontend/src/app.html:15-70` | inline **pre-paint**: read the cookie, apply `mono`/`bw`/`bw-dark`/`dark` + `--app-bg`/`--app-fg` + `color-scheme`, correct the `theme-color` meta, and fall back to the default (`bw`) for a cookie that is missing or unknown |
| `cptr/frontend/src/lib/stores.ts:364-385` | `theme` starts as `themeAtLoad ?? DEFAULT_THEME` — the cookie, else `bw` |
| `cptr/frontend/src/lib/stores.ts:534` | every change writes the cookie (so it is never stale, whatever caused the change) |
| `cptr/frontend/static/manifest.json:11-12` | `background_color` / `theme_color` follow the default: `#ffffff` |
| `cptr/frontend/src/lib/stores.ts:598-610` | `loadPreferences()`: the cookie wins over the server; no cookie → adopt the server's and seed one; nothing either → the default stands |
| `cptr/frontend/src/lib/utils/appearance.ts:6-22` | `THEMES` + `isTheme()` guard, so "is this string a theme" has one answer, and `DEFAULT_THEME = 'bw'`, so "which palette does an unknown display get" has one too |
| `cptr/frontend/src/lib/components/Settings/Appearance.svelte:272` | the imported-theme check now uses `isTheme()` (it had its own copy of the list) |

The server copy is **still written and still read** — it is the fallback for a browser that has
never set the cookie (first run, incognito, a fresh profile), and it is what a second client
inherits. Only precedence changed.

### Precedence

```
at module load:
  themeAtLoad = readThemeCookie()  ← asked once, before anything can write the cookie

loadPreferences():
  themeAtLoad valid    → use it                                  (and it is already the store's value)
  themeAtLoad absent   → the server's if it has one, else DEFAULT_THEME (`bw`)
  themeAtLoad invalid  → ignored, i.e. treated as absent: the server's, else the default
```

`theme.set()` on a valid cookie is a no-op in the normal path; it is there so that
`loadPreferences()` is self-consistent even if the store was changed before prefs arrived. Whatever
ends up in the store is then written back to the cookie by the theme subscription — including the
default, so the *next* load of a fresh browser is painted from the cookie before the server has
answered and the two cannot disagree.

## The default is black on white

A browser that has never chosen, on an account that has never saved a theme, is the one case where a
palette has to come from nowhere. It is now `bw`: `DEFAULT_THEME` in `lib/utils/appearance.ts`,
mirrored as a literal in the pre-paint script (an inline script cannot import the module — same
duplication as the palette values, see below).

It is the only palette with an argument for being the default rather than a taste: ink on paper
reads on a projector, a phone in daylight and an e-ink panel alike. Until now the fallback was
whatever `:root` in `app.css` happened to say — `#ffffff` background with a `#525252` foreground,
i.e. "light" with washed-out text — so an unknown display got a half-way palette that is nobody's
choice. The pre-paint now paints `bw` explicitly when the cookie is missing *or* names a theme this
build does not know (classes `mono bw`, `#ffffff`/`#000000`, `color-scheme: light`) instead of
leaving the first frame to `:root`, and `theme-color` (`#ffffff`) plus the manifest's
`background_color`/`theme_color` follow it so the PWA splash and the browser chrome match the page.

Nothing already chosen changes — a cookie, or a saved preference on an account with no cookie,
still wins — so this only answers "and what if nothing has been chosen?".

### "Has this browser chosen?" is asked once

`subscribeForPersistence()` writes the cookie on every theme change, and a Svelte subscription fires
immediately on subscribe, so `readThemeCookie()` returns the app's *own* last write once that has
run. `loadPreferences()` used to re-read the cookie at call time, which is only safe while
`initState()` keeps `loadPreferences()` before `subscribeForPersistence()` — and `initState()` is
called again after login and after setup (`+layout.svelte`), by which time the cookie exists because
this app wrote it. In a fresh browser that logs in after the first load, the account's saved theme
would have been treated as "the browser has chosen" and never adopted. `themeAtLoad` is read at
module load, before any writer exists, which makes the question well-posed instead of
order-dependent.

The earlier probe run could not tell the two apart: the server copy and the default were both
`dark`, so a browser that ignored the server looked identical to one that adopted it. The runs below
use a server value *different* from the default, which is the only way that case is visible.

## Evidence

Headless Chrome (CDP harness, port 9333), fresh build, one run per case. `prepaint` is what the
first frame used (recorded by a pre-document script before the app could repaint it); `settled`
is where the app ended up.

| `cptr_theme` | OS scheme | first paint | settled | server pref |
|---|---|---|---|---|
| `bw-dark` | dark | `mono bw-dark`, bg `#000000`, fg `#ffffff`, theme-color `#000000` | `mono bw-dark`, bg `rgb(0,0,0)` | `bw-dark` |
| `bw` | dark | `mono bw`, bg `#ffffff`, fg `#000000`, theme-color `#ffffff` | `mono bw`, bg `rgb(255,255,255)` | `bw` |
| `dark` | dark | `dark`, bg `#0a0a0a`, fg `#d4d4d4` | `dark`, bg `rgb(10,10,10)` | `dark` |
| `light` | dark | nothing to apply, theme-color `#ffffff` | light | `light` |
| `system` | dark | `dark`, bg `#0a0a0a` | `dark` | `system` |
| `system` | light | nothing to apply, theme-color `#ffffff` | light | `system` |
| `purple` | dark | `mono bw`, bg `#ffffff`, fg `#000000` (the default: an unknown name is not a choice) | `dark` (server's), cookie **repaired** to `dark` | `dark` |
| *(absent)* | dark | `mono bw`, bg `#ffffff`, fg `#000000`, theme-color `#ffffff` | `dark` (server's), cookie **seeded** `dark` | `dark` |

The default itself, run against a server that has been told nothing (`PUT` with `theme: null`), so
that the default is the only thing left to settle on — five runs, fresh Chrome each time
(`.cptr/harness/verify-theme-default.sh`; the live preferences are copied first and restored after,
because changing them is the point):

| server pref | `cptr_theme` | first frame | settled |
|---|---|---|---|
| `dark` | *(absent)* | `mono bw`, `#ffffff`/`#000000` | `dark`, cookie seeded `dark` |
| *(none)* | *(absent)* | `mono bw`, `#ffffff`/`#000000` | **`mono bw`**, bg `rgb(255,255,255)`, `--app-fg` `#000000`, theme-color `#ffffff`, cookie `bw` |
| `dark` | `purple` | `mono bw` (default, *not* the server's — the pre-paint cannot know it) | `dark`, cookie repaired to `dark` |
| *(none)* | `bw-dark` | `mono bw-dark`, `#000000`/`#ffffff` | `mono bw-dark`, bg `rgb(0,0,0)` |
| `bw-dark` | *(absent)* | `mono bw` (default) | `mono bw-dark`, cookie seeded `bw-dark` |

A screenshot of row 2 (`.cptr/harness/shots/theme-default-bw.png`) is the visual check that the
default is ink on paper rather than a grey wash.

The two `mono bw` rows against a `dark` server are the interesting ones: the first frame is painted
before the server has been asked, so it shows the default and the app then adopts the saved theme —
a repaint on that one load, once, after which the cookie is seeded and the first frame is right.

Also verified in the same harness, before the pre-paint existed:

* **Settings → Appearance writes the cookie on change.** Selecting BW set `cptr_theme=bw` with body
  bg `#ffffff`; selecting Dark set `cptr_theme=dark` with `#0a0a0a`. The server copy lagged and
  then caught up (`PUT /api/state/preferences` unchanged).
* The cookie is `path=/; max-age=31536000; SameSite=Lax` (`Secure` added over https).

Harness additions (all in the gitignored `.cptr/harness/`, so nothing here is committed):
`--cookie name=value` (edits the cookie before anything loads), `--pre <file>` (runs in
`Page.addScriptToEvaluateOnNewDocument`, i.e. before the document exists), `--color-scheme`.
Probes: `pre-record-prepaint.js`, `probe-theme-prepaint.js`, `probe-theme-cookie.js`,
`probe-theme-ui-write.js`, `verify-theme-default.sh`, `shot-default-theme.sh`.

The pre-paint script sets two attributes on `<html>`, *deliberately* left in: `data-theme-cookie`
is what the cookie held (`''` when the browser has never chosen) and `data-theme-prepaint` is the
theme it painted. The app re-applies the real appearance immediately, so these are the only record
of what the first frame used, and they are how a run tells "the default was painted" apart from
"the server's theme was painted" when the repaint lands moments later. Two attributes per load.

`pkill -f 'remote-debugging-port=9333'` run from `run_command` kills the shell that typed it (the
pattern is in that shell's own command line). Put it in a script file, or the pkill never returns.

## Decisions, including the ones not taken

* **The cookie outlives the session; nothing clears it on logout.** The palette belongs to the
  display, and the server's copy is the shared, churning one — dropping the cookie at logout would
  hand the next login whatever device saved last, which is exactly the failure this removes. The
  trade-off is a browser profile shared by two accounts: the second account inherits the first
  one's theme until it picks its own. If that ever matters, `clearSession()` in `lib/session.ts` is
  the hook (the first draft had a `clearThemeCookie()`; it had no caller, so it is gone).
* **The pre-paint duplicates four palette values** (`#0a0a0a`/`#d4d4d4`/`#000000`/`#ffffff`) because
  an inline script cannot import `appearance.ts`. It deliberately does *not* try to reproduce the
  full appearance (borders, dividers, text scale, custom colours) — the app does that a moment
  later, and a first frame that is right about classes, background, foreground and colour-scheme is
  enough to stop the flash. Values are kept identical to `DARK`/`MONO_PALETTES`; a probe asserts the
  first frame and the settled frame agree on the background.
* **The `theme-color` meta is corrected too** (the script sits just after it in `app.html`). Without
  that, the first frame is right but the browser's own chrome — a status bar on a phone, the PWA
  title bar — is still the static `#000000` above a paper page.
* **`system` is resolved at first paint** from `prefers-color-scheme` (dark → the `dark` palette).
  The cookie stores `system` itself, not its resolution, so a device that changes scheme overnight
  is re-resolved on the next load rather than frozen.

## Housekeeping

* The frontend is served prebuilt: `npm run build` in `cptr/frontend`, reload — the asset hashes
  change, so no stale cache and **no server restart**.
* `npx prettier --check` clean on `app.html`, `theme-cookie.ts`, `stores.ts`, `appearance.ts`,
  `Appearance.svelte`.
* `stores.ts` carries one unrelated hunk (a `filter(...)` chain rejoined) that was already in the
  working tree; at HEAD that file is *not* prettier-clean, so the hunk is kept rather than reverted.
* Testing a browser's theme persists it: the probe browsers wrote `bw`, `bw-dark`, `light`,
  `system` … to the server preference as they went. It was restored to `dark` (the value it held
  before the probe run) via `PUT /api/state/preferences`.
