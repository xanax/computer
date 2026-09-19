# Terminal shortcut bar can be turned off

## What the bar is

`cptr/frontend/src/lib/components/ShortcutBar.svelte` is the on-screen key row
(`Tab` `Esc` `Ctrl` `↑↓←→` `| ~ / - _` plus a dictate/mic button) that sits under
the main area and above the GitBar. It renders in `routes/+layout.svelte` only
when the active tab is a terminal, and the component itself decides *visible*
from:

- `isTouchDevice()` (`ontouchstart` or `navigator.maxTouchPoints > 0`) → shown
  whenever a terminal is active, or
- soft-keyboard detection: `innerHeight - visualViewport.height > 150` on
  viewport resize.

So on a touchscreen laptop it shows permanently, even with a physical keyboard
attached — the case this toggle exists for.

## The pref

- Store: `terminalShortcutBar` (`lib/stores.ts`, writable, default `true`).
- Pref key: `terminalShortcutBar?: boolean` in `UserPreferences`; wired through
  `persistPreferences()`, `subscribeForPersistence()` and `loadPreferences()`.
  The backend (`PUT /api/preferences`) merges free-form JSON, so no server change.
- Gate: `routes/+layout.svelte` →
  `{#if $terminalShortcutBar && (<terminal tab>)}` — the component unmounts when
  off, so its `visualViewport` listener, Ctrl-intercept listener and dictation
  all tear down.
- UI: Settings → Appearance, directly below Terminal font size, with the helper
  line `appearance.terminalShortcutBarDesc`. Strings added to `en.json` only
  (other locales fall back to English; `en.json` is already the superset).
