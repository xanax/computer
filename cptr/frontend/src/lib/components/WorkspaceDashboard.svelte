<script lang="ts">
	import { goto } from '$app/navigation';
	import { onDestroy, onMount } from 'svelte';
	import { getAutomations, type AutomationData } from '$lib/apis/automations';
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
		type JobData,
		type JobStatus
	} from '$lib/apis/jobs';
	import { openChatTab } from '$lib/stores';
	import { chatModels, defaultModel, refreshChatState } from '$lib/stores/chat';
	import { socketStore } from '$lib/stores/socket.svelte';
	import { t } from '$lib/i18n';
	import { getPathDisplayName } from '$lib/utils/paths';
	import Icon from './Icon.svelte';
	import Spinner from './common/Spinner.svelte';

	interface Props {
		workspace: string;
	}

	let { workspace }: Props = $props();

	let upcoming = $state<AutomationData[]>([]);
	let chats = $state<ChatInfo[]>([]);
	let todos = $state<TodoData[]>([]);
	let jobs = $state<JobData[]>([]);
	let pendingRequests = $state<TodoRequestData[]>([]);
	let newTodoTitle = $state('');
	let todosBusy = $state(false);
	let loading = $state(true);
	let failed = $state(false);

	// ── Deferred / agent-run state ──────────────────────────────

	/** Which row has its "run later" form open. */
	let deferOpenId = $state<string | null>(null);
	let deferAt = $state('');
	let deferModel = $state('');
	let deferNote = $state('');
	let deferBusy = $state(false);
	let deferError = $state('');

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
		} catch {
			// As above: a failed read must not blank the board.
		}
	}

	async function loadBoard(ws: string) {
		await Promise.allSettled([loadTodos(ws), loadJobs(ws)]);
	}

	$effect(() => {
		const ws = workspace;
		if (!ws) return;
		loading = true;
		failed = false;

		void Promise.allSettled([
			getAutomations(ws),
			getChats(ws, 8, 0, 'updated_at', 'desc', false),
			getTodos(ws),
			getJobs(ws)
		]).then(([autosResult, chatsResult, todosResult, jobsResult]) => {
			const autos = autosResult.status === 'fulfilled' ? autosResult.value.items : [];
			const chatList = chatsResult.status === 'fulfilled' ? chatsResult.value.chats : [];
			failed =
				autosResult.status === 'rejected' && chatsResult.status === 'rejected';

			upcoming = autos
				.filter((a) => a.is_active && a.next_run_at != null)
				.sort((a, b) => (a.next_run_at ?? 0) - (b.next_run_at ?? 0));
			chats = chatList;
			if (todosResult.status === 'fulfilled') {
				todos = todosResult.value.todos;
				pendingRequests = todosResult.value.pending_requests;
			}
			if (jobsResult.status === 'fulfilled') {
				jobs = jobsResult.value.jobs;
			}
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


	/** The board mixes units: timestamps are ms, `trigger_at` is ns. */
	function nsToMs(ns: number): number {
		return Math.floor(ns / 1_000_000);
	}

	/**
	 * "Sep 20, 19:08 · in 4 hours". The relative half is dropped once the moment
	 * has passed — a stale trigger must not read as a countdown.
	 */
	function formatWhen(ms: number): string {
		const date = new Date(ms);
		const diff = ms - Date.now();
		const dateStr = date.toLocaleString(undefined, {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		});

		if (diff <= 0) return dateStr;

		const mins = Math.floor(diff / 60_000);
		if (mins < 1) return `${dateStr} · ${$t('dashboard.now')}`;
		if (mins < 60) return `${dateStr} · ${$t('dashboard.minutes', { count: mins })}`;
		const hrs = Math.floor(mins / 60);
		if (hrs < 24) return `${dateStr} · ${$t('dashboard.hours', { count: hrs })}`;
		const days = Math.floor(hrs / 24);
		return `${dateStr} · ${$t('dashboard.days', { count: days })}`;
	}

	/** Automations carry ns timestamps, like a job's trigger. */
	function formatNextRun(ns: number): string {
		return formatWhen(nsToMs(ns));
	}

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

	function manageScheduled() {
		goto('/scheduled');
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

	/** The second line of a row: who is running it, when, and how it went. */
	function rowDetail(row: Row): string {
		const job = row.job;
		if (!job || row.done) return '';

		const bits: string[] = [];
		// An 'open' job with a trigger is waiting for its moment; say so, because a
		// bare date reads like a deadline the user set.
		if (job.status === 'open' && job.trigger === 'at') bits.push($t('dashboard.jobScheduled'));
		else {
			const label = statusLabel(job.status);
			if (label) bits.push(label);
		}
		if (job.trigger === 'at' && job.trigger_at != null) bits.push(formatWhen(nsToMs(job.trigger_at)));
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
		if (!at || !deferModel || deferBusy) return;
		deferBusy = true;
		deferError = '';
		try {
			await deferJob(id, {
				at,
				executor: deferModel,
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
				{$t('dashboard.manageScheduled')}
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
								<div class="todo-row" class:attention={row.job?.status === 'needs_review'}>
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
											aria-label={row.done
												? $t('dashboard.reopen')
												: $t('dashboard.complete')}
											title={row.done ? $t('dashboard.reopen') : $t('dashboard.complete')}
										>
											{#if row.done}
												<Icon name="check" size={12} />
											{/if}
										</button>
									{/if}
									<div class="row-main">
										<span
											class="row-label {row.done ? 'done' : ''}"
											class:todo-chat={row.source === 'chat'}
										>
											{row.title}
										</span>
										{#if detail}
											<span class="row-detail">{detail}</span>
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
										<input
											type="text"
											class="todo-input defer-at"
											bind:value={deferAt}
											placeholder={$t('dashboard.deferAtPlaceholder')}
											onkeydown={(e) => {
												if (e.key === 'Enter') handleDefer(row.id, row.title);
											}}
										/>
										<select class="defer-model" bind:value={deferModel}>
											{#each $chatModels as model (model.id)}
												<option value={model.id}>{model.name}</option>
											{/each}
										</select>
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
											disabled={deferBusy || !deferAt.trim() || !deferModel}
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
							<li>
								<div class="todo-row">
									<span class="row-label pending">{requestLabel(req)}</span>
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
								<button class="row" onclick={manageScheduled}>
									<Icon name="clock" size={15} />
									<span class="row-label">{task.name}</span>
									<span class="row-meta">{formatNextRun(task.next_run_at!)}</span>
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

	.defer-error {
		flex-basis: 100%;
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
