# On-screen keyboards the page cannot see - the Tesla browser

> **WITHDRAWN 2026-09-21 - reverted. The module squashes the UI with no
> keyboard open.**
>
> On a device with a touch pointer (`(hover: none) and (pointer: coarse)`), the
> module's "guess" arms on *focus alone*: the chat composer autofocuses on load,
> the 45% assumption is applied, and the shell lifts even though no keyboard was
> ever opened. `innerHeight` and `visualViewport.height` are both unchanged (900)
> and nothing was touched - the inset goes to 405 on its own.
>
> ```
> 4200, --touch, no interaction, composer autofocused:
>   inset 405 for the whole 7s window, innerHeight 900, vvHeight 900
> 4200, fine pointer (no --touch):
>   inset 0 throughout
> ```
>
> Reproduced with `notes/_scratch/probe-kb-squash.js` (watches the inset for 7s
> without touching the page). Any coarse-pointer device would have been unusable,
> keyboard or no keyboard - the guess is only ever correct when an overlay
> keyboard exists *and* the user asked for one by tapping a field.
>
> Reverted:
> - live worktree `/home/brendan/computer`: `keyboard.ts` deleted,
>   `+layout.svelte` restored to `HEAD` (the inline `virtualKeyboard` /
>   `visualViewport` block), rebuilt.
> - `/home/brendan/cptr-pr`: branch reset from `5c5f737` back to `3445131`
>   (the pushed commit), rebuilt. Nothing pushed was rewritten.
> - Withdrawn commits still recoverable by hash: `5c5f737` (module port to the
>   branch, amended message) and `b33750c` (its first form, which is also the
>   pre-session content of the live module). The module source is at
>   `/tmp/keyboard-module-post-session.ts`.
> - Verified after revert: 4200 / 4201 / 4202, touch pointer, no interaction -
>   `squashed: false`, inset 0.
>
> The platform analysis below is still the reason the wiring exists; the
> *inference* of an inset from focus/geometry is what has to go. See
> "What to salvage" at the end.

Branch: `fix/onscreen-keyboard-resize`, worktree `/home/brendan/cptr-pr`.
Base: `upstream/main` = `f9d1d8c`. Two commits over it:

| # | commit | what it does |
|---|--------|--------------|
| 1 | `3445131` (pushed to origin) | the wiring: `--keyboard-inset-bottom` becomes an app-wide concern, the markdown editor toolbar stops being pinned to `bottom: 0` |
| 2 | `5c5f737` (local, 1 ahead) | platform detection: work out *which kind* of keyboard platform this is instead of assuming a viewport resize |

4 files, +453/-36: new `cptr/frontend/src/lib/utils/keyboard.ts` (434 lines),
plus `routes/+layout.svelte`, `lib/components/Terminal.svelte`,
`lib/components/markdown/EditorToolbar.svelte`.

The CSS variable contract is unchanged (`--keyboard-inset-bottom`, already
consumed by the shell padding at `+layout.svelte:477`), so the rest of the app
does not care where the number comes from. The only CSS touched is the editor
toolbar, which was `position: fixed; bottom: 0` and is now
`bottom: var(--keyboard-inset-bottom, 0)`.

## Symptom

In the Tesla in-car browser, tapping the chat composer opens the car's own
keyboard. It is drawn *over the page*: the shell never resizes, so the composer
sits underneath the keyboard and you type blind. Measured: with the keyboard
covering the bottom 45% of a 1280x900 window, the composer stays at y 811-851
while the keyboard starts at y 495.

## Why the two existing mechanisms cannot fire

| mechanism | where | needs the page to be told | Tesla |
|-----------|-------|---------------------------|-------|
| `--keyboard-inset-bottom` from `visualViewport` (`resize`/`scroll`) | `+layout.svelte` (upstream) | `visualViewport.height`/`offsetTop` to change | never changes |
| `navigator.virtualKeyboard.overlaysContent` + `boundingRect` | `Terminal.svelte` (upstream) | the VirtualKeyboard API | not implemented |
| `focusin`/`focusout`/`pointer*` as a trigger | anywhere | an event to arrive | nothing is dispatched |

On a platform that reports nothing, there is no signal to react to: upstream's
inset stays `0` and the composer stays under the keyboard. (Probe evidence in
section 2 below.) The only thing that *does* change is
`document.activeElement`, which is what the branch polls.

## What the branch does

`initKeyboardInset()` (called once from `+layout.svelte`) classifies the
platform and writes `--keyboard-inset-bottom` accordingly:

| class | example | detection | inset |
|-------|---------|-----------|-------|
| 1 - resizes the layout viewport | Android Chrome with `interactive-widget=resizes-content`, desktop OSK | `taken = fullHeight - innerHeight` | `0`: the browser already moved the content |
| 2 - reports real geometry | iOS Safari (`visualViewport`), Chromium (`virtualKeyboard`) | `reported = max(boundingRect.height, fullHeight - (vv.offsetTop + vv.height))` | `reported - taken` |
| 3 - reports nothing | Tesla / car browsers, native overlay keyboards | nothing arrives | a guess: 45% of the screen, 450 ms after an editable takes focus on a coarse-pointer device |

