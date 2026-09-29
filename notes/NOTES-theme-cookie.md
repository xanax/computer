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
| `cptr/frontend/src/app.html:15-65` | inline **pre-paint**: read the cookie, apply `mono`/`bw`/`bw-dark`/`dark` + `--app-bg`/`--app-fg` + `color-scheme`, and correct the `theme-color` meta |
| `cptr/frontend/src/lib/stores.ts:364` | `theme` starts as `readThemeCookie() ?? 'dark'` instead of `'dark'` |
| `cptr/frontend/src/lib/stores.ts:534` | every change writes the cookie (so it is never stale, whatever caused the change) |
| `cptr/frontend/src/lib/stores.ts:598-605` | `loadPreferences()`: the cookie wins over the server; no cookie → adopt the server's and seed one |
| `cptr/frontend/src/lib/utils/appearance.ts:6-11` | `THEMES` + `isTheme()` guard, so "is this string a theme" has one answer |
| `cptr/frontend/src/lib/components/Settings/Appearance.svelte:272` | the imported-theme check now uses `isTheme()` (it had its own copy of the list) |

The server copy is **still written and still read** — it is the fallback for a browser that has
never set the cookie (first run, incognito, a fresh profile), and it is what a second client
inherits. Only precedence changed.

### Precedence

```
loadPreferences():
  cookie present and valid  → use it                      (and it is already the store's value)
  cookie absent             → use the server's, write it  (first run seeds the browser)
  cookie invalid            → ignore it, use the server's, rewrite it  (repairs a hand-edited one)
```

`theme.set()` on a valid cookie is a no-op in the normal path; it is there so that
`loadPreferences()` is self-consistent even if the store was changed before prefs arrived.

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
| `purple` | dark | **nothing applied** (`bg`/`fg`/class unset), theme-color left `#000000` | `dark` (server's), cookie **repaired** to `dark` | `dark` |
| *(absent)* | dark | nothing to apply | `dark` (server's), cookie **seeded** `dark` | `dark` |

Also verified in the same harness, before the pre-paint existed:

* **Settings → Appearance writes the cookie on change.** Selecting BW set `cptr_theme=bw` with body
  bg `#ffffff`; selecting Dark set `cptr_theme=dark` with `#0a0a0a`. The server copy lagged and
  then caught up (`PUT /api/state/preferences` unchanged).
* The cookie is `path=/; max-age=31536000; SameSite=Lax` (`Secure` added over https).

Harness additions (all in the gitignored `.cptr/harness/`, so nothing here is committed):
`--cookie name=value` (edits the cookie before anything loads), `--pre <file>` (runs in
`Page.addScriptToEvaluateOnNewDocument`, i.e. before the document exists), `--color-scheme`.
Probes: `pre-record-prepaint.js`, `probe-theme-prepaint.js`, `probe-theme-cookie.js`,
`probe-theme-ui-write.js`.

`root.dataset.themePrepaint` is set by the pre-paint script and is *deliberately* left in: the app
re-applies the real appearance immediately, so it is the only record of what the first frame used —
and for an ignored value (`purple`) it is the only way to see that the script ran at all and
declined. It is one attribute on `<html>` per load.

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
