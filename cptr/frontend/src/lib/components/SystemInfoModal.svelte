<script lang="ts">
	import { onMount } from 'svelte';
	import Modal from './Modal.svelte';
	import SystemInfo from './SystemInfo.svelte';
	import Spinner from './common/Spinner.svelte';
	import { getWelcome, getHealth, restartServer } from '$lib/apis/state';
	import { ApiError } from '$lib/apis';
	import { t } from '$lib/i18n';

	interface Props {
		onclose: () => void;
	}

	let { onclose }: Props = $props();

	let loading = $state(true);
	let welcomeData = $state<{
		hostname?: string;
		version?: string;
		/** pid of the server that answered: it changes after a restart. */
		pid?: number;
		system?: {
			os: string;
			arch: string;
			python: string;
			cpu_count: number;
			memory_total?: number;
			memory_available?: number;
			disk_total?: number;
			disk_used?: number;
			disk_free?: number;
			uptime_seconds?: number;
			load_avg?: number[];
			cpu_usage?: number;
			network?: { name: string; ip: string }[];
		};
		processes?: { pid: number; cpu: number; mem: number; name: string; cmd?: string }[];
	} | null>(null);

	async function refresh() {
		try {
			welcomeData = (await getWelcome()) as typeof welcomeData;
		} catch {
			welcomeData = null;
		}
	}

	onMount(() => {
		refresh().finally(() => {
			loading = false;
		});
	});

	// ── Server restart ──────────────────────────────────────────
	//
	// The server restarts itself: it stops, comes back on the same port with the
	// same command line, and reports its new pid on /api/health. So the wait is
	// "poll until health answers with a different pid, then reload" — a reload
	// re-attaches the socket and the running chat to the new process.

	const HEALTH_POLL_MS = 750;
	const HEALTH_TIMEOUT_MS = 90_000;

	let confirmingRestart = $state(false);
	let restarting = $state(false);
	let restartStage = $state<'stopping' | 'waiting' | 'slow' | 'failed'>('stopping');
	let restartError = $state('');

	function sleep(ms: number) {
		return new Promise((resolve) => setTimeout(resolve, ms));
	}

	async function restart() {
		confirmingRestart = false;
		restartStage = 'stopping';
		restartError = '';
		restarting = true;
		const previousPid = welcomeData?.pid ?? null;
		try {
			await restartServer();
		} catch (err) {
			restarting = false;
			restartStage = 'failed';
			restartError = err instanceof ApiError ? err.message : String(err);
			return;
		}
		restartStage = 'waiting';
		const deadline = Date.now() + HEALTH_TIMEOUT_MS;
		while (Date.now() < deadline) {
			await sleep(HEALTH_POLL_MS);
			try {
				const health = await getHealth();
				if (previousPid === null || health.pid !== previousPid) {
					// Up, and it is the new process: a full reload drops every
					// socket and the stale chat stream from the old server.
					location.reload();
					return;
				}
			} catch {
				// The old server is down (or going down) — keep waiting.
			}
		}
		restarting = false;
		restartStage = 'slow';
	}
</script>

<Modal {onclose} class="w-full max-w-[26.25rem] mx-4">
	<div class="px-4 py-3.5">
		<div class="mb-3 flex items-baseline justify-between gap-3">
			<div class="min-w-0">
				<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('system.infoTitle')}</h2>
				{#if welcomeData?.hostname}
					<p class="mt-0.5 truncate font-mono text-[0.6875rem] text-gray-400 dark:text-gray-600">
						{welcomeData.hostname}
					</p>
				{/if}
			</div>
		</div>

		{#if loading}
			<div class="flex h-28 items-center justify-center">
				<Spinner size={18} />
			</div>
		{:else if welcomeData?.system}
			<SystemInfo
				system={welcomeData.system}
				processes={welcomeData.processes ?? []}
				serverPid={welcomeData.pid}
				onchanged={refresh}
				defaultOpen
			/>
		{:else}
			<div class="py-8 text-center text-xs text-gray-400 dark:text-gray-600">
				{$t('system.unavailable')}
			</div>
		{/if}

		<div
			class="mt-4 flex items-center justify-between gap-3 border-t border-gray-100 pt-3 dark:border-white/6"
		>
			<span class="min-w-0 text-[0.625rem] leading-snug text-gray-400 dark:text-gray-600">
				{#if restarting}
					{restartStage === 'waiting'
						? $t('system.restartWaiting')
						: $t('system.restartStopping')}
				{:else if restartStage === 'slow'}
					{$t('system.restartSlow')}
				{:else if restartStage === 'failed'}
					{$t('system.restartFailed', { message: restartError })}
				{:else if confirmingRestart}
					{$t('system.restartConfirm')}
				{:else}
					{$t('system.restartHint', { pid: welcomeData?.pid ?? '?' })}
				{/if}
			</span>
			{#if restarting}
				<Spinner size={14} />
			{:else if confirmingRestart}
				<span class="flex shrink-0 items-center gap-1.5">
					<button
						type="button"
						class="h-7 rounded-lg bg-gray-900 px-2.5 text-[0.6875rem] font-medium text-white transition-colors hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-white/90"
						onclick={restart}
					>
						{$t('system.restart')}
					</button>
					<button
						type="button"
						class="h-7 rounded-lg px-2 text-[0.6875rem] text-gray-500 transition-colors hover:bg-gray-200/60 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-gray-200"
						onclick={() => (confirmingRestart = false)}
					>
						{$t('common.cancel')}
					</button>
				</span>
			{:else if restartStage === 'slow'}
				<button
					type="button"
					class="h-7 shrink-0 rounded-lg border border-gray-300 px-2.5 text-[0.6875rem] text-gray-600 transition-colors hover:bg-gray-200/60 dark:border-white/20 dark:text-gray-400 dark:hover:bg-white/6"
					onclick={() => location.reload()}
				>
					{$t('system.restartReload')}
				</button>
			{:else}
				<button
					type="button"
					class="h-7 shrink-0 rounded-lg border border-gray-300 px-2.5 text-[0.6875rem] text-gray-600 transition-colors hover:bg-gray-200/60 hover:text-gray-900 disabled:cursor-default disabled:opacity-50 dark:border-white/20 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-white"
					disabled={!welcomeData?.system}
					onclick={() => {
						restartError = '';
						if (restartStage === 'failed') restartStage = 'stopping';
						confirmingRestart = true;
					}}
				>
					{$t('system.restart')}
				</button>
			{/if}
		</div>
	</div>
</Modal>
