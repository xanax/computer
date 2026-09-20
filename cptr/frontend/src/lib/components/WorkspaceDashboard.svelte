<script lang="ts">
	import { goto } from '$app/navigation';
	import { getAutomations, type AutomationData } from '$lib/apis/automations';
	import { getChats, type ChatInfo } from '$lib/apis/chat';
	import { openChatTab } from '$lib/stores';
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
	let loading = $state(true);
	let failed = $state(false);

	$effect(() => {
		const ws = workspace;
		if (!ws) return;
		loading = true;
		failed = false;

		void Promise.allSettled([
			getAutomations(ws),
			getChats(ws, 8, 0, 'updated_at', 'desc', false)
		]).then(([autosResult, chatsResult]) => {
			const autos = autosResult.status === 'fulfilled' ? autosResult.value.items : [];
			const chatList = chatsResult.status === 'fulfilled' ? chatsResult.value.chats : [];
			failed =
				autosResult.status === 'rejected' && chatsResult.status === 'rejected';

			upcoming = autos
				.filter((a) => a.is_active && a.next_run_at != null)
				.sort((a, b) => (a.next_run_at ?? 0) - (b.next_run_at ?? 0));
			chats = chatList;
			loading = false;
		});
	});

	function formatNextRun(ns: number): string {
		const ms = ns / 1_000_000;
		const date = new Date(ms);
		const diff = date.getTime() - Date.now();
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
</style>
