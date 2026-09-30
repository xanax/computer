# Plan-mode artifacts killed the turn: `create_artifact() got an unexpected keyword argument 'workspace'`

Ledger: **B-016**. Seen live in chat `324915e5-1e78-479e-a0b9-ef1f95a39309`
(`/home/brendan/brogue-js-eink`, "E-Ink Map UI"), where the user asked for a plan
and every turn — including a plain "continue" — came back as:

> **Error:** create_artifact() got an unexpected keyword argument 'workspace'

## What the user sees

A complete-looking reply with an error footer and **no artifact card**. The plan
text is real (the model wrote it), so it reads as "the plan tool is broken", not
as "the turn crashed". The stored message carries
`meta.error = "create_artifact() got an unexpected keyword argument 'workspace'"`,
which is how the chat is identifiable from the DB.

## Why

`create_artifact` is **plan-mode-only**: `run_chat_task` appends it to the tool
list inline (`_fn_to_schema("create_artifact", create_artifact)`) rather than
registering it in `ALL_TOOLS`. So it cannot go through `execute_tool()` and its
two call sites invoke it directly:

- `cptr/utils/chat_task.py` — the queued/resume path (`run_queued_tool_calls`)
- `cptr/utils/chat_task.py` — the auto-approved path in the main loop

Both called it as `create_artifact(**args, workspace=workspace)`.

That was correct when it was written, and stopped being correct in two upstream
commits:

| Commit | Date | What it did |
| --- | --- | --- |
| `10507c0` (Tim, "refac") | 2026-07-31 | *Introduced* both call sites, with `workspace=workspace` — correct then, because `create_artifact`'s signature was `*, workspace: str` |
| `22b29c6` (Tim, "refac") | 2026-08-05 | Moved the signature to `*, __context__: dict` and updated every caller that goes through `execute_tool` — but not those two direct calls |

So the wrong keyword has been sitting there since 2026-08-05.

Two things turn the mismatch into a dead turn rather than a tool error:

1. `execute_tool` wraps tool execution in `try/except` and returns
   `Error executing <name>: <e>` — the direct calls bypass that entirely.
2. The raise therefore escapes the tool call into the agentic loop's top-level
   handler, which appends `> **Error:** {msg}` to the assistant content, scrubs
   incomplete items and ends the message. The model never gets a tool result and
   never gets a chance to retry.

Note the same trap is reachable from any plan-mode chat, and the failure is
deterministic: any turn that calls the tool dies, and the model will call it
again next turn (it is the point of the mode), so the chat never recovers.

## Fix

A single helper, used by both call sites, in `cptr/utils/chat_task.py`:

```python
async def _execute_create_artifact(arguments: dict, __context__: dict) -> str:
    args = dict(arguments)
    args.pop("workspace", None)      # model-supplied; the real one is in the context
    try:
        return await create_artifact(**args, __context__=__context__)
    except Exception as e:
        return f"Error executing create_artifact: {e}"
```

The context passed is `{**tool_ctx, "call_id": …}` — the same dict every other
tool gets in that scope, so `workspace`, `request` and `chat_id` all resolve.

Deliberate parts of the shape:

- **the guard matters as much as the keyword** — the class of bug is "a tool
  error killed the turn"; with the `try/except` a future signature drift
  degrades to an error string the model can read and retry;
- **one helper, not two fixed call sites** — the duplication is what let the
  original refactor miss the second one (and it is easy to add a third path).

## Verification

`tests/test_plan_mode_artifact.py` (5 tests):

- the call shape binds against the **real** `create_artifact` signature
  (`inspect.signature(...).bind`), so the next signature change fails loudly;
- an **AST** check that no `create_artifact` call in `chat_task.py` passes
  `workspace=` — run against the pre-fix source it reports `[(2154, 'workspace'),
  (2714, 'workspace')]`, i.e. exactly the two real call sites;
- the helper forwards `__context__`, drops a model-invented `workspace`, and
  turns a raising tool into an `Error executing …` string;
- end-to-end against the real tool with no live request:
  `Error: request context unavailable` — proof the `TypeError` is gone, since
  this is the body that used to be unreachable.

## Upstream status

`PRESENT-UPSTREAM` @ `f9d1d8c` (checked 2026-08-21). Upstream's
`create_artifact` also takes `__context__` and upstream's `chat_task.py` still
says `create_artifact(**args, workspace=workspace)` — so a stock checkout has
the same dead turn, and it is reproducible on the `cptr-upstream` lane (:4201)
by asking for a plan. Worth filing; the local patch is temporary if it lands.

## Operational note

The fix is Python, so the running server keeps the bug until it restarts
(`cptr-lanes.sh restart cptr`, or the Restart control in System info). Chats
already hit by it are fine — the failure is per-turn, not per-chat; just send
another message after the restart.
