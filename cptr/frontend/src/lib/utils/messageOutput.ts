/**
 * On-demand message output.
 *
 * `GET /api/chats/{id}` sends only what the collapsed transcript draws: long
 * tool output is clipped, and reasoning text and tool results are reduced to
 * placeholders (see `_skeleton_output` in `cptr/routers/chat.py`). That is most
 * of a long chat's bytes — measured 6.6 MB → 0.66 MB on a chat with 846 tool
 * calls — and none of it is drawn until a row is expanded.
 *
 * Expanding a row calls `ensureMessageOutput`, which pulls the whole stream for
 * that message once and parks it here; `AssistantMessage` then renders from it
 * instead of the trimmed copy.
 */
import { get, writable } from 'svelte/store';
import { getMessageOutput } from '$lib/apis/chat';

/** Full output streams fetched on demand, keyed by message id. */
export const hydratedOutputs = writable<Map<string, any[]>>(new Map());

/** Message ids whose loaded output is a skeleton, i.e. worth fetching. */
const stripped = writable<Set<string>>(new Set());

const inflight = new Map<string, Promise<void>>();

/**
 * Record which messages of a freshly loaded chat need hydrating.
 *
 * Called on every chat load, so it doubles as the reset: messages fetched for
 * the previous chat are dropped (their ids never reappear here).
 */
export function registerLoadedMessages(messages: { id: string; output_stripped?: boolean }[]) {
	hydratedOutputs.set(new Map());
	inflight.clear();
	stripped.set(new Set(messages.filter((m) => m.output_stripped).map((m) => m.id)));
}

/** Whether a message's loaded output is missing collapsed-only detail. */
export function needsFullOutput(messageId: string): boolean {
	return get(stripped).has(messageId) && !get(hydratedOutputs).has(messageId);
}

/**
 * Fetch a message's complete output, once. Safe to call from every expand
 * handler: it is a no-op for messages that were never trimmed, and concurrent
 * calls for the same message share one request.
 */
export function ensureMessageOutput(chatId: string | null, messageId: string): Promise<void> {
	if (!chatId || !needsFullOutput(messageId)) return Promise.resolve();
	const pending = inflight.get(messageId);
	if (pending) return pending;

	const request = getMessageOutput(chatId, messageId)
		.then(({ output }) => {
			hydratedOutputs.update((map) => new Map(map).set(messageId, output));
		})
		.catch(() => {
			// Leave the skeleton in place: the row stays collapsed-looking rather
			// than blanking, and a later expand retries.
		})
		.finally(() => inflight.delete(messageId));

	inflight.set(messageId, request);
	return request;
}
