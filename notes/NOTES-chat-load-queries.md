# One chat load read the whole message table four times

## Symptom

Opening the 59 MB chat (`4c61866d…`, the one the search audit measured) took
~1.2 s, and the server spent a fifth of that re-reading rows it had already read:
every message's `output` column — reasoning text and tool output, ~95% of the
table's bytes — was selected and JSON-decoded **four times** to answer one
`GET /api/chats/{id}`.

Measured with `notes/_scratch/chat-load-query-count.py` against `95bfbf4`:

```
GET /api/chats/4c61866d  ->  6.60 MB, 1197 ms
  get_all_by_chat        x4      418 ms  (118, 101, 92, 108)
```

## Root cause

Four call sites reached for `ChatMessage.get_all_by_chat()`, which loads full ORM
rows, when three of them needed only a few columns:

| caller | what it actually needed |
| --- | --- |
| `get_chat` | the rows, for the response body |
| `_get_context_leaf_message_id` | the id of the newest message |
| `_infer_chat_model` | the newest non-null `model` |
| `_get_latest_usage_checkpoint` | `id`/`parent_id`/`role`/`usage`/`chat_summary`, to walk the branch |

The last three are pure bookkeeping — no `output` in sight — but each one paid
for the whole table, and the usage-checkpoint walk (the largest of the three)
ran on *every* load.

`_get_chat_context_usage` made it worse by ordering that walk last: it rebuilt
the history (a fifth full read via `_load_message_history`) and fetched the
system prompt before checking whether the branch already carried a real provider
usage report, in which case neither is used.

## Fix

New header-only queries in `cptr/models/chats.py` — `MessageHeader` (a
`slots` dataclass: `id`, `parent_id`, `role`, `content`, `chat_summary`,
`usage`), `get_headers_by_chat`, `get_last_message_id`, `get_last_model` — and
the three callers switched to them:

* `_get_context_leaf_message_id` → `SELECT id … ORDER BY created_at DESC LIMIT 1`
* `_infer_chat_model` → `SELECT model … WHERE model IS NOT NULL … LIMIT 1`
* `_get_latest_usage_checkpoint` → walks `MessageHeader`s; it returns the trailing
  messages only so their `role`/`content` can be token-estimated, which is all
  the estimate reads.

`_get_chat_context_usage` now tries the usage checkpoint **first** and returns
early; `_load_message_history` and `_load_system_prompt` are reached only when
there is no provider usage to report.

## Verification

`notes/_scratch/profile-get-chat-detail.py` (per-step timings through the real
ASGI app) and `chat-load-query-count.py` (counts the queries a load issues).
Same chat, same payload (this commit does not shrink the response):

```
GET /api/chats/4c61866d  ->  6.60 MB, 1005 ms
  get_all_by_chat        x2      204 ms  (111, 93)
  get_headers_by_chat    x1       10 ms
  get_last_model         x1       10 ms
```

Four full reads became two: the response body (which needs the rows anyway) and
the token estimate (`_load_message_history`, `cptr/utils/chat_task.py:995`,
which is handed the text it estimates). The two new queries together cost 20 ms
against 214 ms saved. `get_last_message_id` is not called at all in this chat —
`chat.current_message_id` is set, so the leaf lookup short-circuits.
