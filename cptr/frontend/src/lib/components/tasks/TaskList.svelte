<script lang="ts">
	/**
	 * TaskList — every task the user has, whatever it is.
	 *
	 * This is the one list: a plain todo, a reminder waiting for its moment, a
	 * deferred item a model will run, a recurring task and the runs it has
	 * produced are all rows of one `jobs` table read through `/api/jobs`. The
	 * workspace dashboard embeds the same component scoped to one workspace; the
	 * Tasks tab is it unscoped, grouped by workspace.
	 *
	 * The only status a human can set is `done` (via review); agents land
	 * `needs_review`, which is what the approve button is for.
	 */
	import { goto } from '$app/navigation';
	import { onDestroy, onMount } from 'svelte';
	import {
		cancelJob,
		deleteJob,
		getAllJobs,
		getJobRuns,
		reviewJob,
		runJobNow,
		toggleJob,
		type JobData
	} from '$lib/apis/jobs';
	import { sidebarOpen } from '$lib/stores';
	import { socketStore } from '$lib/stores/socket.svelte';
	import { t } from '$lib/i18n';
	import { getPathDisplayName } from '$lib/utils/paths';
	import { formatWhen as formatWhenAt, nsToMs } from '$lib/utils/when';
	import Icon from '../Icon.svelte';
	import Spinner from '../common/Spinner.svelte';
	import ToggleSwitch from '../common/ToggleSwitch.svelte';
	import TaskModal from './TaskModal.svelte';
	import { toast } from 'svelte-sonner';

	interface Props {
		/** Limits the list to one workspace (the dashboard's view). */
		workspace?: string;
		/** Row to open on load, e.g. from an old `/scheduled/<id>` link. */
		focus?: string;
		/** Hide the page chrome, for embedding inside the dashboard. */
		embedded?: boolean;
	}

	let { workspace = '', focus = '', embedded = false }: Props = $props();

	type Filter = 'all' | 'open' | 'review' | 'done' | 'runs';

	let items = $state<JobData[]>([]);
	let loading = $state(true);
	let query = $state('');
	let filter = $state<Filter>('all');
	/** Which row is showing its instructions and run history. */
	let expanded = $state<string | null>(focus || null);
	let runs = $state<Record<string, JobData[]>>({});
	/** One write at a time: the list is the truth, refetched after each. */
	let busy = $state<string | null>(null);
	let showModal = $state(false);
	let collapsed = $state<Record<string, boolean>>({});

	const RUNS = 'run';
	const TASKS = 'task,note';

	async function load(silent = false) {
		if (!silent) loading = items.length === 0;
		try {
			const data = await getAllJobs({
				workspace: workspace || undefined,
				kind: filter === 'runs' ? RUNS : TASKS
			});
			items = data.jobs;
			if (filter === 'runs' && items.length === 0) {
				// A workspace with no runs should not read as a failed load.
				items = [];
			}
		} catch (e: any) {
			if (!silent) toast.error(e?.message || $t('tasks.failedToLoad'));
		} finally {
			loading = false;
		}
	}

	async function loadRuns(id: string) {
		try {
			const data = await getJobRuns(id);
			runs = { ...runs, [id]: data.runs };
		} catch {
			// The row still works without its history.
		}
	}

	$effect(() => {
		void filter;
		void workspace;
		void load(true);
	});

	$effect(() => {
		if (expanded && !runs[expanded]) void loadRuns(expanded);
	});

	// ── Live updates ────────────────────────────────────────────

	let reloadTimer: ReturnType<typeof setTimeout> | null = null;

	function scheduleReload() {
		if (reloadTimer) return;
		reloadTimer = setTimeout(() => {
			reloadTimer = null;
			void load(true).then(refreshOpenRuns);
		}, 200);
	}

	function refreshOpenRuns() {
		if (expanded) void loadRuns(expanded);
	}

	let docVisible = $state(
		typeof document !== 'undefined' ? document.visibilityState === 'visible' : true
	);

	function handleVisibility() {
		docVisible = document.visibilityState === 'visible';
	}

	let pollTimer: ReturnType<typeof setInterval> | null = null;

	onMount(() => {
		document.addEventListener('visibilitychange', handleVisibility);
		// Not scoped to a workspace: this list spans all of them.
		const off = socketStore.on('events:chat', (data: any) => {
			if (data?.type === 'jobs_changed' || data?.type === 'todos_changed') {
				scheduleReload();
			}
		});
		return () => {
			off();
			document.removeEventListener('visibilitychange', handleVisibility);
			if (reloadTimer) clearTimeout(reloadTimer);
		};
	});

	/** While something is in flight, poll, so the row moves without a reload. */
	const live = $derived(
		items.some((job) => job.status === 'running' || job.status === 'queued')
	);

	$effect(() => {
		if (live && docVisible) {
			if (!pollTimer) {
				pollTimer = setInterval(() => {
					void load(true).then(refreshOpenRuns);
				}, 5_000);
			}
		} else if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});

	// ── Rows ────────────────────────────────────────────────────

	const visible = $derived.by(() => {
		const needle = query.trim().toLowerCase();
		return items
			.filter((job) => {
				if (filter === 'open' && !(job.status === 'open' || job.status === 'paused')) {
					return false;
				}
				if (filter === 'review' && job.status !== 'needs_review') return false;
				if (filter === 'done' && job.status !== 'done') return false;
				if (!needle) return true;
				return (
					job.title.toLowerCase().includes(needle) ||
					job.workspace.toLowerCase().includes(needle) ||
					(job.payload || '').toLowerCase().includes(needle)
				);
			})
			.sort((a, b) => (a.trigger_at ?? Infinity) - (b.trigger_at ?? Infinity));
	});

	/** The grouped view: one header per workspace, in first-seen order. */
	const groups = $derived.by(() => {
		const out: { path: string; jobs: JobData[] }[] = [];
		const index = new Map<string, { path: string; jobs: JobData[] }>();
		for (const job of visible) {
			let group = index.get(job.workspace);
			if (!group) {
				group = { path: job.workspace, jobs: [] };
				index.set(job.workspace, group);
				out.push(group);
			}
			group.jobs.push(job);
		}
		out.sort((a, b) => getPathDisplayName(a.path).localeCompare(getPathDisplayName(b.path)));
		return out;
	});

	function toggleRow(id: string) {
		expanded = expanded === id ? null : id;
	}

	function toggleCollapse(path: string) {
		collapsed = { ...collapsed, [path]: !collapsed[path] };
	}

	// ── Writes ──────────────────────────────────────────────────

	async function write(id: string, fn: () => Promise<unknown>, errorKey: string) {
		if (busy) return;
		busy = id;
		try {
			await fn();
			await load(true);
			refreshOpenRuns();
		} catch (e: any) {
			toast.error(e?.message || $t(errorKey));
		} finally {
			busy = null;
		}
	}

	function handleRunNow(job: JobData) {
		void write(job.id, () => runJobNow(job.id), 'tasks.failedToRun');
	}

	function handleToggle(job: JobData) {
		void write(job.id, () => toggleJob(job.id), 'tasks.failedToToggle');
	}

	function handleApprove(job: JobData) {
		void write(job.id, () => reviewJob(job.id, 'done'), 'tasks.failedToReview');
	}

	function handleReopen(job: JobData) {
		void write(job.id, () => reviewJob(job.id, 'open'), 'tasks.failedToReview');
	}

	function handleCancel(job: JobData) {
		void write(job.id, () => cancelJob(job.id), 'tasks.failedToCancel');
	}

	async function handleDelete(job: JobData) {
		if (!confirm($t('tasks.deleteConfirm', { name: job.title }))) return;
		await write(
			job.id,
			async () => {
				await deleteJob(job.id);
				toast.success($t('tasks.deleted'));
			},
			'tasks.failedToDelete'
		);
	}

	function handleSaved() {
		showModal = false;
		void load(true);
	}

	function openChat(chatId: string, jobWorkspace: string) {
		const ws = jobWorkspace || workspace;
		const workspaceQuery = ws ? `workspace=${encodeURIComponent(ws)}&` : '';
		goto(`/?${workspaceQuery}chatId=${encodeURIComponent(chatId)}`);
	}

	// ── Wording ─────────────────────────────────────────────────

	/** How a row is scheduled, in one phrase. */
	function scheduleLabel(job: JobData): string {
		if (job.kind === RUNS) return '';
		if (job.trigger === 'manual') return '';
		if (job.trigger === 'at') {
			if (job.executor === 'human') return $t('tasks.reminder');
			return $t('tasks.deferred');
		}
		if (job.trigger === 'rrule') return frequencyLabel(job.rrule || '');
		return '';
	}

	function frequencyLabel(rrule: string): string {
		if (rrule.includes('COUNT=1')) return $t('tasks.once');
		const match = rrule.match(/FREQ=(\w+)/);
		if (!match) return $t('tasks.custom');
		switch (match[1].toUpperCase()) {
			case 'HOURLY':
				return $t('tasks.hourly');
			case 'DAILY':
				return $t('tasks.daily');
			case 'WEEKLY':
				return $t('tasks.weekly');
			case 'MONTHLY':
				return $t('tasks.monthly');
			default:
				return $t('tasks.custom');
		}
	}

	/**
	 * An instant as "Sep 22, 20:15 · in 20 minutes".
	 *
	 * `formatWhen` takes the translator as an argument so it can stay UI-free;
	 * this is where the component injects it.
	 */
	function formatWhen(ms: number): string {
		return formatWhenAt(ms, $t);
	}

	/** The status of a row, as a word: blank when `open` says enough. */
	function statusLabel(job: JobData): string {
		switch (job.status) {
			case 'queued':
				return $t('dashboard.jobQueued');
			case 'running':
				return $t('dashboard.jobRunning');
			case 'needs_review':
				return $t('tasks.needsReview');
			case 'blocked':
				return $t('dashboard.jobBlocked');
			case 'failed':
				return $t('dashboard.jobFailed');
			case 'cancelled':
				return $t('dashboard.jobStopped');
			case 'paused':
				return $t('tasks.paused');
			case 'done':
				return $t('tasks.done');
			default:
				return '';
		}
	}

	/**
	 * The dot beside a row: solid for what needs the user, hollow for what is
	 * simply waiting. Never a colour alone — the label carries the meaning too.
	 */
	function dotClass(job: JobData): string {
		switch (job.status) {
			case 'needs_review':
				return 'bg-amber-500';
			case 'running':
			case 'queued':
				return 'bg-blue-500';
			case 'failed':
			case 'blocked':
				return 'bg-red-500';
			case 'done':
				return 'bg-emerald-500';
			case 'paused':
			case 'cancelled':
				return 'bg-gray-300 dark:bg-white/20';
			default:
				return 'border border-gray-400 dark:border-white/30';
		}
	}

	/** The second line: who runs it, when, and how the last run went. */
	function detailLine(job: JobData): string {
		const bits: string[] = [];
		const label = statusLabel(job);
		if (label) bits.push(label);
		if (job.trigger === 'at' && job.trigger_at != null) {
			bits.push(formatWhen(nsToMs(job.trigger_at)));
		}
		const schedule = scheduleLabel(job);
		if (schedule) bits.push(schedule);
		if (job.executor && job.executor !== 'human') bits.push(job.executor);
		if (job.kind !== RUNS && job.last_run && job.last_run.status !== 'done') {
			// `created_at` is milliseconds (like `chats`); only `trigger_at` is ns.
			const when = formatWhen(job.last_run.created_at);
			bits.push($t('tasks.lastRun', { when, status: statusLabel(job.last_run) }));
		}
		if (job.attempts > 1) bits.push($t('dashboard.jobAttempt', { count: job.attempts }));
		if ((job.status === 'failed' || job.status === 'blocked') && job.last_error) {
			bits.push(job.last_error);
		}
		return bits.join(' · ');
	}

	function runChatId(job: JobData): string {
		const id = job.meta?.run_chat_id;
		return typeof id === 'string' ? id : '';
	}