Guards that make the three coexist:

- `fullHeight` only ever goes *down* while nothing editable is focused, so a
  keyboard-sized resize is never mistaken for the new normal.
- Once a browser is seen *reporting* geometry while editing, that fact is
  remembered on the device (`localStorage.cptr.kb-signal`) and the class-3 guess
  is off for good there. It is latched on reported geometry only, never on a
  bare resize.
- The guess is skipped entirely once the layout has lost >= 100 px
  (`takenPx() >= MIN_KEYBOARD_PX`): that is the case where the browser resized
  and padding would stack on top of it.
- The guess never lifts more than the screen minus 160 px of content.
- Opt-in: only on touch devices, or explicitly via `?kb=<pct>` / `?kb=auto` /
  `?kb=off`. A desktop with a mouse pays nothing.
- `overlaysContent` is now owned by the app shell (the module), not by
  `Terminal.svelte`, and is only switched on after this browser has been seen
  reporting real geometry.

## Evidence

All of it measured today, on this machine, against the two real servers:

- 4201 = `upstream/main` `f9d1d8c` (control)
- 4202 = this branch
- 4200 = the live server I use daily (same branch, so a smoke test)

Method: the CDP harness (`cptr/harness/cdp.mjs`, port 9333) drives a real
Chrome with touch emulation. Both lanes are pinned to the same workspace and
chat (`?workspace=/home/brendan/general&chatId=...`) so that a composer exists
and stays mounted; the keyboard is drawn into the page as a solid band over the
bottom 45%, because the real one is the thing the page is blind to. The probe
taps the real composer with a real touch event.

### 1. Composer vs keyboard, matched captures

| viewport | lane | inset before tap | inset after tap | composer top..bottom | keyboard band | composer behind keyboard? |
|----------|------|------------------|-----------------|----------------------|---------------|---------------------------|
| 1280x900 | 4201 upstream | 0 | 0 | 811..851 | 495..900 | **yes** |
| 1280x900 | 4202 branch | 0 | 405 | 406..446 | 495..900 | no |
| 1280x495 | 4201 upstream | 0 | 0 | 406..446 | 272..495 | **yes** |
| 1280x495 | 4202 branch | 0 | 223 | 183..223 | 272..495 | no |

The upstream rows are the bug: the app is never told, so nothing moves and the
composer is inside the keyboard band. The branch rows are the fix: the app
lifts by the guess and the composer sits 44-50 px above the band (the slack is
the tab bar).

### 2. The Tesla mechanism, with no event at all

`notes/_scratch/probe-kb-noevents.js` swallows `focusin`/`focusout` in the
capture phase, then focuses the composer the way the car browser would (element
becomes `activeElement`, nothing dispatched) and watches the inset:

| field | 4201 upstream | 4202 branch |
|-------|---------------|-------------|
| inset at start | 0 | 0 |
| still 0 at 300 ms (no event path) | true | true |
| inset after silent focus | **0** | **405** |
| lifted after | never | 863 ms (400 ms poll + 450 ms assumption delay) |
| inset after silent blur | 0 | 0 (259 ms) |
| probe PASS | false | true |

This is the whole bug and the whole fix in one table: an implementation that
waits for an event never lifts (upstream), one that polls `activeElement` does.

### 3. The two classes upstream already handled (no regression)

- Class 1, layout resize: `notes/_scratch/probe-kb-shrink.js` shrinks the
  viewport 900 -> 495 with an editable focused. Branch: inset 0, composer
  406..446, 50 px of slack above the keyboard. (My *first* iteration of this
  branch double-padded here - see below.)
- Class 2, visual viewport only: harness pinch to 1.8 -> the page is told the
  truth, `reported` = 400, inset 400, composer 411..451 with the band from 495.
- Live lane 4200 at 1280x900: inset 405, composer 406..446,
  `composerBehindKeyboard: false` - i.e. the case upstream got right stays right
  on my own server.

### 4. Figures

In `notes/kb-evidence/` (copies of the harness screenshots):

| file | shows |
|------|-------|
| `kb-before-900.png` | 4201 upstream, composer under the keyboard band |
| `kb-after-900.png` | 4202 branch, composer lifted clear |
| `kb-before-495.png` / `kb-after-495.png` | the same at 1280x495 |
| `kb-doublepad-before.png` | the regression I found and fixed (see below) |
| `kb-doublepad-after.png` | the same case after the arithmetic fix |
| `kb-visual-only-pinch18.png` | class 2 (iOS-style) after a 1.8 pinch |

## Found while testing (both worth a line in the PR)

