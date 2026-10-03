---
name: cptr-frontend
description: Build/verify steps and side-effect patterns for the cptr Svelte frontend
  (under /home/brendan/computer/cptr/frontend). Use when editing components in this
  codebase, especially timers, polling, socket listeners, or other per-tab background
  work.
created_by: computer
created_from: background_review
created_at: '2026-09-19T06:38:02.831036+00:00'
updated_at: '2026-09-19T06:38:02.831036+00:00'
---
# cptr frontend

## Verify every frontend change

Build after editing anything under `cptr/frontend/src`:

```
cd /home/brendan/computer/cptr/frontend && npm run build 2>&1 | tail -25
```

- Exit code 0 means the change compiles.
- Build takes ~50s; `tail` keeps output readable.
- Warnings like `INEFFECTIVE_DYNAMIC_IMPORT` and the rolldown plugin-timing notes are noise, not failures.
- Frontend-only changes ship as prebuilt static files on next page load — **no Python/server restart needed**. Only say a restart is needed if server code changed.

## Per-tab polling in the persisted-tab layout

Tabs (chat and others) stay mounted even when hidden. An unconditional `setInterval` started in `onMount` therefore runs once per mounted tab forever — 15 open tabs produced ~180 req/min of redundant `/api/...` traffic. Never start a poller unconditionally; gate it on the active tab **and** document visibility.

```svelte
let docVisible = $state(
  typeof document !== 'undefined' ? document.visibilityState === 'visible' : true
);

function handleVisibilityChange() {
  docVisible = document.visibilityState === 'visible';
}

onMount(() => {
  document.addEventListener('visibilitychange', handleVisibilityChange);
});

onDestroy(() => {
  document.removeEventListener('visibilitychange', handleVisibilityChange);
});

$effect(() => {
  if (active && docVisible && someId) startPolling();
  else stopPolling();
});
```

Rules:

- Keep the interval handle typed (`ReturnType<typeof setInterval> | null`) and clear it in both the `stopPolling` path and `onDestroy`.
- Drop the one-shot refresh from `onMount` — the `$effect` covers startup when the tab is active, and the initial `null` interval is cleaned up either way.
- A hidden or backgrounded tab must issue **zero** timer requests; switching back re-fires the effect and resumes the cadence immediately.
- Unsubscribe socket-store listeners on destroy (store the unbind function from setup, e.g. `unbindSocketListeners`).
- Event-driven refresh (fs watcher with debounce, socket events) is legitimately different from timer polling — do not "fix" it into a poller, and do not gate it the same way.

After the change, run the build command above to confirm it compiles.
