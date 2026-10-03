<script lang="ts">
	import { goto } from '$app/navigation';
	import { onDestroy, onMount } from 'svelte';
	import { getChats, type ChatInfo } from '$lib/apis/chat';
	import {
		addTodo,
		approveTodoRequest,
		getTodos,
		rejectTodoRequest,
		removeTodo,
		toggleTodo,
		type TodoData,
		type TodoRequestData
	} from '$lib/apis/todos';
	import {
		cancelJob,
		deferJob,
		deleteJob,
		getJobs,
		patchJob,
		type JobData,
		type JobStatus
	} from '$lib/apis/jobs';
	import {
		addWorkspaceNote,
		deleteWorkspaceNote,
		getWorkspaceNotes,
		getWorkspacePrompt,
		saveWorkspacePrompt,
		type WorkspaceNote
	} from '$lib/apis/state';
	import { get } from 'svelte/store';
	import { currentWorkspace, openChatTab, setActiveTab } from '$lib/stores';
	import { chatModels, defaultModel, refreshChatState } from '$lib/stores/chat';
	import { socketStore } from '$lib/stores/socket.svelte';
	import { t } from '$lib/i18n';
	import { getPathDisplayName } from '$lib/utils/paths';
	import { formatWhen as formatWhenAt, nsToMs, HOUR_MS } from '$lib/utils/when';
	import DeferWhenPicker from './DeferWhenPicker.svelte';
	import Icon from './Icon.svelte';
	import Spinner from './common/Spinner.svelte';
	import WorkspaceServices from './WorkspaceServices.svelte';

	interface Props {
		workspace: string;
	}

	let { workspace }: Props = $props();

	let chats = $state<ChatInfo[]>([]);
	let todos = $state<TodoData[]>([]);
	let jobs = $state<JobData[]>([]);
	let pendingRequests = $state<TodoRequestData[]>([]);

	// ── Workspace prompt ────────────────────────────────────────
	//
	// What this workspace is, in the user's words. It is read back here and
	// prepended to the system prompt of every chat started in this workspace,
	// so the one place to change "what this project is" is this box.
	let workspacePrompt = $state('');
	let promptEditing = $state(false);
	let promptDraft = $state('');
	let promptBusy = $state(false);
	let promptSaved = $state(false);
	let newTodoTitle = $state('');
	let todosBusy = $state(false);

	// ── Workspace notes ─────────────────────────────────────────
	//
	// The workspace's own memory: what the human leaves for the agent, and what
	// the agent leaves for the next chat here (its `add_workspace_note` tool
	// writes the same list). Both sides read them: they are listed here and
	// injected into the system prompt of every chat in this workspace.
	let notes = $state<WorkspaceNote[]>([]);
	let noteDraft = $state('');
	let notesBusy = $state(false);
	let notesError = $state('');

	/** Newest first: a note is nearly always about what just happened. */
	const shownNotes = $derived(
		[...notes].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0))
	);
	let loading = $state(true);
	let failed = $state(false);
	/** The workspace the rows on screen belong to; '' until the first read lands. */
	let loadedFor = $state('');

	/**
	 * Row titles are ellipsised to a single line, which is exactly the wrong
	 * trade for an approval: the half of the sentence that says what is being
	 * asked for is the half that gets cut. One row open at a time, keyed by id —
	 * clicking a label drops its full text open in place.
	 */
	let expandedRowId = $state<string | null>(null);
	let expandedRequestId = $state<string | null>(null);

	// ── Deferred / agent-run state ──────────────────────────────

	/** Which row has its "run later" form open. */
	let deferOpenId = $state<string | null>(null);
	let deferAt = $state('');
	let deferModel = $state('');
	let deferNote = $state('');
	let deferBusy = $state(false);
	let deferError = $state('');
	/** "I'll do it myself": the row waits for the user instead of being run. */
	let deferManual = $state(false);

	/** The clock manual reminders are read against; loading the board resets it. */
	let nowMs = $state(Date.now());

	/**
	 * One row per board entry. The todo list is the human slice of the board, so
	 * a todo and its job share an id: enrich the todo with its job rather than
	 * listing both. Anything on the board with no todo of its own (a note, or a
	 * job a chat put there) is appended.
	 */
	type Row = {
		id: string;
		title: string;
		done: boolean;
		source: string;
		/** False for a board entry that has no `/api/todos` row of its own. */
		fromTodos: boolean;
		job: JobData | null;
	};

	const rows = $derived.by<Row[]>(() => {
		const byId = new Map(jobs.map((job) => [job.id, job]));
		const seen = new Set<string>();
		const out: Row[] = [];

		for (const todo of todos) {
			seen.add(todo.id);
			out.push({
				id: todo.id,
				title: todo.title,
				done: todo.status === 'done',
				source: todo.source,
				fromTodos: true,
				job: byId.get(todo.id) ?? null
			});
		}

		const extra = jobs
			.filter((job) => !seen.has(job.id) && job.status !== 'cancelled')
			.sort((a, b) => (a.trigger_at ?? Infinity) - (b.trigger_at ?? Infinity));
		for (const job of extra) {
			out.push({
				id: job.id,
				title: job.title,
				done: job.status === 'done',
				source: job.source,
				fromTodos: false,
				job
			});
		}

		return out;
	});

	/** True while a run is in flight, or about to be — the only time we poll. */
	const liveRun = $derived(
		jobs.some((job) => {
			if (job.status === 'running' || job.status === 'queued') return true;
			// A manual reminder never fires server-side, so there is nothing to poll
			// for: the clock alone moves it (see `manualReminderAhead`).
			if (job.executor === 'human') return false;
			if (job.trigger !== 'at' || job.status !== 'open' || job.trigger_at == null) return false;
			return nsToMs(job.trigger_at) - Date.now() < 5 * 60_000;
		})
	);

	async function loadTodos(ws: string) {
		if (!ws) return;
		try {
			const data = await getTodos(ws);
			todos = data.todos;
			pendingRequests = data.pending_requests;
		} catch {
			// Keep previous list on error; dashboard shows whatever we have.
		}
	}

	async function loadJobs(ws: string) {
		if (!ws) return;
		try {
			const data = await getJobs(ws);
			jobs = data.jobs;
			nowMs = Date.now();
		} catch {
			// As above: a failed read must not blank the board.
		}
	}

	async function loadBoard(ws: string) {
		await Promise.allSettled([loadTodos(ws), loadJobs(ws)]);
	}

	async function loadNotes(ws: string) {
		if (!ws) return;
		try {
			const data = await getWorkspaceNotes(ws);
			notes = data.notes;
		} catch {
			// As above: a failed read must not blank what is on screen.
		}
	}

	// ── Workspace notes ─────────────────────────────────────────

	async function handleAddNote() {
		const text = noteDraft.trim();
		if (!text || notesBusy) return;
		notesBusy = true;
		notesError = '';
		try {
			const res = await addWorkspaceNote(workspace, text);
			notes = res.notes;
			noteDraft = '';
		} catch (err) {
			// Keep the draft so the note can be retried (or shortened to fit).
			notesError = err instanceof Error ? err.message : String(err);
		} finally {
			notesBusy = false;
		}
	}

	async function handleDeleteNote(id: string) {
		if (notesBusy) return;
		notesBusy = true;
		notesError = '';
		try {
			const res = await deleteWorkspaceNote(workspace, id);
			notes = res.notes;
		} catch (err) {
			notesError = err instanceof Error ? err.message : String(err);
		} finally {
			notesBusy = false;
		}
	}

	// ── Workspace prompt ────────────────────────────────────────

	function startPromptEdit() {
		promptDraft = workspacePrompt;
		promptSaved = false;
		promptEditing = true;
	}

	function cancelPromptEdit() {
		promptDraft = workspacePrompt;
		promptEditing = false;
		promptSaved = false;
	}

	async function handleSavePrompt() {
		if (promptBusy) return;
		promptBusy = true;
		try {
			const res = await saveWorkspacePrompt(workspace, promptDraft.trim());
			workspacePrompt = res.prompt ?? '';
			promptDraft = workspacePrompt;
			promptEditing = false;
			promptSaved = true;
			setTimeout(() => (promptSaved = false), 2000);
		} catch {
			// Keep the box open with the user's text so the save can be retried.
		} finally {
			promptBusy = false;
		}
	}

	$effect(() => {
		const ws = workspace;
		if (!ws) return;
		// The spinner replaces the board, so it may only be shown when there is
		// nothing to replace: the first read for a workspace. Re-reading the same
		// workspace (a store write from anywhere in the app re-runs this effect)
		// otherwise tore the rows down between pointerdown and pointerup, and the
		// click the user made landed on a detached button. Rows are refreshed in
		// place instead; `loadBoard` below has always worked this way.
		loading = loadedFor !== ws;
		failed = false;

		// One read for the whole board: the schedules are rows of it too, so the
		// dashboard no longer needs the automations API it used to call.
		void Promise.allSettled([
			getChats(ws, 8, 0, 'updated_at', 'desc', false),
			getTodos(ws),
			getJobs(ws),
			getWorkspacePrompt(ws),
			getWorkspaceNotes(ws)
		]).then(([chatsResult, todosResult, jobsResult, promptResult, notesResult]) => {
			const chatList = chatsResult.status === 'fulfilled' ? chatsResult.value.chats : [];
			failed = chatsResult.status === 'rejected' && jobsResult.status === 'rejected';

			chats = chatList;
			if (todosResult.status === 'fulfilled') {
				todos = todosResult.value.todos;
				pendingRequests = todosResult.value.pending_requests;
			}
			if (jobsResult.status === 'fulfilled') {
				jobs = jobsResult.value.jobs;
			}
			// A half-typed edit survives a reload of the board; only a read that
			// disagrees with what is on screen replaces it.
			if (promptResult.status === 'fulfilled') {
				workspacePrompt = promptResult.value.prompt ?? '';
				if (!promptEditing) promptDraft = workspacePrompt;
			}
			if (notesResult.status === 'fulfilled') {
				notes = notesResult.value.notes;
			}
			loadedFor = ws;
			loading = false;
		});
	});

	// ── Live updates ────────────────────────────────────────────

	/** A run that settles emits jobs_changed *and* todos_changed; coalesce them. */
	let reloadTimer: ReturnType<typeof setTimeout> | null = null;

	function scheduleReload() {
		if (reloadTimer) return;
		reloadTimer = setTimeout(() => {
			reloadTimer = null;
			void loadBoard(workspace);
		}, 200);
	}

	let docVisible = $state(
		typeof document !== 'undefined' ? document.visibilityState === 'visible' : true
	);

	function handleVisibilityChange() {
		docVisible = document.visibilityState === 'visible';
	}

	let pollTimer: ReturnType<typeof setInterval> | null = null;

	onMount(() => {
		document.addEventListener('visibilitychange', handleVisibilityChange);
		const off = socketStore.on('events:chat', (data: any) => {
			if (data?.workspace !== workspace) return;
			// Either event means the board moved: the todo routes are a shim over
			// the jobs table, so they are always emitted as a pair.
			if (data?.type === 'todos_changed' || data?.type === 'jobs_changed') {
				scheduleReload();
			}
			// A chat's `add_workspace_note` lands mid-turn: the notes are the only
			// thing that moved, so read them back without touching the board.
			if (data?.type === 'workspace_notes_changed') {
				void loadNotes(workspace);
			}
		});
		return () => {
			off();
			document.removeEventListener('visibilitychange', handleVisibilityChange);
			if (reloadTimer) {
				clearTimeout(reloadTimer);
				reloadTimer = null;
			}
		};
	});

	/**
	 * While something is running (or due within minutes) poll, so the row moves
	 * from "waiting to run" the moment it starts. Idle boards issue no requests,
	 * and a backgrounded tab stays quiet: the scheduler emits jobs_changed when a
	 * run settles, so nothing is missed by staying silent.
	 */
	function startPolling() {
		if (pollTimer) return;
		pollTimer = setInterval(() => void loadJobs(workspace), 5_000);
	}

	function stopPolling() {
		if (!pollTimer) return;
		clearInterval(pollTimer);
		pollTimer = null;
	}

	$effect(() => {
		if (liveRun && docVisible && workspace) startPolling();
		else stopPolling();
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});

	// ── Manual reminders ─────────────────────────────────────────

	/**
	 * The clock a manual row needs. A reminder is `executor="human"` plus a
	 * trigger, and the scheduler skips those rows on purpose (`mark_due_queued`
	 * filters them out) — so no response will ever move the row, only time does.
	 * Tick while one is due within the hour, so a row that comes due under the
	 * user's eyes turns into "due now" without a reload; nothing here is a
	 * request, and the ticker stops the moment the moment passes.
	 */
	const manualReminderAhead = $derived(
		jobs.some((job) => {
			if (!isManualReminder(job) || job.trigger_at == null) return false;
			const delta = nsToMs(job.trigger_at) - nowMs;
			return delta > 0 && delta <= HOUR_MS;
		})
	);

	let tickTimer: ReturnType<typeof setInterval> | null = null;

	function startTicking() {
		if (tickTimer) return;
		tickTimer = setInterval(() => (nowMs = Date.now()), 30_000);
	}

	function stopTicking() {
		if (!tickTimer) return;
		clearInterval(tickTimer);
		tickTimer = null;
	}

	$effect(() => {
		if (manualReminderAhead && docVisible) startTicking();
		else stopTicking();
	});

	onDestroy(() => {
		if (tickTimer) clearInterval(tickTimer);
	});

	/**
	 * "Sep 20, 19:08 · in 4 hours", worded once for the whole app so a pick and the
	 * row it lands on read identically. The board mixes units: timestamps are ms,
	 * `trigger_at` is ns, which `nsToMs` reconciles.
	 */
	function formatWhen(ms: number): string {
		return formatWhenAt(ms, $t);
	}

	/** A schedule on the board: open, with a next occurrence to show. */
	const upcoming = $derived(
		jobs
			.filter((job) => job.trigger === 'rrule' && job.status === 'open' && job.trigger_at != null)
			.sort((a, b) => (a.trigger_at ?? 0) - (b.trigger_at ?? 0))
	);

	function formatChatTime(ts: number): string {
		const diffSec = Math.floor((Date.now() - ts) / 1000);
		if (diffSec < 60) return $t('dashboard.now');
		const diffMin = Math.floor(diffSec / 60);
		if (diffMin < 60) return `${diffMin}m`;
		const diffHr = Math.floor(diffMin / 60);
		if (diffHr < 24) return `${diffHr}h`;
		const diffDay = Math.floor(diffHr / 24);
		if (diffDay < 7) return `${diffDay}d`;
		return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	}

	function openWorkspace() {
		const ws = get(currentWorkspace);
		if (ws) {
			for (const group of ws.groups) {
				const files = group.tabs.find((tab) => tab.type === 'files');
				if (files) {
					setActiveTab(files.id, group.id);
					return;
				}
			}
		}
		goto(`/?workspace=${encodeURIComponent(workspace)}`);
	}

	function openChat(chatId: string) {
		openChatTab(chatId);
		goto(`/?workspace=${encodeURIComponent(workspace)}`);
	}

	function newChat() {
		openChatTab();
		goto(`/?workspace=${encodeURIComponent(workspace)}`);
	}

	/** The Tasks tab, unscoped: every workspace's board on one page. */
	function manageScheduled() {
		goto('/scheduled');
	}

	function openTask(id: string) {
		goto(`/scheduled/${id}`);
	}

	/** One row action at a time: every board write shares this latch. */
	async function withTodosBusy(fn: () => Promise<unknown>) {
		if (todosBusy) return;
		todosBusy = true;
		try {
			await fn();
			await loadBoard(workspace);
		} catch {
			/* a failed write leaves the board as it was; the next read is truth */
		} finally {
			todosBusy = false;
		}
	}

	async function handleAddTodo() {
		const title = newTodoTitle.trim();
		if (!title || todosBusy) return;
		await withTodosBusy(async () => {
			await addTodo(workspace, title);
			newTodoTitle = '';
		});
	}

	async function handleToggleTodo(row: Row) {
		// A row with no todo of its own lives only on the board, so it is toggled
		// through the job route; every other row goes through the todo shim.
		if (row.fromTodos) {
			await withTodosBusy(() => toggleTodo(row.id));
			return;
		}
		await withTodosBusy(() => patchJob(row.id, { status: row.done ? 'open' : 'done' }));
	}

	async function handleRemoveTodo(id: string) {
		await withTodosBusy(() => removeTodo(id));
	}

	/** Remove a row: through the todo route when it has a todo, else the board. */
	async function handleRemove(row: Row) {
		if (row.fromTodos) {
			await withTodosBusy(() => removeTodo(row.id));
			return;
		}
		await withTodosBusy(() => deleteJob(row.id));
	}

	// ── Job status text ────────────────────────────────────────

	function statusLabel(status: JobStatus): string {
		switch (status) {
			case 'queued':
				return $t('dashboard.jobQueued');
			case 'running':
				return $t('dashboard.jobRunning');
			case 'needs_review':
				return $t('dashboard.jobReady');
			case 'blocked':
				return $t('dashboard.jobBlocked');
			case 'failed':
				return $t('dashboard.jobFailed');
			case 'cancelled':
				return $t('dashboard.jobStopped');
			default:
				return '';
		}
	}

	/**
	 * A row the user scheduled for themselves: a human executor with a trigger.
	 * Nothing runs it, so the board can only say when it is theirs to do.
	 */
	function isManualReminder(job: JobData | null | undefined): boolean {
		return (
			!!job &&
			job.executor === 'human' &&
			job.status === 'open' &&
			job.trigger === 'at' &&
			job.trigger_at != null
		);
	}

	/** True once a manual reminder's moment has arrived. */
	function isDue(row: Row): boolean {
		const job = row.job;
		if (!job || row.done || !isManualReminder(job) || job.trigger_at == null) return false;
		return nsToMs(job.trigger_at) <= nowMs;
	}

	/** The second line of a row: who is running it, when, and how it went. */
	function rowDetail(row: Row): string {
		const job = row.job;
		if (!job || row.done) return '';

		const bits: string[] = [];
		// An 'open' job with a trigger is waiting for its moment; say so, because a
		// bare date reads like a deadline the user set.
		if (job.status === 'open' && job.trigger === 'at') {
			// A human row is not a run waiting to happen: it waits for the user.
			bits.push($t(isManualReminder(job) ? 'dashboard.reminderWaiting' : 'dashboard.jobScheduled'));
		} else {
			const label = statusLabel(job.status);
			if (label) bits.push(label);
		}
		if (job.trigger === 'at' && job.trigger_at != null) {
			if (isDue(row)) bits.push($t('dashboard.reminderDue'));
			bits.push(formatWhen(nsToMs(job.trigger_at)));
		}
		if (job.executor && job.executor !== 'human') bits.push(job.executor);
		if (job.attempts > 1) bits.push($t('dashboard.jobAttempt', { count: job.attempts }));
		if ((job.status === 'failed' || job.status === 'blocked') && job.last_error) {
			bits.push(job.last_error);
		}
		return bits.join(' · ');
	}

	function runChatId(row: Row): string {
		const id = row.job?.meta?.run_chat_id;
		return typeof id === 'string' ? id : '';
	}

	/** True while a run is in flight or waiting for the user to look at it. */
	function hasPendingRun(row: Row): boolean {
		const job = row.job;
		if (!job || row.done) return false;
		if (job.status === 'running' || job.status === 'queued') return false;
		if (job.status === 'needs_review') return true;
		return job.trigger === 'at' && job.trigger_at != null;
	}

	/** True while the model is mid-turn — the moment the stop button means something. */
	function isRunning(row: Row): boolean {
		const status = row.job?.status;
		if (row.done) return false;
		return status === 'running' || status === 'queued';
	}

	// ── Run later ──────────────────────────────────────────────

	async function toggleDefer(id: string) {
		if (deferOpenId === id) {
			deferOpenId = null;
			return;
		}
		deferOpenId = id;
		deferError = '';
		deferAt = '';
		deferNote = '';
		deferManual = false;
		if ($chatModels.length === 0) await refreshChatState();
		pickDeferModel();
	}

	/**
	 * Preselect the chat's default model, but only when it is one we can actually
	 * offer: a select bound to a value with no matching option renders blank.
	 */
	function pickDeferModel() {
		const ids = $chatModels.map((model) => model.id);
		const preferred = $defaultModel;
		if (preferred && ids.includes(preferred)) deferModel = preferred;
		else deferModel = ids[0] ?? '';
	}

	async function handleDefer(id: string, title: string) {
		const at = deferAt.trim();
		// Manual: the same trigger, but no model — the row is a reminder for the user.
		const executor = deferManual ? 'human' : deferModel;
		if (!at || !executor || deferBusy) return;
		deferBusy = true;
		deferError = '';
		try {
			await deferJob(id, {
				at,
				executor,
				payload: deferNote.trim() ? `${title}\n\n${deferNote.trim()}` : title
			});
			deferOpenId = null;
			deferAt = '';
			deferNote = '';
			await loadBoard(workspace);
		} catch (err) {
			deferError = err instanceof Error ? err.message : String(err);
		} finally {
			deferBusy = false;
		}
	}

	async function handleCancel(id: string) {
		await withTodosBusy(() => cancelJob(id));
		deferOpenId = null;
	}

	async function handleResolveRequest(id: string, approve: boolean) {
		await withTodosBusy(async () => {
			if (approve) await approveTodoRequest(id);
			else await rejectTodoRequest(id);
		});
	}

	function toggleRow(rowId: string) {
		expandedRowId = expandedRowId === rowId ? null : rowId;
	}

	function toggleRequest(id: string) {
		expandedRequestId = expandedRequestId === id ? null : id;
	}

	function requestLabel(req: TodoRequestData): string {
		if (req.action === 'add') return req.title || $t('dashboard.newChat');
		const title = req.title || req.todo_id || '';
		if (req.action === 'complete') return `${$t('dashboard.complete')}: ${title}`;
		if (req.action === 'reopen') return `${$t('dashboard.reopen')}: ${title}`;
		return `${$t('dashboard.remove')}: ${title}`;
	}