1. **Double padding on platforms that resize the layout.** My first iteration
   measured what the browser shrank by against a baseline that was
   re-baselined on any rotation-sized resize (> 35%). A keyboard on a
   phone-shaped viewport takes about 45%, so the re-baseline swallowed the
   keyboard resize and then the guess was added on top of it: inset 223 on a
   495 px tall viewport, composer floating in the middle of the screen. The
   final code never lowers `fullHeight` while an editable is focused and
   refuses to guess once the layout has lost >= 100 px. This matters beyond the
   Tesla: Android Chrome is a class-1 platform, so the intermediate version
   would have regressed every Android phone.
2. **`overlaysContent` is a window-wide setting, not a view setting.** Upstream
   set it in `Terminal.svelte` on mount and cleared it on destroy, so the
   keyboard behaviour of every other view depended on whether a terminal tab
   happened to be mounted.

## How to reproduce

The harness and probes are fork-local (not part of the PR):

```sh
# lanes (see ~/lane-logs): 4201 upstream, 4202 branch
/home/brendan/cptr-lanes.sh status

# clear the device-local state so the guess is armed, then drive the probe
cd /home/brendan/computer
node .cptr/harness/cdp.mjs --touch --cookies /tmp/verify-cookies-fixes.txt \
  --url "http://127.0.0.1:4202/?workspace=/home/brendan/general&chatId=<id>" \
  --js notes/_scratch/probe-kb-state.js --wait 6000

node .cptr/harness/cdp.mjs --touch --cookies /tmp/verify-cookies-fixes.txt \
  --url "http://127.0.0.1:4202/?workspace=/home/brendan/general&chatId=<id>" \
  --js notes/_scratch/probe-kb-noevents.js --wait 6000

# before/after figure (the real composer, tapped for real)
node .cptr/harness/cdp.mjs --touch --cookies /tmp/verify-cookies-upstream.txt \
  --url "http://127.0.0.1:4201/?workspace=/home/brendan/general&chatId=<id>" \
  --js notes/_scratch/probe-kb-figure.js --wait 6000 --shot /tmp/before.png
```

Chat ids differ per lane (separate data dirs) - get one from
`GET /api/chats?workspace=/home/brendan/general`. Cookies come from
`.cptr/harness/mint-cookie.py --out <file>`; a stale cookie lands the probe on
the Sign In page with an empty DOM.

## Open questions for the maintainer

1. Should the app declare `interactive-widget=resizes-content` in the viewport
   meta, so classes 1 and 2 lean on the browser's own resize and the module
   only has to cover class 3? (The Tesla browser ignores it either way, so this
   is about the phones.)
2. The class-3 guess defaults to 45% of the screen (from the real device's
   keyboard) and is overridable per device with `?kb=<pct>`. Would you rather
   have a setting in the UI than a URL parameter?
3. Real-device coverage: class 3 is emulated here - an invisible keyboard plus
   silent focus. Class 1 and class 2 were exercised with the harness's own
   resize and pinch, not on an Android/iOS device. The one non-emulated data
   point is the original report from the Tesla itself.

## What to salvage (design constraint learned from the revert)

The diagnosis stands and is worth keeping:

- the Tesla browser reports *nothing* - no `resize`, no `visualViewport`
  change, no `focusin`, no VirtualKeyboard API - so an inset derived only from
  browser-reported geometry is always `0` there (measured numbers above and in
  the tables below, with figure files in `kb-evidence/`);
- `overlaysContent` is a window-wide switch and did not belong in
  `Terminal.svelte`, where whichever view was open flipped it (part of
  `3445131`, still on the branch and still correct);
- the editor toolbar being pinned to `bottom: 0` while the shell reserves room
  is a real inconsistency (`3445131`);
- `--keyboard-inset-bottom` is a reasonable contract for the shell.

What is not salvageable is inferring an inset from focus or from "the layout
has not lost the room yet". On a coarse-pointer device the composer
autofocuses on load, so that inference fires with no keyboard present and
lifts 45% of the shell - the failure above. Any successor needs a signal that
only a *user* can produce:

1. prefer platforms that report real geometry (`geometrychange` /
   `boundingRect` where available) and do nothing otherwise;
2. if a guess is kept for the Tesla, arm it only on a user-initiated focus
   (a `pointerdown`/`touchstart` on the field immediately before `focusin`),
   never on a programmatic autofocus, and re-check at the moment the inset is
   applied;
3. make the guess opt-in per device (`?kb=<pct>` persisted) rather than
   default-on, so the blast radius of a wrong guess is one device that asked
   for it;
4. gate any of the above behind a probe like
   `notes/_scratch/probe-kb-squash.js` in CI-able form: load a lane, touch
   nothing, assert the inset stays `0` for 7s on both pointer profiles.

Issue text for the underlying Tesla gap is still fine to post, but it should
not promise the branch: describe the measurements and ask whether upstream
wants a user-initiated-signal version.
