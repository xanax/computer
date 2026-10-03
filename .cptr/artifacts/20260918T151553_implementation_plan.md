# Plan — background chat titles from your prompt, via an LLM you choose

## Goal
When you send the first prompt in a new chat, a model **you pick in Settings** names the chat
from that prompt, **detached from the turn** — so the sidebar/tab title lands within a second or
two, and nothing in the request path (or the teardown path) waits on it.

---

## 1. What already exists (verified in the tree)

| Piece | Where |
|---|---|
| Config key for the model | `chat.title_generation.model` — `cptr/utils/utility_models.py:6` |
| Config key for the toggle | `chat.title_generation.enabled` — `cptr/utils/chat_task.py:825` |
| The generation call | `generate_chat_title()` — `cptr/utils/chat_task.py:812` |
| Prompt | `TITLE_PROMPT` — `cptr/utils/chat_task.py:733` ("max 8 words", `{"title": ...}`, `max_tokens=50`) |
| Settings UI | `cptr/frontend/src/lib/components/Admin/Chat.svelte` — "Titles" section, `ToggleSwitch` + `ModelSelector` (`nullable`, falls back to current model). Already shipped in `cptr/frontend/build`. |
| Model resolution | `configured_utility_model("title_generation")` → `generate_json(None, model_id=…)` → `resolve_api_model_target()` (`cptr/utils/ai.py:239-260`). **Works across connections/providers**, and works with `request=None` because `resolve_api_model_target(model_id, None)` reads `Config` directly. |
| Delivery to UI | `emit_to_user(user_id, {"chat_id", "title"})` (`cptr/socket/main.py:104`) → consumed in `SidebarWorkspaceList.svelte:236`, `ChatPanel.svelte:577/626`, `stores/chat.ts:218` (notification title) |
| Guard against clobbering a rename | string equality vs the fallback title |

**So: LLM title + model picker + toggle are done. What's missing is the "async" part, and the
lack of any way to steer it.**

### The actual defect
`cptr/utils/chat_task.py:2866-2932` — generation lives in `run_chat_task`'s `finally`:

```python
finally:
    ...
    await export_chat_to_file(request, chat_id)      # 2901
    ...
    if chat_obj.title == fallback:                   # 2921
        await generate_chat_title(...)               # 2922  <-- awaited, inline
    await review_memory_after_turn(...)              # 2938
    await review_skills_after_turn(...)              # 2953
    await process_pending_chat_inputs(...)           # 2972
```

Consequences:
1. **Late.** The title only exists *after the whole first turn* — a prompt that kicks off two
   minutes of tool calls keeps the raw `content[:50]` truncation in the sidebar for two minutes.
2. **Blocking.** It's awaited before memory review, skills review and — worst — before
   `process_pending_chat_inputs`, which is what picks up *your next queued prompt*. Up to
   `timeout_seconds=20` of dead air on the next message, for a cosmetic label.
3. **Fragile trigger.** Same `finally` also means it runs on cancel/error (good), but it only
   ever fires once, at the end, and only from the first user message.
4. **No steering.** Prompt and word limit are hardcoded; no language option, no custom prompt,
   no "prompt vs prompt+reply" choice.
5. **Wrong predicate for "is this an auto title".** The guard is `chat.title == content[:50]`.
   `cptr/routers/gateway.py:771-790` creates chats with `title = first_user[:100]` — those never
   match, so Open WebUI-originated chats are permanently stuck on a truncated title. Same for any
   import path.
6. **Silent failure.** Failure is `logger.debug` only — indistinguishable from "the feature is off".

---

## 2. Proposed changes

### 2a. Extract to `cptr/utils/title.py` (new module)
Move `generate_chat_title` + `TITLE_PROMPT` out of `chat_task.py` (118 KB, imported by nearly
everything) into a small dedicated module, and have `chat_task.py` re-export for compatibility.

```python
@dataclass
class TitleSettings:            # one place, loaded once per call
    enabled: bool
    model_id: str | None        # chat.title_generation.model
    trigger: str                # on_send | after_response
    source: str                 # prompt | prompt_and_response
    prompt: str                 # custom system prompt ("" -> TITLE_PROMPT)
    max_words: int              # rendered into the prompt
    language: str               # auto | <locale>

async def load_title_settings() -> TitleSettings: ...

async def generate_chat_title(chat_id, user_id, *, prompt_text, reply_text="",
                              connection=None, model="", force=False) -> str | None:
    """Never raises. Returns the title if it wrote one."""
```

- `force=True` skips the "is this still an auto title" guard (used by the new retitle endpoint).
- Explicit `timeout_seconds=10`, `max_tokens=50`.
- Log at `info` on failure **with the resolved model id and the HTTP reason**; `debug` for the
  stack. Feeding the last error into Settings UI is a bonus, not required.

### 2b. Fire it at send time, detached (the core change)
- New scheduler alongside the existing task bookkeeping in `chat_task.py`:

```python
_title_tasks: set[asyncio.Task] = set()
_title_inflight: set[str] = set()   # chat_ids

def schedule_title_generation(**kw) -> None:
    """Fire-and-forget; keeps a strong ref so it isn't GC'd mid-flight."""
    if kw["chat_id"] in _title_inflight:
        return
    _title_inflight.add(kw["chat_id"])
    task = asyncio.create_task(_run_title(**kw))
    _title_tasks.add(task)
    task.add_done_callback(lambda t: (_title_tasks.discard(t), _title_inflight.discard(kw["chat_id"])))
```

  (Same shape as the existing detached patterns in `utils/memory.py:1630`, `utils/skills.py:776`.)

