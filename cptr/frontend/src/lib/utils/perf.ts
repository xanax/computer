/**
 * Lightweight UI performance collector.
 *
 * Records durations for the interactions that make cptr feel slow — tab
 * switches, workspace/directory navigation, heavy component mounts — plus the
 * browser's own long-task entries, and ships them to the backend in small
 * batches where they land in the `ui_events` table for offline analysis.
 *
 * Design constraints:
 *   - Never blocks the interaction it measures. Reporting happens on an idle
 *     timer (and on unload), never in the hot path.
 *   - Survives page unload: the final batch goes out with sendBeacon/keepalive.
 *   - Cheap when disabled (`localStorage['cptr.perf.disabled'] === '1'`).
 *
 * Import nothing from `$lib/stores` here — stores imports this module, and a
 * cycle would bite. The app registers a context getter via `setPerfContext`.
 */

const ENDPOINT = '/api/ui-events';
const FLUSH_INTERVAL_MS = 5000;
const MAX_BUFFER = 40;
/** Guard against a runaway buffer if the backend is down. */
const HARD_BUFFER_CAP = 400;

export interface PerfEvent {
	ts: number;
	duration_ms: number;
	kind: string;
	label?: string;
	meta?: Record<string, unknown>;
}

interface PerfContext {
	workspace?: string | null;
	userId?: string | null;
}

const SESSION_ID = (() => {
	try {
		if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
			return crypto.randomUUID();
		}
	} catch {
		/* fall through */
	}
	return `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
})();

let contextGetter: () => PerfContext = () => ({});
let buffer: PerfEvent[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;
let started = false;

function isEnabled(): boolean {
	if (typeof window === 'undefined' || typeof performance === 'undefined') return false;
	try {
		if (localStorage.getItem('cptr.perf.disabled') === '1') return false;
	} catch {
		/* localStorage may be unavailable; stay enabled */
	}
	return typeof fetch === 'function';
}

function safeContext(): PerfContext {
	try {
		return contextGetter() ?? {};
	} catch {
		return {};
	}
}

/** Register a callback that supplies the current workspace/user for tagging. */
export function setPerfContext(fn: () => PerfContext): void {
	contextGetter = fn;
}

/** Monotonic timestamp in ms, for callers that want to measure spans manually. */
export function nowMs(): number {
	return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

/** Record a finished measurement. Cheap: pushes to an in-memory buffer. */
export function record(
	kind: string,
	label: string | undefined,
	durationMs: number,
	meta?: Record<string, unknown>
): void {
	if (!isEnabled()) return;
	buffer.push({
		ts: Math.round(nowMs() * 10) / 10,
		duration_ms: Math.round(durationMs * 10) / 10,
		kind,
		label,
		meta
	});
	if (buffer.length >= MAX_BUFFER) {
		flush();
		return;
	}
	// Emergency brake: if flushing keeps failing, drop the oldest samples
	// rather than growing without bound.
	if (buffer.length > HARD_BUFFER_CAP) {
		buffer.splice(0, buffer.length - MAX_BUFFER);
	}
	scheduleFlush();
}

function scheduleFlush(): void {
	if (timer != null) return;
	timer = setTimeout(() => {
		timer = null;
		flush();
	}, FLUSH_INTERVAL_MS);
}

function payload(events: PerfEvent[]): string {
	const ctx = safeContext();
	return JSON.stringify({
		session_id: SESSION_ID,
		workspace: ctx.workspace ?? null,
		events
	});
}

/** Send whatever is buffered right now. Safe to call anywhere, any time. */
export function flush(): void {
	if (timer != null) {
		clearTimeout(timer);
		timer = null;
	}
	if (buffer.length === 0) return;
	const events = buffer;
	buffer = [];
	try {
		fetch(ENDPOINT, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: payload(events),
			credentials: 'include',
			keepalive: true
		}).catch(() => {
			/* telemetry is best-effort */
		});
	} catch {
		/* give up silently */
	}
}

/** Final flush for page unload — uses sendBeacon so it can't be cancelled. */
export function flushBeacon(): void {
	if (buffer.length === 0) return;
	const events = buffer;
	buffer = [];
	try {
		if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
			const blob = new Blob([payload(events)], { type: 'application/json' });
			if (navigator.sendBeacon(ENDPOINT, blob)) return;
		}
	} catch {
		/* fall through to fetch */
	}
	try {
		fetch(ENDPOINT, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: payload(events),
			credentials: 'include',
			keepalive: true
		}).catch(() => {});
	} catch {
		/* ignore */
	}
}

// ── Measurement helpers ─────────────────────────────────────────

/** Time a synchronous block. Returns its result; records on the way out. */
export function trace<T>(
	kind: string,
	label: string | undefined,
	fn: () => T,
	meta?: Record<string, unknown>
): T {
	const t0 = nowMs();
	try {
		return fn();
	} finally {
		record(kind, label, nowMs() - t0, meta);
	}
}

/** Time an async block. Returns its promise; records when it settles. */
export async function traceAsync<T>(
	kind: string,
	label: string | undefined,
	fn: () => Promise<T>,
	meta?: Record<string, unknown>
): Promise<T> {
	const t0 = nowMs();
	try {
		return await fn();
	} finally {
		record(kind, label, nowMs() - t0, meta);
	}
}

/**
 * Measure from now until the browser has painted the next two frames.
 *
 * Two rAFs is the standard proxy for "the change is on screen": the first
 * callback runs before the paint that includes the DOM update, the second
 * after it. Good enough to attribute perceived latency to a state change.
 */
export function measureToPaint(
	kind: string,
	label: string | undefined,
	meta?: Record<string, unknown>
): void {
	const t0 = nowMs();
	requestAnimationFrame(() => {
		requestAnimationFrame(() => {
			record(kind, label, nowMs() - t0, meta);
		});
	});
}

/** Record a span measured elsewhere: `markSince('mount', 'terminal', t0)`. */
export function markSince(
	kind: string,
	label: string | undefined,
	startMs: number,
	meta?: Record<string, unknown>
): void {
	record(kind, label, nowMs() - startMs, meta);
}

// ── Long tasks ──────────────────────────────────────────────────

function startLongTaskObserver(): void {
	if (typeof PerformanceObserver === 'undefined') return;
	try {
		const observer = new PerformanceObserver((list) => {
			for (const entry of list.getEntries()) {
				record('long_task', entry.name || 'task', entry.duration, {
					start: Math.round(entry.startTime)
				});
			}
		});
		observer.observe({ entryTypes: ['longtask'] });
	} catch {
		/* 'longtask' unsupported (Safari/Firefox) — skip quietly */
	}
}

/** Start collection. Idempotent; no-op when disabled or non-browser. */
export function initPerf(): void {
	if (started || !isEnabled()) return;
	started = true;
	startLongTaskObserver();
	window.addEventListener('pagehide', flushBeacon);
	document.addEventListener('visibilitychange', () => {
		if (document.visibilityState === 'hidden') flushBeacon();
	});
}

/** Number of buffered samples (for debugging / a settings readout). */
export function bufferedCount(): number {
	return buffer.length;
}

// ── Svelte action ───────────────────────────────────────────────

/**
 * Svelte action: measure mount → next paint for an element.
 *
 * Applied to each persisted-tab wrapper so mounting a tab (loading a workspace
 * with many tabs, opening a new one) reports how long it took until that
 * subtree was on screen — for every tab type from a single call site.
 *
 *   <div use:perfMount={tab.type}> … </div>
 */
export function perfMount(_node: HTMLElement, label: string | undefined) {
	measureToPaint('mount', label ?? 'unknown');
	return {};
}
