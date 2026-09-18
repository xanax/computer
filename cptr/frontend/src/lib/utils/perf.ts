/**
 * Lightweight UI performance collector.
 *
 * Records durations for the interactions that make cptr feel slow — tab
 * switches, workspace/directory navigation, heavy component mounts — plus the
 * browser's own long-task entries, and ships them to the backend in small
 * batches where they land in the `ui_events` table for offline analysis.
 *
 * Every sample carries two clocks:
 *   - `ts`      wall clock (Date.now) — a real epoch-ms timeline that survives
 *               reloads and lets events be ordered across sessions.
 *   - `perf_ms` `performance.now()`, monotonic per page load, for precise
 *               intra-page gap analysis free of NTP jumps.
 *
 * Every sample also carries *ambient context* — how much UI was open at the
 * moment it was recorded (`total_tabs`, `groups`, `active_tab`) and whether
 * the document was hidden/focused. Context is captured at record time, not at
 * flush time, so it reflects the state the user was actually in.
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
/** Long tasks below this are noise; the platform reports everything >= 50ms. */
const LONG_TASK_MIN_MS = 80;
/** A long task is attributed to an interaction that started within this window. */
const ATTRIBUTION_WINDOW_MS = 1500;

export interface PerfEvent {
	/** Wall-clock epoch ms — survives reloads, orderable across sessions. */
	ts: number;
	/** Monotonic ms since page load — precise intra-page ordering. */
	perf_ms: number;
	duration_ms: number;
	kind: string;
	label?: string;
	/** Captured at record time, so navigating workspaces can't mislabel it. */
	workspace?: string | null;
	meta?: Record<string, unknown>;
}

/**
 * Ambient state, sampled whenever an event is recorded. `workspace` is lifted
 * to its own column; every other field is merged into `meta` automatically so
 * call sites don't have to think about it.
 */
export interface PerfContext {
	workspace?: string | null;
	total_tabs?: number;
	groups?: number;
	active_tab?: string | null;
	hidden?: boolean;
	focused?: boolean;
	viewport?: string;
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

/** Last interaction seen, used to attribute the long task it caused. */
let lastActivity: { kind: string; label?: string; at: number } | null = null;

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
		const ctx = contextGetter() ?? {};
		// Cheap ambient flags the call sites must never have to remember.
		if (typeof document !== 'undefined') {
			ctx.hidden = document.visibilityState === 'hidden';
			ctx.focused = typeof document.hasFocus === 'function' ? document.hasFocus() : undefined;
		}
		if (typeof window !== 'undefined') {
			ctx.viewport = `${window.innerWidth}x${window.innerHeight}`;
		}
		return ctx;
	} catch {
		return {};
	}
}

/** Everything in the context except `workspace`, which gets its own column. */
function contextMeta(ctx: PerfContext): Record<string, unknown> {
	const { workspace: _workspace, ...rest } = ctx;
	const meta: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(rest)) {
		if (value !== undefined && value !== null) meta[key] = value;
	}
	return meta;
}

/** Register a callback that supplies ambient context for every sample. */
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
	const ctx = safeContext();
	const perfMs = Math.round(nowMs() * 10) / 10;
	buffer.push({
		ts: Date.now(),
		perf_ms: perfMs,
		duration_ms: Math.round(durationMs * 10) / 10,
		kind,
		label,
		workspace: ctx.workspace ?? null,
		meta: { ...contextMeta(ctx), ...(meta ?? {}) }
	});
	// Remember what the user just did so the next long task can be blamed on it.
	// Long tasks themselves must not become the "activity" or they chain.
	if (kind !== 'long_task') {
		lastActivity = { kind, label, at: perfMs };
	}
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
	return JSON.stringify({
		session_id: SESSION_ID,
		// Fallback for any event that couldn't resolve a workspace itself.
		workspace: safeContext().workspace ?? null,
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
 *
 * A background tab has its rAFs throttled (or paused entirely), which would
 * otherwise show up as a huge "duration". We stamp whether the document was
 * hidden when measurement began so such samples can be discounted.
 */
export function measureToPaint(
	kind: string,
	label: string | undefined,
	meta?: Record<string, unknown>
): void {
	const t0 = nowMs();
	const startedHidden = typeof document !== 'undefined' && document.visibilityState === 'hidden';
	requestAnimationFrame(() => {
		requestAnimationFrame(() => {
			record(kind, label, nowMs() - t0, { started_hidden: startedHidden, ...meta });
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
			const activity = lastActivity;
			for (const entry of list.getEntries()) {
				if (entry.duration < LONG_TASK_MIN_MS) continue;
				const meta: Record<string, unknown> = { start: Math.round(entry.startTime) };
				// Blame the interaction that most likely caused the block.
				if (activity) {
					const since = entry.startTime - activity.at;
					if (since >= -50 && since <= ATTRIBUTION_WINDOW_MS) {
						meta.during = activity.label ? `${activity.kind}:${activity.label}` : activity.kind;
						meta.since_ms = Math.round(since);
					}
				}
				record('long_task', 'self', entry.duration, meta);
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

export interface MountParams {
	label: string;
	/** Whether this tab was the visible one when it mounted. */
	active?: boolean;
}

/**
 * Svelte action: measure mount → next paint for an element.
 *
 * Applied to each persisted-tab wrapper so mounting a tab (loading a workspace
 * with many tabs, opening a new one) reports how long it took until that
 * subtree was on screen — for every tab type from a single call site. Combined
 * with the ambient context this answers "does mounting cost grow with the
 * number of open tabs, and does the active tab pay more than a background one?"
 *
 *   <div use:perfMount={{ label: tab.type, active: tab.id === group.activeTabId }}> … </div>
 */
export function perfMount(_node: HTMLElement, params: MountParams) {
	measureToPaint('mount', params?.label ?? 'unknown', { active: params?.active });
	return {};
}