</script>

<div class="dashboard">
	<header class="dashboard-header">
		<div class="dashboard-title-row">
			<button
				class="back-btn"
				onclick={openWorkspace}
				aria-label={$t('dashboard.backToWorkspace')}
				title={$t('dashboard.backToWorkspace')}
			>
				<Icon name="arrow-left" size={16} />
			</button>
			<div class="min-w-0">
				<h1 class="dashboard-name">{getPathDisplayName(workspace)}</h1>
				<p class="dashboard-path">{workspace}</p>
			</div>
		</div>
		<div class="dashboard-actions">
			<button class="btn-secondary" onclick={manageScheduled}>
				<Icon name="clock" size={14} />
				{$t('tasks.manage')}
			</button>
			<button class="btn-primary" onclick={newChat}>
				<Icon name="plus" size={14} />
				{$t('dashboard.newChat')}
			</button>
		</div>
	</header>

	<main class="dashboard-body">
		{#if loading}
			<div class="dashboard-loading">
				<Spinner size={20} />
			</div>
		{:else if failed}
			<p class="dashboard-empty">{$t('dashboard.noUpcoming')}</p>
		{:else}
			<!-- What this workspace is. Read here, and prepended to the system
			     prompt of every chat started in this workspace. -->
			<section class="dashboard-section">
				<div class="prompt-head">
					<h2 class="section-title">
						<Icon name="quote" size={15} />
						{$t('dashboard.promptTitle')}
					</h2>
					<div class="prompt-head-actions">
						{#if promptSaved}
							<span class="prompt-saved">{$t('settings.saved')}</span>
						{/if}
						{#if !promptEditing}
							<button class="inline-link" onclick={startPromptEdit}>
								{workspacePrompt ? $t('common.edit') : $t('common.add')}
							</button>
						{/if}
					</div>
				</div>

				{#if promptEditing}
					<textarea
						class="prompt-input"
						rows="4"
						bind:value={promptDraft}
						placeholder={$t('dashboard.promptPlaceholder')}
					></textarea>
					<div class="prompt-actions">
						<button class="btn-primary" onclick={handleSavePrompt} disabled={promptBusy}>
							{$t('common.save')}
						</button>
						<button class="btn-secondary" onclick={cancelPromptEdit} disabled={promptBusy}>
							{$t('common.cancel')}
						</button>
					</div>
				{:else}
					<div class="prompt-card" class:empty={!workspacePrompt}>
						<p class="prompt-text">
							{workspacePrompt || $t('dashboard.promptEmpty')}
						</p>
					</div>
				{/if}

				<p class="prompt-hint">{$t('dashboard.promptHint')}</p>
			</section>

			<!-- Notes stuck on this workspace. The same list is injected into the
			     system prompt of every chat here, and the agent can add to it
			     with its note tool — so this is where both sides talk to each
			     other across a chat boundary. -->
			<section class="dashboard-section">
				<h2 class="section-title">
					<Icon name="brain" size={15} />
					{$t('dashboard.notesTitle')}
				</h2>

				<div class="todo-add">
					<input
						type="text"
						class="todo-input"
						bind:value={noteDraft}
						placeholder={$t('dashboard.notesPlaceholder')}
						onkeydown={(e) => {
							if (e.key === 'Enter') handleAddNote();
						}}
					/>
					<button
						class="btn-secondary"
						onclick={handleAddNote}
						disabled={notesBusy || !noteDraft.trim()}
					>
						{$t('dashboard.addNote')}
					</button>
				</div>

				{#if notesError}
					<p class="notes-error">{notesError}</p>
				{/if}

				{#if shownNotes.length === 0}
					<div class="empty-card">
						<p>{$t('dashboard.notesEmpty')}</p>
					</div>
				{:else}
					<ul class="card-list">
						{#each shownNotes as note (note.id)}
							<li>
								<div class="note-row">
									<div class="row-main">
										<p class="note-text">{note.text}</p>
										<span class="row-detail">
											{note.author === 'agent'
												? $t('dashboard.notesByAgent')
												: $t('dashboard.notesByYou')}
											· {formatWhenAt(note.created_at, $t)}
										</span>
									</div>
									<button
										class="icon-btn note-remove"
										onclick={() => handleDeleteNote(note.id)}
										disabled={notesBusy}
										aria-label={$t('dashboard.remove')}
										title={$t('dashboard.remove')}
									>
										<Icon name="trash" size={13} />
									</button>
								</div>
							</li>
						{/each}
					</ul>
				{/if}

				<p class="prompt-hint">{$t('dashboard.notesHint')}</p>
			</section>

			<WorkspaceServices {workspace} />

			<section class="dashboard-section">
				<h2 class="section-title">
					<Icon name="list" size={15} />
					{$t('dashboard.todos')}
				</h2>
				<div class="todo-add">
					<input
						type="text"
						class="todo-input"
						bind:value={newTodoTitle}
						placeholder={$t('dashboard.addTodoPlaceholder')}
						onkeydown={(e) => {
							if (e.key === 'Enter') handleAddTodo();
						}}
					/>
					<button
						class="btn-secondary"
						onclick={handleAddTodo}
						disabled={todosBusy || !newTodoTitle.trim()}
					>
						{$t('dashboard.addTodo')}
					</button>
				</div>

				{#if rows.length === 0}
					<div class="empty-card">
						<p>{$t('dashboard.noTodos')}</p>
					</div>
				{:else}
					<ul class="card-list">
						{#each rows as row (row.id)}
							{@const detail = rowDetail(row)}
							{@const chatId = runChatId(row)}
							<li>
								<div
									class="todo-row"
									class:attention={row.job?.status === 'needs_review' || isDue(row)}
								>
									{#if isRunning(row)}
										<button
											class="todo-check stop"
											onclick={() => handleCancel(row.id)}
											aria-label={$t('dashboard.jobStop')}
											title={$t('dashboard.jobStop')}
										>
											<Icon name="stop" size={12} />
										</button>
									{:else}
										<button
											class="todo-check {row.done ? 'done' : ''}"
											onclick={() => handleToggleTodo(row)}
											aria-label={row.done ? $t('dashboard.reopen') : $t('dashboard.complete')}
											title={row.done ? $t('dashboard.reopen') : $t('dashboard.complete')}
										>
											{#if row.done}
												<Icon name="check" size={12} />
											{/if}
										</button>
									{/if}
									<div class="row-main">
										<button
											class="row-title-btn"
											onclick={() => toggleRow(row.id)}
											aria-expanded={expandedRowId === row.id}
											title={row.title}
										>
											<Icon
												name={expandedRowId === row.id ? 'chevron-down' : 'chevron-right'}
												size={11}
											/>
											<span
												class="row-label {row.done ? 'done' : ''}"
												class:todo-chat={row.source === 'chat'}
												class:expanded={expandedRowId === row.id}
											>
												{row.title}
											</span>
										</button>
										{#if detail}
											<span class="row-detail" class:expanded={expandedRowId === row.id}>
												{detail}
											</span>
										{/if}
									</div>
									{#if chatId}
										<button
											class="icon-btn"
											onclick={() => openChat(chatId)}
											aria-label={$t('dashboard.jobOpenRun')}
											title={$t('dashboard.jobOpenRun')}
										>
											<Icon name="chat-bubble" size={13} />
										</button>
									{/if}
									{#if !row.done}
										<button
											class="icon-btn"
											class:active={deferOpenId === row.id}
											onclick={() => toggleDefer(row.id)}
											aria-label={$t('dashboard.defer')}
											title={$t('dashboard.defer')}
										>
											<Icon name="clock" size={13} />
										</button>
									{/if}
									{#if hasPendingRun(row)}
										<button
											class="icon-btn"
											onclick={() => handleCancel(row.id)}
											aria-label={$t('dashboard.jobDismissRun')}
											title={$t('dashboard.jobDismissRun')}
										>
											<Icon name="xmark" size={13} />
										</button>
									{/if}
									<button
										class="icon-btn"
										onclick={() => handleRemove(row)}
										aria-label={$t('dashboard.remove')}
										title={$t('dashboard.remove')}
									>
										<Icon name="trash" size={13} />
									</button>
								</div>

								{#if deferOpenId === row.id}
									<div class="defer-form">
										<DeferWhenPicker bind:at={deferAt} />
										<select class="defer-model" bind:value={deferModel} disabled={deferManual}>
											{#each $chatModels as model (model.id)}
												<option value={model.id}>{model.name}</option>
											{/each}
										</select>
										<label class="defer-manual">
											<input type="checkbox" bind:checked={deferManual} />
											{$t('dashboard.deferManual')}
										</label>
										{#if deferManual}
											<span class="defer-hint">{$t('dashboard.deferManualHint')}</span>
										{/if}
										<input
											type="text"
											class="todo-input defer-note"
											bind:value={deferNote}
											placeholder={$t('dashboard.deferNotePlaceholder')}
											onkeydown={(e) => {
												if (e.key === 'Enter') handleDefer(row.id, row.title);
											}}
										/>
										<button
											class="btn-secondary"
											onclick={() => handleDefer(row.id, row.title)}
											disabled={deferBusy || !deferAt.trim() || (!deferManual && !deferModel)}
										>
											{$t('dashboard.deferSubmit')}
										</button>
										<button class="inline-link" onclick={() => (deferOpenId = null)}>
											{$t('dashboard.deferCancel')}
										</button>
										{#if deferError}
											<span class="defer-error">{deferError}</span>
										{/if}
									</div>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}

				{#if pendingRequests.length > 0}
					<h3 class="subsection-title">
						<Icon name="info" size={13} />
						{$t('dashboard.pendingVerification')}
					</h3>
					<ul class="card-list">
						{#each pendingRequests as req (req.id)}
							{@const open = expandedRequestId === req.id}
							<li>
								<div class="todo-row" class:expanded={open}>
									<button
										class="row-title-btn"
										onclick={() => toggleRequest(req.id)}
										aria-expanded={open}
										title={requestLabel(req)}
									>
										<Icon name={open ? 'chevron-down' : 'chevron-right'} size={11} />
										<span class="row-label pending" class:expanded={open}>
											{requestLabel(req)}
										</span>
									</button>
									<button
										class="btn-approve"
										onclick={() => handleResolveRequest(req.id, true)}
										disabled={todosBusy}
									>
										{$t('dashboard.approve')}
									</button>
									<button
										class="btn-reject"
										onclick={() => handleResolveRequest(req.id, false)}
										disabled={todosBusy}
									>
										{$t('dashboard.reject')}
									</button>
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			<section class="dashboard-section">
				<h2 class="section-title">
					<Icon name="clock" size={15} />
					{$t('dashboard.upcoming')}
				</h2>
				{#if upcoming.length === 0}
					<div class="empty-card">
						<p>{$t('dashboard.noUpcoming')}</p>
						<button class="inline-link" onclick={manageScheduled}>
							{$t('dashboard.createScheduledHint')}
						</button>
					</div>
				{:else}
					<ul class="card-list">
						{#each upcoming as task (task.id)}
							<li>
								<button class="row" onclick={() => openTask(task.id)}>
									<Icon name="clock" size={15} />
									<span class="row-label">{task.title}</span>
									<span class="row-meta">{formatWhen(nsToMs(task.trigger_at!))}</span>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			<section class="dashboard-section">
				<h2 class="section-title">
					<Icon name="chat-bubble" size={15} />
					{$t('dashboard.recentChats')}
				</h2>
				{#if chats.length === 0}
					<div class="empty-card">
						<p>{$t('dashboard.noChats')}</p>
					</div>
				{:else}
					<ul class="card-list">
						{#each chats as chat (chat.id)}
							<li>
								<button class="row" onclick={() => openChat(chat.id)}>
									<Icon name="chat-bubble" size={15} />
									<span class="row-label">{chat.title || $t('dashboard.newChat')}</span>
									<span class="row-meta">{formatChatTime(chat.updated_at)}</span>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</section>
		{/if}
	</main>
</div>

<style>
	.dashboard {
		display: flex;
		flex-direction: column;
		height: 100%;
		width: 100%;
		min-width: 0;
		overflow: hidden;
		background: var(--app-bg);
		color: var(--app-fg);
	}

	.dashboard-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
		padding: 1.5rem 2rem 1rem;
		border-bottom: 1px solid var(--app-divider);
	}

	.dashboard-title-row {
		display: flex;
		align-items: flex-start;
		gap: 0.75rem;
		min-width: 0;
	}

	.back-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 1.75rem;
		height: 1.75rem;
		border-radius: 0.5rem;
		border: 1px solid var(--app-border);
		color: var(--app-fg);
		background: transparent;
		cursor: pointer;
		flex-shrink: 0;
		margin-top: 0.1rem;
	}

	.back-btn:hover {
		background: var(--app-hover);
	}

	.dashboard-name {
		margin: 0;
		font-size: 1.125rem;
		font-weight: 600;
		line-height: 1.3;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.dashboard-path {
		margin: 0.125rem 0 0;
		font-size: 0.75rem;
		font-family: var(--font-mono);
		color: var(--app-fg-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.dashboard-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-shrink: 0;
	}

	.btn-primary,
	.btn-secondary {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		padding: 0.45rem 0.75rem;
		border-radius: 0.5rem;
		font-size: 0.75rem;
		font-weight: 500;
		cursor: pointer;
		white-space: nowrap;
	}

	.btn-primary {
		background: var(--app-fg);
		color: var(--app-bg);
		border: 1px solid var(--app-fg);
	}

	.btn-primary:hover {
		background: var(--app-bg);
		color: var(--app-fg);
	}

	.btn-secondary {
		background: transparent;
		color: var(--app-fg);
		border: 1px solid var(--app-border);
	}

	.btn-secondary:hover {
		background: var(--app-hover);
	}

	.btn-secondary:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.dashboard-body {
		flex: 1;
		overflow-y: auto;
		padding: 1.5rem 2rem 2rem;
		display: flex;
		flex-direction: column;
		gap: 1.75rem;
	}

	.dashboard-loading {
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 3rem 0;
		color: var(--app-fg-muted);
	}

	.dashboard-empty {
		color: var(--app-fg-muted);
		font-size: 0.875rem;
	}

	.dashboard-section {
		display: flex;
		flex-direction: column;
		gap: 0.625rem;
		max-width: 46rem;
	}

	.section-title {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin: 0;
		font-size: 0.75rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--app-fg-muted);
	}

	.subsection-title {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin: 0.75rem 0 0;
		font-size: 0.6875rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--app-fg-muted);
	}

	.card-list {
		list-style: none;
		margin: 0;
		padding: 0;
		border: 1px solid var(--app-border);
		border-radius: 0.625rem;
		overflow: hidden;
	}

	.card-list li + li {
		border-top: 1px solid var(--app-divider);
	}

	.row {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		width: 100%;
		padding: 0.625rem 0.875rem;
		background: transparent;
		border: 0;
		cursor: pointer;
		text-align: left;
		color: var(--app-fg);
	}

	.row:hover {
		background: var(--app-hover);
	}

	.row-label {
		flex: 1;
		min-width: 0;
		font-size: 0.8125rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.row-label.done {
		text-decoration: line-through;
		color: var(--app-fg-muted);
	}

	/* Dropped open: the single-line ellipsis is what hid the request, so once the
	   user asks for the whole thing, wrap it and let the row grow. */
	.row-label.expanded {
		white-space: normal;
		overflow: visible;
		text-overflow: clip;
		overflow-wrap: anywhere;
	}

	.row-label.pending {
		font-size: 0.8125rem;
	}

	.row-meta {
		flex-shrink: 0;
		font-size: 0.75rem;
		color: var(--app-fg-muted);
	}

	.empty-card {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding: 1rem 1rem;
		border: 1px dashed var(--app-border);
		border-radius: 0.625rem;
		color: var(--app-fg-muted);
		font-size: 0.8125rem;
	}

	.empty-card p {
		margin: 0;
	}

	.inline-link {
		background: none;
		border: 0;
		padding: 0;
		font-size: 0.8125rem;
		color: var(--app-fg);
		text-decoration: underline;
		text-underline-offset: 2px;
		cursor: pointer;
	}

	.inline-link:hover {
		color: var(--app-fg-muted);
	}

	/* ── Workspace prompt ──────────────────────────────────── */

	.prompt-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.prompt-saved {
		font-size: 0.75rem;
		color: var(--app-fg-muted);
	}

	.prompt-head-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	/* Paper, with an ink edge — no wash behind the text in any theme, so the
	   mono themes stay ink-on-paper. */
	.prompt-card {
		border: 1px solid var(--app-border);
		border-radius: 0.625rem;
		padding: 0.75rem 1rem;
	}

	.prompt-card.empty {
		border-style: dashed;
	}

	.prompt-text {
		margin: 0;
		font-size: 0.8125rem;
		line-height: 1.5;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}

	.prompt-card.empty .prompt-text {
		color: var(--app-fg-muted);
	}

	.prompt-input {
		width: 100%;
		min-height: 5rem;
		resize: vertical;
		padding: 0.6rem 0.75rem;
		border-radius: 0.5rem;
		border: 1px solid var(--app-border);
		background: transparent;
		color: var(--app-fg);
		font-family: inherit;
		font-size: 0.8125rem;
		line-height: 1.5;
	}

	.prompt-input::placeholder {
		color: var(--app-fg-muted);
	}

	.prompt-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.prompt-hint {
		margin: 0;
		font-size: 0.6875rem;
		color: var(--app-fg-muted);
	}

	/* ── Todos ─────────────────────────────────────────────── */

	.todo-add {
		display: flex;
		gap: 0.5rem;
	}

	.todo-input {
		flex: 1;
		min-width: 0;
		padding: 0.45rem 0.75rem;
		border-radius: 0.5rem;
		border: 1px solid var(--app-border);
		background: transparent;
		color: var(--app-fg);
		font-size: 0.8125rem;
	}

	.todo-input::placeholder {
		color: var(--app-fg-muted);
	}

	.todo-row {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		width: 100%;
		padding: 0.625rem 0.875rem;
		background: transparent;
	}

	/* An open row is a paragraph, not a line: keep the buttons level with the
	   first line instead of floating them down the middle of the text. */
	.todo-row.expanded {
		align-items: flex-start;
	}

	/* The whole label is the disclosure control: click it and the title drops
	   open in place, complete, instead of staying cut off at one ellipsised line. */
	.row-title-btn {
		display: flex;
		align-items: center;
		gap: 0.3rem;
		width: 100%;
		min-width: 0;
		padding: 0;
		border: 0;
		background: transparent;
		color: inherit;
		font: inherit;
		text-align: left;
		cursor: pointer;
	}

	.row-title-btn:hover .row-label {
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	.row-title-btn > :global(svg) {
		flex-shrink: 0;
		color: var(--app-fg-muted);
	}

	.todo-check {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 1.1rem;
		height: 1.1rem;
		border-radius: 0.3125rem;
		border: 1px solid var(--app-border);
		background: transparent;
		color: var(--app-bg);
		cursor: pointer;
		flex-shrink: 0;
		padding: 0;
	}

	.todo-check.done {
		background: var(--app-fg);
		border-color: var(--app-fg);
		color: var(--app-bg);
	}

	.todo-chat {
		font-style: italic;
	}

	/* Second line of a row: status, trigger, executor, error. */
	.row-main {
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		flex: 1;
		min-width: 0;
	}

	.row-detail {
		font-size: 0.6875rem;
		color: var(--app-fg-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* Same deal as the title: the status line can carry a job's last_error, and a
	   clipped error message is no use to anyone. */
	.row-detail.expanded {
		white-space: normal;
		overflow: visible;
		text-overflow: clip;
		overflow-wrap: anywhere;
	}

	/* A run landed and is waiting on a human. An ink bar, never a grey wash. */
	.todo-row.attention {
		box-shadow: inset 2px 0 0 var(--app-fg);
	}

	.todo-check.stop {
		border-color: var(--app-fg);
		color: var(--app-fg);
	}

	.icon-btn.active {
		border: 1px solid var(--app-border);
		color: var(--app-fg);
	}

	/* ── Run later ─────────────────────────────────────────── */

	.defer-form {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		padding: 0.625rem 0.875rem;
		border-top: 1px dashed var(--app-divider);
	}

	.defer-form .todo-input {
		flex: 1;
		min-width: 8rem;
	}

	.defer-model {
		padding: 0.45rem 0.5rem;
		border-radius: 0.5rem;
		border: 1px solid var(--app-border);
		background: transparent;
		color: var(--app-fg);
		font-size: 0.8125rem;
		max-width: 14rem;
	}

	/* The one control that changes what "Schedule" means. */
	.defer-manual {
		display: flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.8125rem;
		color: var(--app-fg);
		white-space: nowrap;
		cursor: pointer;
	}

	.defer-manual input {
		accent-color: var(--app-fg);
		cursor: pointer;
	}

	/* A disabled select, drawn from the theme rather than the browser's greys. */
	.defer-model:disabled {
		opacity: 1;
		color: var(--app-fg-muted);
		border-color: var(--app-divider);
		cursor: not-allowed;
	}

	.defer-hint {
		flex-basis: 100%;
		font-size: 0.6875rem;
		color: var(--app-fg-muted);
	}

	.defer-error {
		flex-basis: 100%;
		font-size: 0.6875rem;
		color: var(--app-fg);
		text-decoration: underline;
		text-decoration-style: dotted;
		text-underline-offset: 2px;
	}

	/* ── Workspace notes ───────────────────────────────────── */

	.note-row {
		display: flex;
		align-items: flex-start;
		gap: 0.625rem;
		padding: 0.625rem 0.875rem;
	}

	/* A note is prose, not a row title: the whole sentence is the point, so it
	   wraps instead of being ellipsised onto one line. */
	.note-text {
		margin: 0;
		font-size: 0.8125rem;
		line-height: 1.45;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
	}

	.note-row .row-detail {
		white-space: normal;
	}

	.note-remove {
		flex-shrink: 0;
	}

	/* Ink, dotted-underline — the same "something went wrong" as a failed
	   defer, so an empty theme stays ink-on-paper. */
	.notes-error {
		margin: 0.375rem 0 0;
		font-size: 0.6875rem;
		color: var(--app-fg);
		text-decoration: underline;
		text-decoration-style: dotted;
		text-underline-offset: 2px;
	}

	.icon-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 1.5rem;
		height: 1.5rem;
		border-radius: 0.375rem;
		border: 0;
		background: transparent;
		color: var(--app-fg-muted);
		cursor: pointer;
		flex-shrink: 0;
	}

	.icon-btn:hover {
		background: var(--app-hover);
		color: var(--app-fg);
	}

	.btn-approve,
	.btn-reject {
		flex-shrink: 0;
		padding: 0.3rem 0.6rem;
		border-radius: 0.375rem;
		font-size: 0.6875rem;
		font-weight: 500;
		cursor: pointer;
	}

	.btn-approve {
		background: var(--app-fg);
		color: var(--app-bg);
		border: 1px solid var(--app-fg);
	}

	.btn-approve:hover {
		background: var(--app-bg);
		color: var(--app-fg);
	}

	.btn-reject {
		background: transparent;
		color: var(--app-fg);
		border: 1px solid var(--app-border);
	}

	.btn-reject:hover {
		background: var(--app-hover);
	}

	.btn-approve:disabled,
	.btn-reject:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
