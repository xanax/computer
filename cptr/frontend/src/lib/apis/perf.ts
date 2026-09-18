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

/** Raw samples, newest first. */
export const getRecentUiEvents = (limit = 100, kind?: string) => {
	const params = new URLSearchParams({ limit: String(limit) });
	if (kind) params.set('kind', kind);
	return fetchJSON<{ events: UiEventRow[] }>(`/api/ui-events/recent?${params.toString()}`);
};

/** Delete every stored sample. */
export const clearUiEvents = () =>
	fetchJSON<{ status: string; deleted: number }>('/api/ui-events', { method: 'DELETE' });
