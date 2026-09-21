# System info: full command lines, per-process kill, server restart

The System-info dialog now shows each process's real command line and lets you kill
one, and it can restart the cptr server itself. Both endpoints already existed; the
work was the UI, one `_process_name` bug, and the translations.

## Backend (`cptr/routers/state.py`)

| thing | where |
| --- | --- |
| per-process `name` + `cmd` (trimmed to `MAX_COMMAND_CHARS = 400`) | `_collect_system_info` |
| name cleanup — kernel threads `[kthreadd]`, `<defunct>`, quoted Windows paths | `_process_name` |
| SIGTERM / SIGKILL helper | `_kill_process(pid, force)` |
| `POST /api/state/processes/kill` | `{pid, force}` |
| `POST /api/state/server/restart` | `RestartServerBody(delay_seconds)` |

`cptr/utils/restart.py` does the restart detached: sleep → SIGTERM the old server →
SIGKILL if it lingers → wait for the port to be released → start a new one → health
check. A plain in-process `execv` cannot be used: `cptr run` is its own supervisor,
so the restart has to be handed to something that outlives the process being killed.

Kill and restart are open to any **authenticated** user, not just admins: they already
have workspace terminals, and the OS uid permissions are the real boundary — a process
owned by another uid returns 403.

## Frontend

* `lib/components/SystemInfo.svelte` — command line per row (mono truncation), `✕` with
  an inline confirm bar (`Kill` / `Force` / `✕`), `Force` = SIGKILL.
* `lib/components/SystemInfoModal.svelte` — restart button + confirm, staged messaging
  (`stopping` → `waiting` → `slow`/`failed`), `Reload` fallback.
* `lib/apis/state.ts` — `killProcess`, `restartServer`.

## i18n

14 keys (`system.kill` … `system.restartFailed`) are needed by the dialog. They were
added to `en.json` with the feature and to the nine translated locales by

```
.venv/bin/python scripts/add-system-info-locales.py          # insert
.venv/bin/python scripts/add-system-info-locales.py --check  # report gaps (exit 1)
```

The script splices the block in straight after `system.load` — the same place it sits
in `en.json` — so the files keep their own tab-indented formatting and the diff is
exactly 14 added lines per locale. It is idempotent ("already complete").

Rebuild the frontend after touching locales: the bundles are imported at build time
(`lib/i18n/index.ts`), so `npm run build` (in `cptr/frontend`) is required.

## Verification

Scratch lane on :4319 with its own data dir; `POST` bodies do the work, the harness
drives the UI.

* Kill: confirm bar renders (`… ps 프로세스를 종료할까요? 종료 강제 종료 ✕`), the row is
  replaced, the count stays the same.
* Restart: `{"delay_seconds":1}` → 200 `{"status":"restarting", …}`, the pid changes
  within a minute, and the server row comes back marked as the cptr server.
* Theme audit on `bw` / `bw-dark` (`notes/_scratch/probe-mono-systeminfo.js`): every
  surface is either transparent over paper/ink or a solid inversion
  (`Kill` = ink plate with paper text in `bw`, `bg` `rgb(0,0,0)`, text
  `rgb(255,255,255)`; mirrored in `bw-dark`), `background-image: none` everywhere,
  no grey. Hover resolves through `.mono :where([class*="hover:bg-"]):hover` →
  `var(--app-fg) !important`, i.e. a solid ink/paper swap.
* Locales (`notes/_scratch/probe-locale.js`, `?lang=de|ko|ja`): dialog title, process
  header, kill confirm, and the restart confirm are all translated with no English
  showing through.
* Final mono run, both themes, `problems: NONE`
  (`notes/_scratch/shot-systeminfo.js` leaves the same state for the screenshots
  `systeminfo-bw.png` / `systeminfo-bw-dark.png`):
  `bw` — body paper/ink, panel + command line + confirm bar transparent, `Kill` and
  `Restart` ink plates with paper text; `bw-dark` — mirrored (paper text, paper plates
  with ink text). `background-image: none` and `opacity: 1` on every surface.
  The only `notes` entry is that the SERVER chip was not painted in that snapshot: the
  chip only appears on the row whose pid is the live server pid and the list is capped
  to the top CPU consumers, so it is informational, not a theme defect.

## Gotchas found while testing

* **The locale is a server-side preference.** `loadPreferences()` applies
  `prefs.locale` *after* i18next's detector, so writing `localStorage.cptr_locale` or
  `i18nextLng` does nothing on its own — set it with
  `PUT /api/state/preferences {"locale":"de"}` (values are `en-US`, `de`, …).
* `appearance.theme` wins over the top-level `theme` when both are stored; patch the
  sub-object when restoring a theme.
* A click that re-renders a button leaves that node **detached** — `closest()` then
  returns null. Read the confirm text from a live ancestor (the dialog) instead.
* `pgrep -f` / `pkill -f 'cptr-kill-me'` matches the shell running the command and
  kills it (exit `-15`). Use the bracket form `cptr-kill[-]me`.
* The CDP harness reports an in-page exception but still exits 0 — a probe that
  throws can look like a silent pass, so guard its body and return a `problems` array.
