<script lang="ts">
	import Collapsible from './Collapsible.svelte';
	import { ApiError } from '$lib/apis';
	import { killProcess } from '$lib/apis/state';
	import { t } from '$lib/i18n';

	interface SystemInfo {
		os: string;
		arch: string;
		python: string;
		cpu_count: number;
		cpu_usage?: number;
		memory_total?: number;
		memory_available?: number;
		disk_total?: number;
		disk_used?: number;
		disk_free?: number;
		uptime_seconds?: number;
		load_avg?: number[];
		network?: { name: string; ip: string }[];
	}

	interface Process {
		pid: number;
		cpu: number;
		mem: number;
		name: string;
		/** Full command line, when the platform can provide it. */
		cmd?: string;
	}

	interface Props {
		system: SystemInfo;
		processes?: Process[];
		/** pid of the cptr server itself: that row restarts rather than kills. */
		serverPid?: number;
		defaultOpen?: boolean;
		/** Called to re-read system info after a process was signalled. */
		onchanged?: () => void;
	}

	let {
		system: sys,
		processes = [],
		serverPid,
		defaultOpen = false,
		onchanged
	}: Props = $props();

	// ── Process control ─────────────────────────────────────────

	/** pid whose ✕ was clicked; the row is showing its confirm bar. */
	let confirming = $state<number | null>(null);
	/** pid with a signal request in flight. */
	let busyPid = $state<number | null>(null);
	/** pids signalled from this dialog; hidden until the panel is reopened. */
	let signalled = $state<number[]>([]);
	let killError = $state<{ pid: number; message: string } | null>(null);

	const visible = $derived(processes.filter((proc) => !signalled.includes(proc.pid)));

	function commandLine(proc: Process): string {
		return proc.cmd?.trim() || proc.name;
	}

	async function kill(proc: Process, force: boolean) {
		busyPid = proc.pid;
		killError = null;
		try {
			await killProcess(proc.pid, force);
			signalled = [...signalled, proc.pid];
			confirming = null;
			// The signal is asynchronous: resync once it has had time to exit.
			setTimeout(() => onchanged?.(), 1500);
		} catch (err) {
			killError = {
				pid: proc.pid,
				message: err instanceof ApiError ? err.message : String(err)
			};
		} finally {
			busyPid = null;
		}
	}

	function formatBytes(bytes: number): string {
		if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(0)} MB`;
		return `${(bytes / 1073741824).toFixed(1)} GB`;
	}

	function formatUptime(seconds: number): string {
		const days = Math.floor(seconds / 86400);
		const hours = Math.floor((seconds % 86400) / 3600);
		if (days > 0) return `${days}d ${hours}h`;
		const mins = Math.floor((seconds % 3600) / 60);
		if (hours > 0) return `${hours}h ${mins}m`;
		return `${mins}m`;
	}

	const summary = $derived.by(() => {
		const parts: string[] = [];
		if (sys.cpu_usage != null) parts.push($t('system.summaryCpu', { percent: sys.cpu_usage }));
		if (sys.memory_total) {
			const memPct = Math.round(
				((sys.memory_total - (sys.memory_available ?? 0)) / sys.memory_total) * 100
			);
			parts.push($t('system.summaryMemory', { percent: memPct }));
		}
		if (sys.disk_total) {
			const diskPct = Math.round(((sys.disk_used ?? 0) / sys.disk_total) * 100);
			parts.push($t('system.summaryDisk', { percent: diskPct }));
		}
		if (sys.uptime_seconds)
			parts.push($t('system.uptime', { value: formatUptime(sys.uptime_seconds) }));
		return parts.join('  ');
	});
</script>

<Collapsible title={$t('system.title')} {summary} open={defaultOpen}>
	<div class="flex flex-col gap-3">
		{#if sys.cpu_usage != null}
			<div>
				<div class="flex items-center justify-between mb-1">
					<span class="text-[0.6875rem] text-gray-500 dark:text-gray-500">{$t('system.cpu')}</span>
					<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600 font-mono"
						>{sys.cpu_usage}%</span
					>
				</div>
				<div class="h-1.5 rounded-full bg-gray-100 dark:bg-white/6 overflow-hidden">
					<div
						class="h-full rounded-full bg-gray-400 dark:bg-gray-500 transition-all"
						style="width: {sys.cpu_usage}%"
					></div>
				</div>
			</div>
		{/if}

		{#if sys.memory_total}
			{@const memUsed = sys.memory_total - (sys.memory_available ?? 0)}
			{@const memPct = Math.round((memUsed / sys.memory_total) * 100)}
			<div>
				<div class="flex items-center justify-between mb-1">
					<span class="text-[0.6875rem] text-gray-500 dark:text-gray-500"
						>{$t('system.memory')}</span
					>
					<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600 font-mono"
						>{formatBytes(memUsed)} / {formatBytes(sys.memory_total)}</span
					>
				</div>
				<div class="h-1.5 rounded-full bg-gray-100 dark:bg-white/6 overflow-hidden">
					<div
						class="h-full rounded-full bg-gray-400 dark:bg-gray-500 transition-all"
						style="width: {memPct}%"
					></div>
				</div>
			</div>
		{/if}

		{#if sys.disk_total}
			{@const diskPct = Math.round(((sys.disk_used ?? 0) / sys.disk_total) * 100)}
			<div>
				<div class="flex items-center justify-between mb-1">
					<span class="text-[0.6875rem] text-gray-500 dark:text-gray-500">{$t('system.disk')}</span>
					<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600 font-mono"
						>{formatBytes(sys.disk_used ?? 0)} / {formatBytes(sys.disk_total)}</span
					>
				</div>
				<div class="h-1.5 rounded-full bg-gray-100 dark:bg-white/6 overflow-hidden">
					<div
						class="h-full rounded-full bg-gray-400 dark:bg-gray-500 transition-all"
						style="width: {diskPct}%"
					></div>
				</div>
			</div>
		{/if}

		<div
			class="flex items-center gap-4 text-[0.6875rem] text-gray-400 dark:text-gray-600 font-mono"
		>
			<span>{$t('system.cores', { count: sys.cpu_count })}</span>
			<span>{sys.arch}</span>
			{#if sys.uptime_seconds}
				<span>{$t('system.uptime', { value: formatUptime(sys.uptime_seconds) })}</span>
			{/if}
			{#if sys.load_avg}
				<span>{$t('system.load', { value: sys.load_avg.join(' ') })}</span>
			{/if}
		</div>

		{#if sys.network?.length}
			<div
				class="flex flex-wrap gap-x-4 gap-y-0.5 text-[0.6875rem] font-mono text-gray-400 dark:text-gray-600"
			>
				{#each sys.network as iface}
					<span>{iface.name} {iface.ip}</span>
				{/each}
			</div>
		{/if}

		{#if visible.length}
			<div class="mt-1">
				<div
					class="mb-1 flex items-center gap-2 font-mono text-[0.6875rem] text-gray-400 dark:text-gray-600"
				>
					<span class="w-12 text-right">{$t('system.cpuShort')}</span>
					<span class="w-11 text-right">{$t('system.memoryShort')}</span>
					<span class="flex-1">{$t('system.process')}</span>
					<span class="w-6"></span>
				</div>
				<div class="flex flex-col gap-2">
					{#each visible as proc (proc.pid)}
						{@const isServer = proc.pid === serverPid}
						<div class="flex items-start gap-2">
							<span
								class="w-12 shrink-0 text-right font-mono text-[0.6875rem] text-gray-700 dark:text-gray-300"
								>{proc.cpu.toFixed(1)}%</span
							>
							<span
								class="w-11 shrink-0 text-right font-mono text-[0.6875rem] text-gray-500 dark:text-gray-500"
								>{proc.mem.toFixed(1)}%</span
							>
							<div class="min-w-0 flex-1">
								<div class="flex items-baseline gap-1.5">
									<span
										class="truncate font-mono text-[0.6875rem] text-gray-700 dark:text-gray-300"
										>{proc.name}</span
									>
									<span class="shrink-0 font-mono text-[0.625rem] text-gray-400 dark:text-gray-600"
										>{proc.pid}</span
									>
								</div>
								<div
									class="mt-0.5 break-all font-mono text-[0.625rem] leading-snug text-gray-400 dark:text-gray-600"
								>
									{commandLine(proc)}
								</div>
								{#if killError?.pid === proc.pid}
									<div class="mt-1 font-mono text-[0.625rem] text-gray-600 dark:text-gray-400">
										{killError.message}
									</div>
								{/if}
								{#if confirming === proc.pid}
									<div class="mt-1.5 flex flex-wrap items-center gap-1.5">
										<span class="text-[0.625rem] text-gray-500 dark:text-gray-500">
											{$t('system.killConfirm', { name: proc.name })}
										</span>
										<button
											type="button"
											disabled={busyPid === proc.pid}
											class="h-6 rounded-md bg-gray-900 px-2 text-[0.6875rem] font-medium text-white transition-colors hover:bg-gray-800 disabled:cursor-default disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-white/90"
											onclick={() => kill(proc, false)}
										>
											{$t('system.kill')}
										</button>
										<button
											type="button"
											disabled={busyPid === proc.pid}
											title={$t('system.killForceHint')}
											class="h-6 rounded-md border border-gray-300 px-2 text-[0.6875rem] text-gray-600 transition-colors hover:bg-gray-200/60 disabled:cursor-default disabled:opacity-50 dark:border-white/20 dark:text-gray-400 dark:hover:bg-white/6"
											onclick={() => kill(proc, true)}
										>
											{$t('system.killForce')}
										</button>
									</div>
								{/if}
							</div>
							{#if isServer}
								<span
									class="mt-0.5 shrink-0 rounded-md border border-gray-300 px-1 font-mono text-[0.5625rem] uppercase leading-4 text-gray-500 dark:border-white/20 dark:text-gray-500"
									>{$t('system.cptrRow')}</span
								>
							{:else}
								<button
									type="button"
									class="mt-0.5 shrink-0 rounded-md border border-gray-300 px-1.5 font-mono text-[0.6875rem] leading-4 text-gray-500 transition-colors hover:border-gray-400 hover:bg-gray-200/60 hover:text-gray-700 dark:border-white/20 dark:text-gray-500 dark:hover:bg-white/6 dark:hover:text-gray-300"
									title={$t('system.killTitle', { name: proc.name, pid: proc.pid })}
									aria-label={$t('system.killTitle', { name: proc.name, pid: proc.pid })}
									onclick={() => {
										killError = null;
										confirming = confirming === proc.pid ? null : proc.pid;
									}}
								>
									✕
								</button>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		{/if}
	</div>
</Collapsible>
