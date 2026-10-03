# Chat index → `chats.summary`

Your two answers cut this down hard. **DB + existing `search_chats` tool** kills every file,
gitignore, prompt-block and skill change. **"Two separate things, no promotion"** kills the
reviewer and the fold-back job. What is left is: *fill a field that already exists, and add a
button to refresh it.*

## Verified facts

| Fact | Value |
| --- | --- |
| `chats.summary` populated | **0 of 378** |
| Chats with a compaction checkpoint | **120** (275 checkpoint messages total) |
| Chats with extractable tool activity | **343** |
| Chats that are title-only | **35** |
| Where `chats.summary` is already read | `chat.py:202` (list), `chat.py:706` (single), `search.py:92` (search), `tools.py:3268` (`search_chats` browse) |
| Where it is already ranked | `chats.py:503` → **rank 40**, above raw message content |
| Existing writer | `Chat.update_summary()` — `chats.py:196`, used by fork at `chat.py:1159` |
| TTS row (the one you mean) | `AssistantMessage.svelte:784` — `{#if done && $ttsEnabled && $ttsConfigured}` speaker button |
| Endpoint precedent | `POST /api/chat/{chat_id}/compact` — `routers/chat.py:1359` |
| Icons available | `brain` (`Icon.svelte:108`), `archive` (`:421`) |

## Phase 1 — Backfill, zero LLM calls

One re-runnable script, `scripts/backfill-chat-index.py`, `--dry-run` by default.

Precedence per chat:

1. **Has a checkpoint** (120 chats) → use `chat_messages.chat_summary` verbatim, capped to a
   fixed length. Already written by `DEFAULT_SUMMARIZE_PROMPT` (`summarize.py:15`), which asks
   for exactly the right things: decisions, files created/modified, task state, user preferences.
2. **No checkpoint, has tool activity** (343 chats) → compose free: title, workspace, date
   range, user-turn count, files touched from `function_call.arguments.path` in
   `chat_messages.output`, and the tool names that ran.
3. **Neither** (35 chats) → title + dates + workspace.

Writes via the existing `Chat.update_summary(chat_id, entry, chat.updated_at)`.

**Pass the chat's existing `updated_at`, not `now`** — the sidebar and `/api/search/recent`
sort on `updated_at`, so writing `now` would shuffle every chat's position in the UI.

Expected coverage: every chat gets an entry; 120 get a genuine prose summary, 343 get a file
list, 35 get title-only.

## Phase 2 — The button

In the TTS row in `AssistantMessage.svelte`, styled with the **exact same classes** as the
neighbouring copy/speaker/regenerate buttons so the mono/bw theme rules keep applying with no
new mid-tones:

```
{#if done && onindex}
  <button … onclick={onindex} aria-label={$t('chat.indexChat')} use:tooltip={$t('chat.indexChat')}>
    <Icon name="brain" size={14} />
  </button>
{/if}
```

- New prop `onindex`, wired from `ChatPanel.svelte:2248` alongside `onstatus`.
- New endpoint `POST /api/chat/{chat_id}/index` in `routers/chat.py`, mirroring
  `compact_chat` (`:1359`): load active branch → build entry → `Chat.update_summary` →
  return it. Toast on completion.
- Locale key `chat.indexChat` added alongside the existing `chat.*` keys.

## The one thing to confirm on the button

You said the backfill should make **no new calls**, and you replaced *automatic* freshness with
a *manual* button. Those are compatible two ways:

- **Recommended:** the press makes **exactly one** utility-model call
  (`configured_utility_model("summary_generation")`) to write a proper prose summary, falling
  back to the free heuristic entry if no model is configured or the call errors. Nothing spends
  unless you press — this is the escape hatch from "no automatic calls".
- **Alternative:** the press is free-only, rebuilding the entry from local data. Then it only
  refreshes file lists, and cannot improve on Phase 1 for the 35 title-only chats.

Tell me which and I will build that one.

## Honest caveats

- **A checkpoint summary describes the *older* half of a chat.** `chat_summary` sits on the
  message where compaction happened, and covers the messages *dropped* before it — not the
  transcript after it. Promoting it verbatim is a good index line but it skews early. Cheap fix:
  fall through to the free heuristic when a chat has a checkpoint but also has substantial
  activity *after* it.
- **Forks inherit the index.** `chat.py:1159` copies `chat.summary` on fork, so a fork starts
  with its parent's index entry until something refreshes it.
- **Search results will reorder.** Summaries rank at 40, above message content, so existing
  queries can change order and `_extract_snippet` can now return summary text as the snippet.
  This is the only behaviour change to an existing feature.
- **No portability.** The DB is not gitignored, but it is not versioned either — outside every
  repo. `scripts/cptr-backup.sh` captures `app.db` WAL-safe nightly and asserts it is in the
  archive, so it survives, but it does not travel with a clone. DB-only cannot meet a hard
  "must live with the repo" requirement.

## Verify (not yet run)

1. `scripts/backfill-chat-index.py --dry-run` → eyeball the 20 longest chats' proposed entries.
2. Run it for real; count `chats.summary` non-null = 378.
3. **Server restart needed** (`routers/chat.py` changed) and `npm run build` for the frontend.
   I will not restart — I will tell you when.
4. Probe: `search_chats` for a phrase known only to be in a summary, confirm the hit returns
   `match_type: "summary"`.
5. Press the button on a title-only chat; confirm `chats.summary` changes and the toast lands.

## Not in scope (per your answers)

No `.agent-kb` changes, no `.gitignore` work, no prompt block, no per-workspace skill, no
10-turn reviewer, no fold-back job, no nightly sweep, no new files in any repo.
