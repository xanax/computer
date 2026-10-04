/**
 * Workspace priority: which of the sidebar's workspaces are shown.
 *
 * The server ranks every workspace (`GET /api/state/workspaces/priority`) but the
 * *choice* belongs to the user: the rank seeds the initial list, and from then
 * on the pinned set is remembered in preferences so nothing jumps out from under
 * a pointer mid-drag. That split is the whole point — an activity score that
 * reorders itself while you are using it is worse than no score at all.
 *
 * Three ways a hidden workspace comes back:
 *   - the user expands "Show more" (or switches into one from elsewhere)
 *   - it has a standing reason to be seen: unread chats, outstanding tasks
 *   - the user works in it — opens it, chats there, or opens a terminal
 *
 * The last one is `resurface()`, and it deliberately does *not* re-rank: it
 * pins, leaving the collapse set alone. Actively using a project should not
 * silently bury the other seven that were already visible.
 *
 * Design: notes/NOTES-activity-aware-sidebar.md
 */

import { get, writable } from 'svelte/store';
import { getWorkspacePriority } from '$lib/apis/state';

export interface WorkspacePriority {
	path: string;
	score: number;
	dwell_seconds: number;
	active_days: number;
	chats: number;
	live_jobs: number;
	unread: number;
	last_seen_ms: number;
	reasons: string[];
}

export interface WorkspacePriorityResponse {
	workspaces: WorkspacePriority[];
	pinned: string[];
	limit: number;
	window_days?: number;
}

/** How many workspaces the server suggests showing. */
export const DEFAULT_LIMIT = 8;

/**
 * Past this, a hidden workspace is pushed out of `pinned` when the user clears
 * "Show more". A list that only ever grows is a list that has stopped ranking.
 */
const PIN_CAP = 40;

export const workspacePriority = writable<Map<string, WorkspacePriority>>(new Map());

/** Paths the user has chosen to keep visible. Never trimmed by activity. */
export const pinnedWorkspaces = writable<string[]>([]);

/** True once the user has expanded the collapsed tail. */
export const workspacesExpandedList = writable(false);

/**
 * True when this workspace has a standing reason to be seen regardless of
 * score: the user is in it, it holds something waiting, or it has unread chat.
 */
export function isHeld(row: WorkspacePriority | undefined): boolean {
	return !!row && row.reasons.length > 0;
}

/**
 * Workspaces to render, in the user's own order.
 *
 * A workspace is shown when it is pinned, held, already expanded, or the tail
 * is expanded. `workspaceOrder` decides the sequence among them, so a drag still
 * means what it says; anything the order does not mention keeps the rank order.
 */
export function visibleWorkspaces(
	all: { path: string }[],
	rank: Map<string, WorkspacePriority>,
	pinned: string[],
	tailExpanded: boolean,
	limit: number
): string[] {
	const rankIndex = new Map(rank.size ? [...rank].map(([path], i) => [path, i]) : []);
	const shown = (path: string) => {
		if (pinned.includes(path) || tailExpanded) return true;
		const row = rank.get(path);
		if (isHeld(row)) return true;
		// Before the ranking arrives, show the server's suggestion rather than
		// flashing an empty sidebar and then filling it in.
		return rankIndex.size === 0 ? true : (rankIndex.get(path) ?? Infinity) < limit;
	};
	return all.filter((ws) => shown(ws.path)).map((ws) => ws.path);
}

/** The workspaces that are being hidden, for the "Show more" label. */
export function hiddenCount(all: { path: string }[], visible: string[]): number {
	return all.length - visible.length;
}

/**
 * Note that the user worked in this workspace, bringing it back into view.
 *
 * Called for the things that count as real use: switching to it, chatting there,
 * opening a terminal, creating a task. Idempotent and cheap — a plain array
 * membership test — so a hot path can call it without thinking.
 */
export function resurface(path: string | null | undefined): void {
	if (!path) return;
	const current = get(pinnedWorkspaces);
	if (current.includes(path)) return;
	// Most recent first: the tail of this list is what gets trimmed.
	const next = [path, ...current].slice(0, PIN_CAP);
	pinnedWorkspaces.set(next);
}

/** Let the user collapse the tail again, dropping what it only remembered. */
export function collapseList(): void {
	workspacesExpandedList.set(false);
	pinnedWorkspaces.set([]);
}

/**
 * Fetch the ranking once. Deliberately not polled: the signals it reads (dwell,
 * active days, recency) move over days, not seconds, and a sidebar refetching
 * on a timer is the mount storm NOTES-ui-multi-chat-slowdown.md is about.
 */
export async function loadWorkspacePriority(current?: string | null): Promise<void> {
	try {
		const data = await getWorkspacePriority(current);
		const rank = new Map(data.workspaces.map((row) => [row.path, row]));
		workspacePriority.set(rank);
		// Seed the visible set from the server's suggestion on first load only;
		// afterwards `pinnedWorkspaces` is the user's, and reset() is its undo.
		if (get(pinnedWorkspaces).length === 0) {
			const suggested = data.workspaces.slice(0, data.limit || DEFAULT_LIMIT);
			pinnedWorkspaces.set(suggested.map((row) => row.path));
			workspacesExpandedList.set(data.workspaces.length > (data.limit || DEFAULT_LIMIT));
		}
	} catch {
		/* No ranking is not an error: the sidebar falls back to showing all. */
	}
}

/**
 * Apply the user's remembered set, or seed it from preferences at boot.
 *
 * Preferences are read once into `pinnedWorkspaces`; after that this store is
 * the only writer, so a later `/api/state/preferences` save cannot overwrite the
 * user's choice with a stale echo (the B-019 family — see
 * areas/workspace-state-ownership.md).
 */
export function applyStoredPins(stored: unknown): void {
	if (!Array.isArray(stored)) return;
	pinnedWorkspaces.set(stored.filter((p): p is string => typeof p === 'string'));
}
