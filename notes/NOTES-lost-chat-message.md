# Lost chat message in f4e02c67 — diagnosis (2026-09-20)

User sent a follow-up in chat `f4e02c67-b11f-43ac-bc01-933303748fa4`
("Workspace Click Should Open Dashboard With Scheduled Tasks", workspace
`/home/brendan/computer`), navigated away, and it was gone on return. Content was
about wanting to schedule work for out-of-hours when the LLM is cheap — i.e. a
fifth angle on the A/B/C/D options table in the assistant's 16:31:00 reply.

## Verdict: the message never reached the server

Evidence (all against `~/.cptr/app.db` + `~/computer/cptr-start.log`):

| Check | Result |
|---|---|
| Last `chat_messages` row in f4e02c67 | 16:31:00 (user `c51d1c55` + reply `10612493`) — both intact |
| `chat.updated_at` = 16:32:25 | matches `[task 10612493] db save (done)` for the assistant reply; no later write |
| `POST /api/chats` for f4e02c67 after 16:31:01.042 | none. Only other POSTs: `fc6e8779` 16:32:07, new chat `474e65c5` 17:18:20 |
| non-200 / exceptions / restarts, 16:32–17:20 | none; log continuous, idle 16:39→17:14 |
| full-text scan (all chats, automations) for "out of hours"/"cheap"/"off-peak" | no match — text does not exist anywhere |
| `GET / HTTP` at 17:14:13 | a full page load happened before the user returned to the chat at 17:19 |

So: no DB write, no request that could have failed, nothing to recover from disk.
The message died in the browser.

## Why a message can vanish silently (client-side, reproducible by reading code)

1. **Composer is cleared before the server confirms.** `ChatPanel.svelte:1182`
   (`inputText = ''`) runs before the request is built/sent.
2. **Both failure paths are invisible.**
   - Queue path (sent while streaming): `catch { console.error(...) }` — no toast,
     no restore. Text already gone. `ChatPanel.svelte:1236-1250`.
   - Normal path: `catch` removes the optimistic row and `throw e`
     (`ChatPanel.svelte:1287-1291`); `onsend={send}` (`:2067`, `:2214`) is invoked
     without await/catch in `ChatInput.svelte:1199/1207` → unhandled rejection,
     no user-visible error.
3. **Silent no-op sends.** `send()` early-returns with no feedback:
   `if (!text || !selectedModel) return; if (sending) return;` (`:1145-1147`).
   Enter → `handleSubmit` → `onsend` regardless of `canSend` (`ChatInput.svelte:682`),
   so pressing Enter during an in-flight send (or before the model list loads) does
   nothing at all, visibly or otherwise.
4. **No draft persistence for an existing chat.** The only draft store is the *new
   chat* composer (`cptr:intent:chatDraft:<ws>` in sessionStorage, read at
   `ChatPanel.svelte:195-203`, written by GitBar/GitView/+page). An existing chat's
   unsent text lives only in component state, so any page load wipes it. All client
   traffic arrives via Cloudflare, so a tunnel-level failure leaves no cptr log line.

## Fix (not yet applied)

1. Persist the composer draft per chat (`localStorage cptr:draft:<chatId>`; restore
   on mount and on `loadChat`) so unsent text survives navigation *and* reload.
2. Clear the composer only after the server confirms; on failure restore the text
   and `toast.error`.
3. Give the early-returns a voice (toast, or disabled + tooltip) instead of silence.

## Related

- Nothing in cptr today is price/off-peak aware: `automations` are prompt-on-rrule
  only, with `next_run_at`; no notion of cheap windows exists in the codebase.
