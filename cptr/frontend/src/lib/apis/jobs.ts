/**
 * Job board API (`/api/jobs`).
 *
 * A job is the general form of a todo: `executor='human'` is a plain todo,
 * anything else is something a model does on its own, now or at `trigger_at`.
 * `/api/todos` shows only the human slice, so a deferred todo looks unchanged
 * there until its run lands — this module is how the dashboard sees that.
 */
import { fetchJSON, jsonBody } from './index';

export type JobStatus =
	| 'open'
	| 'queued'
	| 'running'
	| 'blocked'
	| 'needs_review'
	| 'done'
	| 'failed'
	| 'cancelled'
	| 'paused';

export type JobKind = 'task' | 'note' | 'run';
export type JobTrigger = 'manual' | 'at' | 'rrule' | 'window';

/** One firing of a recurring task, as the list carries it. */
export type RunSummary = {
	id: string;
	status: JobStatus;
	created_at: number;
	last_error: string | null;
	chat_id: string | null;
};

export type JobData = {
	id: string;
	workspace: string;
	title: string;
	kind: JobKind;
	/** 'human' or a model id. */
	executor: string;
	trigger: JobTrigger;
	/** Epoch ns the job is due, for `trigger='at'` (the rest of the board is ms). */
	trigger_at: number | null;
	rrule: string | null;
	status: JobStatus;
	priority: number;
	attempts: number;
	last_error: string | null;
	parent_chat: string | null;
	/** The recurring task this row is one firing of (kind === 'run'). */
	parent_job: string | null;
	source: 'human' | 'chat';
	origin_chat: string | null;
	payload: string | null;
	meta: Record<string, unknown> & { run_chat_id?: string; run_message_id?: string };
	created_at: number;
	updated_at: number;
	/** Newest firing of a recurring task, so a row can say how it went. */
	last_run: RunSummary | null;
};

export type JobsResponse = {
	jobs: JobData[];
	counts: Record<string, number>;
	/** Per-workspace totals, for the all-workspaces view. */
	workspaces: Record<string, number>;
	open_statuses: JobStatus[];
};

/** Every task for a workspace on the board (its todos, deferrals, schedules). */
export async function getJobs(workspace: string): Promise<JobsResponse> {
	return fetchJSON(`/api/jobs?workspace=${encodeURIComponent(workspace)}`);
}

/**
 * Every task, every workspace — the Tasks tab. `kind` picks which slice of the
 * one table to read: the tasks themselves, or their run history.
 */
export async function getAllJobs(
	params: { workspace?: string; kind?: string; status?: string } = {}
): Promise<JobsResponse> {
	const search = new URLSearchParams();
	if (params.workspace) search.set('workspace', params.workspace);
	if (params.kind) search.set('kind', params.kind);
	if (params.status) search.set('status', params.status);
	const qs = search.toString();
	return fetchJSON(`/api/jobs${qs ? `?${qs}` : ''}`);
}

export type DeferJobPayload = {
	/** Relative ("30m", "in 2 hours") or RFC 3339 with a timezone. */
	at: string;
	/** Model id that runs it. */
	executor: string;
	/** Run in this conversation instead of a fresh chat. */
	parent_chat?: string;
	/** Extra briefing for the run. */
	payload?: string;
};

export async function deferJob(id: string, body: DeferJobPayload): Promise<JobData> {
	return fetchJSON(`/api/jobs/${id}/defer`, jsonBody(body));
}

/**
 * Edit a job in place. The board only needs `status`, for the rows that live on
 * the board without a todo of their own to toggle.
 */
export async function patchJob(
	id: string,
	body: { status?: JobStatus; title?: string; executor?: string; trigger?: string }
): Promise<JobData> {
	return fetchJSON(`/api/jobs/${id}`, {
		method: 'PATCH',
		body: JSON.stringify(body)
	});
}

/** Clear a pending trigger and kill a run in flight. */
export async function cancelJob(id: string): Promise<JobData> {
	return fetchJSON(`/api/jobs/${id}/cancel`, { method: 'POST' });
}

/** Remove a board entry that has no `/api/todos` row of its own. */
export async function deleteJob(id: string): Promise<{ ok: boolean }> {
	return fetchJSON(`/api/jobs/${id}`, { method: 'DELETE' });
}

// ── Recurring tasks ──────────────────────────────────────────

/**
 * Do this task once, now, without touching its schedule. Returns the `run` row
 * the scheduler will claim — the task itself keeps every future occurrence.
 */
export async function runJobNow(id: string): Promise<JobData> {
	return fetchJSON(`/api/jobs/${id}/run`, { method: 'POST' });
}

/** Pause or resume a recurring task. Paused keeps its schedule and its history. */
export async function toggleJob(id: string): Promise<JobData> {
	return fetchJSON(`/api/jobs/${id}/toggle`, { method: 'POST' });
}

/** Every firing of a recurring task, newest first. */
export async function getJobRuns(
	id: string,
	limit: number = 20
): Promise<{ runs: JobData[]; task: JobData }> {
	return fetchJSON(`/api/jobs/${id}/runs?limit=${limit}`);
}

/**
 * Settle a row by hand. `done` accepts what a run produced; `open` puts it back
 * on the board. This is the only route to `done` — a run lands `needs_review`.
 */
export async function reviewJob(
	id: string,
	action: 'done' | 'open' = 'done',
	note?: string
): Promise<JobData> {
	return fetchJSON(`/api/jobs/${id}/review`, jsonBody({ action, note }));
}

/** Push a task's next due time out ("10m", "in 3 hours", RFC 3339). */
export async function snoozeJob(id: string, at: string): Promise<JobData> {
	return fetchJSON(`/api/jobs/${id}/snooze`, jsonBody({ at }));
}

export type TaskForm = {
	workspace: string;
	title: string;
	kind?: JobKind;
	executor?: string;
	trigger?: JobTrigger;
	/** For trigger='at': relative ("10m") or RFC 3339 with a timezone. */
	at?: string;
	rrule?: string;
	payload?: string;
};

export async function createJob(body: TaskForm): Promise<JobData> {
	return fetchJSON('/api/jobs', jsonBody(body));
}
