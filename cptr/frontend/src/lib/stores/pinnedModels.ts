/**
 * Pinned models: the handful of models the user actually reaches for, kept at
 * the top of every model dropdown.
 *
 * The model list is whatever the admin configured, in connection order, and it
 * only grows — so the two or three models someone lives in drift further down
 * every time a connection is added. A pin is the user's own ordering on top of
 * the server's, and it is remembered in preferences (`pinnedModels`) so it
 * follows the account, not the browser.
 *
 * Pins are ids, not names: a model id is what actually gets sent, and a renamed
 * model keeps its pin. A pin for a model that no longer exists is harmless — the
 * dropdown only ever orders models it was given.
 *
 * The pin group is ordered by when it was pinned (newest last) so the rows the
 * user already knows stay where they put them. `PIN_CAP` is a backstop, not a
 * feature: a pin list as long as the model list has stopped being a shortcut.
 */

import { get, writable } from 'svelte/store';

export const pinnedModels = writable<string[]>([]);

/** Beyond this, the oldest pin is dropped. */
const PIN_CAP = 30;

/** Whether this model id is currently pinned. */
export function isModelPinned(id: string): boolean {
	return get(pinnedModels).includes(id);
}

/** Pin a model, or unpin it if it already is. */
export function togglePinnedModel(id: string): void {
	pinnedModels.update((current) =>
		current.includes(id)
			? current.filter((pinned) => pinned !== id)
			: [...current, id].slice(-PIN_CAP)
	);
}

/**
 * Apply the pins remembered in preferences.
 *
 * Called once at boot. Preferences are read into this store and nothing else
 * writes them back, so a later autosave of the whole blob cannot hand the user
 * a stale pin list (the B-019 family — see areas/workspace-state-ownership.md).
 */
export function applyStoredModelPins(stored: unknown): void {
	if (!Array.isArray(stored)) return;
	pinnedModels.set(stored.filter((id): id is string => typeof id === 'string'));
}
