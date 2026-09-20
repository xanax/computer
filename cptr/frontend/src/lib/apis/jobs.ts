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
	| 'cancelled';

export type JobData = {
	id: string;
	workspace: string;
	title: string;
	kind: 'task' | 'note';
	/** 'human' or a model id. */
	executor: string;
	trigger: 'manual' | 'at' | 'rrule';
	/** Epoch ns the job is due, for `trigger='at'` (the rest of the board is ms). */
	trigger_at: number | null;
	rrule: string | null;
	status: JobStatus;
	priority: number;
	attempts: number;
	last_error: string | null;
	parent_chat: string | null;
	source: 'human' | 'chat';
	origin_chat: string | null;
	payload: string | null;
	meta: Record<string, unknown> & { run_chat_id?: string; run_message_id?: string };
	created_at: number;
	updated_at: number;
};

export type JobsResponse = {
	jobs: JobData[];
	counts: Record<string, number>;
	open_statuses: JobStatus[];
};

export async function getJobs(workspace: string): Promise<JobsResponse> {
	return fetchJSON(`/api/jobs?workspace=${encodeURIComponent(workspace)}`);
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
