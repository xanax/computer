/**
 * UI performance metrics API: aggregated reads and maintenance endpoints.
 *
 * Writing samples is handled by `$lib/utils/perf` (which batches and posts to
 * `/api/ui-events` directly); these helpers are for reading the results back.
 */
import { fetchJSON } from '$lib/apis';

export interface UiEventSummaryRow {
	kind: string;
	label: string | null;
	count: number;
	p50: number;
	p95: number;
	max: number;
	mean: number;
}

export interface UiEventSummary {
	since_ms: number;
	groups: UiEventSummaryRow[];
}

export interface UiEventRow {
	id: string;
	created_at: number;
	kind: string;
	label: string | null;
	duration_ms: number;
	workspace: string | null;
	session_id: string | null;
	meta: Record<string, unknown> | null;
}

/** Aggregated durations per event type, slowest p95 first. */
export const getUiEventSummary = (sinceMs?: number, kind?: string) => {
	const params = new URLSearchParams();
	if (sinceMs && sinceMs > 0) params.set('since_ms', String(sinceMs));
	if (kind) params.set('kind', kind);
	const qs = params.toString();
	return fetchJSON<UiEventSummary>(`/api/ui-events/summary${qs ? `?${qs}` : ''}`);
};

export interface WorkspaceDwellRow {
	workspace: string;
	seconds: number;
	/** Share of total tracked time in the window, 0–100. */
	share_pct: number;
}

export interface WorkspaceDwell {
	since_ms: number;
	window_hours: number;
	total_seconds: number;
	workspaces: WorkspaceDwellRow[];
}

/**
 * Active time per workspace as a share of total tracked time.
 *
 * Measured from client-reported `dwell` spans, so it only counts time the tab
 * was actually visible. Empty until samples exist.
 */
export const getWorkspaceDwell = (windowHours = 7 * 24) =>
	fetchJSON<WorkspaceDwell>(`/api/ui-events/dwell?window_hours=${windowHours}`);

/** Raw samples, newest first. */
export const getRecentUiEvents = (limit = 100, kind?: string) => {
	const params = new URLSearchParams({ limit: String(limit) });
	if (kind) params.set('kind', kind);
	return fetchJSON<{ events: UiEventRow[] }>(`/api/ui-events/recent?${params.toString()}`);
};

/** Delete every stored sample. */
export const clearUiEvents = () =>
	fetchJSON<{ status: string; deleted: number }>('/api/ui-events', { method: 'DELETE' });
