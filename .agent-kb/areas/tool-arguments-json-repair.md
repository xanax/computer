# Malformed tool-call arguments killed the turn (B-026)

**Summary.** One unescaped `"` in a tool's string arguments raised
`JSONDecodeError` out of the stream adapter, which terminated the
`async for event in stream(...)` loop in `run_chat_task` — so the turn was
saved as an error and every *other* tool call batched into that same message
was thrown away. `json.loads` was unguarded on streamed arguments in all three
adapters. Now a parser repairs what it can and never raises.

**Keywords.** tool arguments, JSONDecodeError, Expecting value, unescaped
quote, truncated arguments, json-repair, _parse_tool_arguments,
_invalid_tool_arguments, run_chat_task, stream generator, batched tool calls,
plan artifact, raw_decode prefix, RecursionError, B-026

## Facts

- Three call sites, all unguarded before the fix:
  - anthropic adapter — `current_block["input_json"]`
  - openai-completions adapter — `tc["arguments_json"]`
  - openai-responses adapter — `item["arguments"]`
- The async generator has exactly one consumer, so any exception in it ends
  `run_chat_task`. A per-argument failure is therefore a per-*turn* failure.
- Ruled out on the failing chat before fixing: no NUL/control chars (the
  stored output was valid JSON), and not provider truncation (the tail was
  intact). The model had simply not escaped a quote inside a large artifact.
- `_read_relaxed_string` decides a `"` ends a string only when the next
  non-space char is `,` `}` or `]`; otherwise it is literal and the scan goes on.
  It also tolerates raw newlines and salvages a mid-string truncated tail.

## Two traps

- **`raw_decode` succeeds on a prefix.** `{"a": "x "y" z", "b": 2}` returns
  `"x "` from the first `raw_decode`, so a *successful* parse does not prove
  the value was fully consumed. Check the following token is `,` `}` `]`, else
  re-read relaxed. Without this, a 120 kB value silently became 2 chars.
- **Do not recurse in the repair.** A recursive draft `RecursionError`ed on
  2000-deep nesting — inside the stream loop. The shipped repair iterates over
  members, so depth is bounded by the payload.

## Sentinel

Unrecoverable arguments return
`{"__invalid_tool_arguments__": True, "__raw__": ...}`. `run_chat_task`
records it as a **completed** `function_call` + `function_call_output` whose
text says the arguments were not valid JSON, then continues — so the model
re-emits and the turn lives. Completion (not abandonment) is what keeps
pending-call bookkeeping consistent.

## Where the evidence lives

The failing blob was **never persisted** — the exception killed the turn
before its save, so the DB row and the chat file do not contain it. The
evidence is the traceback in `cptr-start.log`, which loguru renders with the
arguments preview inline. Two replay traps:

- Stored `output` is flat `{"type": "function_call", "arguments": {...}}` with
  `arguments` already a **dict** — not OpenAI's `role`/`tool_calls` shape. A
  replay scanning `m["tool_calls"]` finds nothing and wrongly reports "0
  broken calls". Filter on `type == "function_call"`.
- The DB is `~/.cptr/app.db` (`chat_messages.created_at`); `~/.cptr/cptr.db`
  is empty.

The `db save (error)` log line right after the traceback shows `19 items`
against `63` in the working turn — that is the batched calls being lost.

## Deliberate omissions

- **No `json-repair` dependency.** The shapes that arrive are few and all
  recoverable in ~100 tested lines; a new runtime dep in the request path is
  the worse trade. Revisit on a third shape.
- **`ai.py:414` left as a plain `json.loads`.** That one parses tool arguments
  *replayed from our own history*, written by `json.dumps`, so it is always
  valid — routing it through the repairer could put the sentinel into an
  outbound request.

## Verify

`tests/test_tool_arguments_repair.py` (31 tests). Revert the three call sites
to `json.loads` and the two adapter tests fail with the original
`JSONDecodeError` — that is what makes them a guard rather than a description.
Full suite 263 (232 pre-existing).
Full write-up: `notes/NOTES-tool-arguments-json-repair.md`.