</script>

<div class="flex flex-col h-full overflow-hidden">
	<!-- ── Header ── -->
	{#if !embedded}
		<div
			class="flex items-center gap-2 h-9 shrink-0 border-b border-gray-200 dark:border-white/6 {!$sidebarOpen
				? 'px-1.5'
				: 'px-3'}"
		>
			{#if !$sidebarOpen}
				<button
					class="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={() => sidebarOpen.set(true)}
					title={$t('tasks.toggleSidebar')}
				>
					<Icon name="sidebar-expand" size={14} />
				</button>
			{/if}
			<span class="text-xs text-gray-900 dark:text-white">{$t('tasks.title')}</span>
			{#if visible.length > 0}
				<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600">{visible.length}</span>
			{/if}

			<div class="ml-auto flex items-center gap-1">
				<select
					class="text-[0.6875rem] bg-transparent text-gray-400 dark:text-gray-500 outline-none cursor-pointer"
					bind:value={filter}
				>
					<option value="all">{$t('tasks.all')}</option>
					<option value="open">{$t('tasks.open')}</option>
					<option value="review">{$t('tasks.review')}</option>
					<option value="done">{$t('tasks.done')}</option>
					<option value="runs">{$t('tasks.runs')}</option>
				</select>
				<button
					class="flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-75"
					onclick={() => (showModal = true)}
					title={$t('tasks.newTask')}
				>
					<Icon name="plus" size={13} />
				</button>
			</div>
		</div>

		<!-- Search -->
		<div
			class="flex items-center gap-1.5 h-8 px-3 shrink-0 border-b border-gray-200 dark:border-white/6"
		>
			<Icon name="search" size={13} class="text-gray-400 shrink-0" />
			<input
				type="text"
				class="flex-1 border-none outline-none bg-transparent text-xs text-gray-900 dark:text-white placeholder:text-gray-400"
				placeholder={$t('tasks.filter')}
				bind:value={query}
			/>
			{#if query}
				<button class="text-gray-400 flex items-center" onclick={() => (query = '')}>
					<Icon name="xmark" size={11} />
				</button>
			{/if}
		</div>
	{/if}

	<!-- ── List ── -->
	<div class="flex-1 overflow-y-auto {embedded ? '' : 'p-1'}">
		{#if loading}
			<div class="flex items-center justify-center py-12">
				<Spinner size={16} />
			</div>
		{:else if visible.length === 0}
			<div class="flex flex-col items-center justify-center py-12 gap-2">
				<p class="text-xs text-gray-400 dark:text-gray-600">
					{query ? $t('tasks.noMatches') : $t('tasks.noTasks')}
				</p>
				{#if !query && !embedded}
					<button
						class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-1 rounded-lg bg-gray-100 dark:bg-white/6 transition-colors duration-75"
						onclick={() => (showModal = true)}
					>
						{$t('tasks.create')}
					</button>
				{/if}
			</div>
		{:else}
			{#each groups as group (group.path)}
				<!-- One header per workspace: the Tasks tab spans all of them. -->
				{#if !workspace}
					<button
						class="flex items-center gap-1.5 w-full h-7 px-2 text-left text-[0.6875rem] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-75"
						onclick={() => toggleCollapse(group.path)}
						title={group.path}
					>
						<Icon name={collapsed[group.path] ? 'chevron-right' : 'chevron-down'} size={11} />
						<Icon name="folder" size={11} />
						<span class="truncate">{getPathDisplayName(group.path)}</span>
						<span class="text-gray-300 dark:text-gray-600">{group.jobs.length}</span>
					</button>
				{/if}

				{#if !collapsed[group.path]}
					{#each group.jobs as job (job.id)}
						{@const schedule = scheduleLabel(job)}
						{@const detail = detailLine(job)}
						<div class="rounded-md {busy === job.id ? 'opacity-60' : ''}">
							<div class="flex items-center gap-2 min-h-7 px-2 py-0.5">
								<span class="w-1.5 h-1.5 rounded-full shrink-0 {dotClass(job)}"></span>

								<button
									class="flex-1 min-w-0 text-left text-xs {job.status === 'done'
										? 'text-gray-400 dark:text-gray-600 line-through'
										: 'text-gray-700 dark:text-gray-300'} truncate hover:underline cursor-pointer transition-colors duration-75"
									onclick={() => toggleRow(job.id)}
									title={job.title}
								>
									{job.title || $t('tasks.untitled')}
								</button>

								{#if detail}
									<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate max-w-[45%] hidden sm:inline">
										{detail}
									</span>
								{/if}

								<!-- Approve: the only thing that makes a run `done`. -->
								{#if job.status === 'needs_review'}
									<button
										class="flex items-center justify-center w-5 h-5 rounded text-amber-500 hover:text-amber-600 transition-colors duration-75"
										onclick={() => handleApprove(job)}
										title={$t('tasks.approve')}
									>
										<Icon name="check" size={12} />
									</button>
								{/if}

								{#if job.status === 'open' || job.status === 'paused'}
									<button
										class="flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-75"
										onclick={() => handleRunNow(job)}
										title={$t('tasks.runNow')}
									>
										<Icon name="play" size={11} />
									</button>
								{/if}

								{#if job.status === 'running' || job.status === 'queued'}
									<button
										class="flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:text-red-500 transition-colors duration-75"
										onclick={() => handleCancel(job)}
										title={$t('tasks.cancelRun')}
									>
										<Icon name="stop" size={11} />
									</button>
								{/if}

								{#if job.trigger === 'rrule' && job.kind !== RUNS}
									<div class="flex items-center shrink-0">
										<ToggleSwitch
											value={job.status !== 'paused'}
											onchange={() => handleToggle(job)}
										/>
									</div>
								{/if}

								<button
									class="flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:text-red-500 transition-colors duration-75"
									onclick={() => handleDelete(job)}
									title={$t('tasks.delete')}
								>
									<Icon name="trash" size={11} />
								</button>
							</div>

							{#if expanded === job.id}
								<div class="px-3 pb-2 pt-0.5">
									{#if job.payload}
										<div
											class="text-[0.6875rem] text-gray-500 dark:text-gray-400 whitespace-pre-wrap font-mono leading-relaxed max-h-32 overflow-y-auto border-l border-gray-200 dark:border-white/10 pl-2"
										>
											{job.payload}
										</div>
									{/if}

									{#if job.kind !== RUNS}
										<div class="mt-1.5 flex items-center gap-2">
											<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
												{$t('tasks.runs')}
											</span>
											{#if runs[job.id]?.length}
												<span class="text-[0.6875rem] text-gray-300 dark:text-gray-600"
													>{runs[job.id].length}</span
												>
											{/if}
										</div>
										{#if runs[job.id] === undefined}
											<div class="py-1"><Spinner size={11} /></div>
										{:else if runs[job.id].length === 0}
											<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600 py-0.5">
												{$t('tasks.noRuns')}
											</div>
										{:else}
											{#each runs[job.id].slice(0, 8) as run (run.id)}
												<div class="flex items-center gap-2 h-6">
													<span class="w-1.5 h-1.5 rounded-full shrink-0 {dotClass(run)}"></span>
													<span class="text-[0.6875rem] text-gray-500 dark:text-gray-400 shrink-0">
														{formatWhen(run.created_at)}
													</span>
													<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate">
														{statusLabel(run)}
													</span>
													{#if runChatId(run)}
														<button
															class="text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-75 shrink-0"
															onclick={() => openChat(runChatId(run), run.workspace)}
														>
															{$t('tasks.viewChat')}
														</button>
													{/if}
													{#if run.status === 'needs_review'}
														<button
															class="text-[0.6875rem] text-gray-400 hover:text-amber-600 transition-colors duration-75 shrink-0"
															onclick={() => handleApprove(run)}
														>
															{$t('tasks.approve')}
														</button>
													{/if}
												</div>
											{/each}
										{/if}
									{/if}

									{#if job.status === 'done' || job.status === 'cancelled'}
										<button
											class="mt-1 text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-75"
											onclick={() => handleReopen(job)}
										>
											{$t('tasks.reopen')}
										</button>
									{/if}

									{#if !workspace}
										<span class="block mt-1 text-[0.6875rem] text-gray-300 dark:text-gray-600">
											{job.workspace}
										</span>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				{/if}
			{/each}
		{/if}
	</div>
</div>

{#if showModal}
	<TaskModal
		{workspace}
		onclose={() => (showModal = false)}
		onsave={handleSaved}
	/>
{/if}
