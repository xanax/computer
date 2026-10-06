---
area: provider-error-surfacing
title: Provider error bodies and upstream rate limits
aliases: [429, rate limited, provider returned error, free model pool]
updated: 2026-10-06
---

# Provider error bodies and upstream rate limits

## Map
- `cptr/utils/ai.py` — stream adapters; logs the raw body at the `resp.status_code >= 400` branch then `resp.raise_for_status()`. The retry policy used to live here and has moved to `cptr/utils/retry.py` (see `areas/provider-retry-policy.md`).
- `cptr/utils/chat_task.py:3077-3110` — the `except Exception` that turns a failed stream into the chat's error message and `meta.error`.
- `~/.cptr/config.toml` → `app_config["chat.connections"]` — connection list; api keys are `encrypted:` Fernet tokens over `server.secret`, decrypt with `cptr.utils.crypto.decrypt_key`.

## Facts
- [verified 2026-10-06 cptr/utils/chat_task.py:3089] A provider error only survives as `err_data["error"]["message"]`. Everything useful in an OpenRouter error — `code`, `metadata.raw` (the real sentence), `metadata.provider_name`, `limit_source`, `remedy_hint` — is dropped before it reaches the chat, `meta.error`, or the SSE `done` event.
- [verified 2026-10-06, superseded 2026-10-07] The 429 retry that this note first described (429-only, `0.5*(attempt+1)` s, 3 tries) has been replaced by a general policy in `cptr/utils/retry.py`: anything that is not a real 4xx is retried, 4 attempts, exponential backoff capped at 30 s. A 429 in the log with a "[retry] ... retrying in Xs (n/3)" line is normal, not a bug — and as of 2026-10-07 the fourth attempt often succeeds.
- [ran 2026-10-06 OpenRouter /api/v1/key with the stored key] The account is *not* the constraint when a free model 429s: `is_free_tier=false`, `free_model_daily_requests {used:0, limit:1000}`, `limit:null`, and a paid model (`google/gemini-2.5-flash-lite`) returned 200 in the same minute.
- [external 2026-10-06 OpenRouter endpoints API] `inclusionai/ling-3.1-flash` has exactly **one** endpoint (Novita, prompt price 0) — so `provider: {order/allow_fallbacks}` routing has nothing to fall back to and cannot fix a 429. `inclusionai/ling-3.0-flash` has two (Novita, DeepInfra) and costs money, which is why it works.
- [ran 2026-10-06] A `limit_source: upstream_provider_shared_pool` 429 is per-model *and* transient: same key, same minute, `ling-3.0-flash-sante:free` returned 429 then 200.

## Built
- Not fixed on sight here (see Decisions). The retry half of this was built on 2026-10-07 in `areas/provider-retry-policy.md`. Diagnosis path for "model isn't running" from the log alone: `grep "error body" cptr-start.log` → read `provider_name` + `limit_source` → then decide provider vs. account vs. cptr.

## Decisions
- Not fixed on sight: raising the error detail to the UI is a behaviour change to every failure message, so it needs a deliberate call, not a drive-by. Still the case after the retry work: retrying is what makes the turn survive, but the dropped detail is still what makes the *remainder* of a failure undiagnosable.

## Dead ends
- Checking the connection config, the `prefix_id`/model-id resolution, and the local `[proxied]` server on :4020. All fine; the request really did go to `https://openrouter.ai/api/v1/chat/completions` and really did get 429. Nothing in cptr was misconfigured.

## Open
- Should `chat_task.py:3089` keep `metadata.raw` / `code` / `provider_name` so a user can tell "upstream provider pool" from "your account" without the server log?