- Call site: `cptr/routers/chat.py::send_message`, in the new-chat branch immediately after
  `ChatMessage.create(... role="user" ...)` (`:1106-1113`) and before `start_task` — so it
  overlaps the first assistant turn instead of trailing it.
- `routers/chat.py` already holds the resolved `target` for the send. If that target is an API
  model, pass its connection/model as the fallback; if it's an agent profile
  (`claude_code/…`, `codex/…`), pass `None` and let `generate_text` fall back to
  `configured_utility_model` → `chat.default_model` → first API connection. **A title model must
  be an API model** — agent profiles can't serve a one-shot JSON call. Worth stating in the hint.
- Replace the inline `await generate_chat_title(...)` at `:2922` with
  `schedule_title_generation(...)` — keeps the cancel/error safety net, drops the teardown stall.
  The `_title_inflight` guard makes send-time + end-of-run idempotent.

### 2c. Fix the auto-title predicate
Stop inferring from string equality. Write `meta["title_source"]` at creation:
- `routers/chat.py:1051` → `"auto"`
- `routers/gateway.py:790`, forks (`chat.py:974`) → `"intentional"`
- `PUT /api/chats/{id}` rename (`routers/chat.py`, used by `updateChatTitle`) → `"manual"`
- Legacy rows with no flag: fall back to today's string-equality check.

Then generation runs iff `title_source != "manual"` — which also fixes the gateway truncation case.

### 2d. Settings → Chat → Titles (extend `Admin/Chat.svelte`)
All keys are plain KV in `Config` (`routers/admin.py:151` `PUT /config` is a generic upsert —
**no migration needed**).

| Key | Control | Default |
|---|---|---|
| `chat.title_generation.enabled` | toggle *(exists)* | on |
| `chat.title_generation.model` | `ModelSelector` *(exists)* — hint: "Any API model. Falls back to the chat's current model." | unset |
| `chat.title_generation.trigger` | segmented: **On send** / After first reply | `on_send` |
| `chat.title_generation.source` | segmented: **Prompt only** / Prompt + reply | `prompt` |
| `chat.title_generation.prompt` | textarea, placeholder = built-in prompt | empty |
| `chat.title_generation.max_words` | number | 8 |
| `chat.title_generation.language` | select: Match prompt / pinned locale | `auto` |

Also retitle the existing hint at `en.json:507` ("Name new chats after the first response.") —
that string becomes wrong, since the default becomes *on send*.
i18n: add new keys to `en.json` only; other locales fall back (and all of them are currently
dirty in the working tree, so I'd rather not add churn there).

### 2e. Manual retitle (small, high value)
- `POST /api/chats/{chat_id}/retitle` → `generate_chat_title(..., force=True)`, ignoring the
  `trigger`/`title_source` gates (explicit user action), returns the new title.
- Frontend: "Regenerate title" item in the sidebar chat context menu next to Rename / Copy path
  (`SidebarWorkspaceList.svelte`, where `renameChat`/`copyChatPath` already live; `:177`).

---

## 3. Verification

**Backend** (needs a restart — `_restart_server.sh` / the documented `setsid nohup …` + poll `/api/health`):
1. New chat, prompt that forces a long tool run (e.g. "search the repo and summarise X").
   Assert: `[title] Generated title for chat …` appears in `cptr-start.log` **within ~2 s of send**,
   and the memory-review line follows *without* waiting for it.
2. Enable `debug` and confirm the upstream `chat/completions` body has `max_tokens: 50` and the
   selected model id.
3. Rename the chat immediately after sending → rename must survive (title_source guard).
4. Two prompts queued fast on a new chat → exactly one title call (`_title_inflight`).
5. Toggle off → zero `[title]` lines and no upstream request.
6. Unreachable/bogus title model → `info` log with reason, chat keeps its fallback title, turn
   unaffected.
7. Gateway-created chat (Ollama/OWUI path) → now gets a real title.
8. `curl -X POST /api/chats/{id}/retitle` → new title + socket event.

**Frontend**: `npm run build` then hard reload (UI is served prebuilt from
`cptr/frontend/build`, so a source edit alone shows nothing). Check title updates live in: sidebar
row, chat header, browser tab title, landing-page list.

---

## 4. Risks / open questions
- **Process restart** drops the detached task → title stays truncated. Acceptable; end-of-run call
  plus the retitle action cover it. (Could persist a "pending title" flag later if it annoys.)
- **Race** between a slow send-time call and the end-of-run call: `_title_inflight` prevents it;
  the write is still re-checked against `title_source` before persisting.
- **Agent-model chats** (`claude_code/…`): title must come from an API model, so we fall back to
  the configured/default API model rather than the chat's own model.
- **Cost**: one extra ~50-token call per new chat, only when the toggle is on. Negligible.
- **Open question for you:** default `trigger` — `on_send` (fast, names it from your prompt alone,
  may be less accurate on a vague prompt like "fix it") vs `after_response` (today's behaviour,
  sees the assistant's reply, appears minutes later)? I'd default to `on_send` since that's what
  you asked for, with `after_response` one click away.

## 5. Rough work breakdown
1. `utils/title.py` + settings loader — 1 h
2. Detached scheduler + send-time hook + drop the inline await — 1 h
3. `title_source` meta + legacy fallback — 45 m
4. `Admin/Chat.svelte` settings + i18n — 1 h
5. Retitle endpoint + context-menu item — 45 m
6. Build, restart, run the 8 verification checks — 1 h

~half a day. Nothing here needs a DB migration or a schema change.
