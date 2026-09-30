<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { t } from '$lib/i18n';
	import { ApiError } from '$lib/apis';
	import {
		adoptUnmanagedService,
		createWorkspaceService,
		deleteWorkspaceService,
		getWorkspaceServices,
		restartWorkspaceService,
		startWorkspaceService,
		stopUnmanagedService,
		stopWorkspaceService,
		updateWorkspaceService,
		type UnmanagedService,
		type WorkspaceService
	} from '$lib/apis/state';
	import Icon from './Icon.svelte';

	interface Props {
		workspace: string;
	}

	let { workspace }: Props = $props();

	let services = $state<WorkspaceService[]>([]);
	let unmanaged = $state<UnmanagedService[]>([]);
	let loaded = $state(false);
	let error = $state('');
	let busyId = $state('');
	let formOpen = $state(false);
	let editingId = $state('');
	let name = $state('');
	let command = $state('');
	let cwd = $state('.');
	let port = $state('');
	let healthUrl = $state('');
	let saving = $state(false);
	let adoptingId = $state('');
	let adoptName = $state('');

	let timer: ReturnType<typeof setInterval> | null = null;

	function statusLabel(status: WorkspaceService['status']): string {
		if (status === 'running') return $t('dashboard.servicesRunning');
		if (status === 'unhealthy') return $t('dashboard.servicesUnhealthy');
		return $t('dashboard.servicesStopped');
	}

	async function load(ws: string) {
		try {
			const view = await getWorkspaceServices(ws);
			if (ws !== workspace) return;
			services = view.services ?? [];
			unmanaged = view.unmanaged ?? [];
			loaded = true;
			error = '';
		} catch (err) {
			if (ws !== workspace) return;
			loaded = true;
			error = err instanceof ApiError ? err.message : String(err);
		}
	}

	function resetForm() {
		formOpen = false;
		editingId = '';
		name = '';
		command = '';
		cwd = '.';
		port = '';
		healthUrl = '';
	}

	function beginAdd() {
		resetForm();
		formOpen = true;
	}

	function beginEdit(service: WorkspaceService) {
		formOpen = true;
		editingId = service.id;
		name = service.name;
		command = service.command;
		cwd = service.cwd || '.';
		port = service.port ? String(service.port) : '';
		healthUrl = service.health_url || '';
	}

	function payload() {
		const trimmed = port.trim();
		const parsed = trimmed ? Number(trimmed) : null;
		if (trimmed && !Number.isFinite(parsed)) {
			throw new Error('port must be a number');
		}
		return {
			name: name.trim(),
			command: command.trim(),
			cwd: cwd.trim() || '.',
			port: parsed,
			health_url: healthUrl.trim()
		};
	}

	async function saveForm() {
		if (saving) return;
		saving = true;
		error = '';
		try {
			if (editingId) {
				await updateWorkspaceService(workspace, editingId, payload());
			} else {
				await createWorkspaceService(workspace, payload());
			}
			resetForm();
			await load(workspace);
		} catch (err) {
			error = err instanceof ApiError ? err.message : String(err);
		} finally {
			saving = false;
		}
	}

	async function act(id: string, fn: () => Promise<unknown>) {
		if (busyId) return;
		busyId = id;
		error = '';
		try {
			await fn();
			await load(workspace);
		} catch (err) {
			error = err instanceof ApiError ? err.message : String(err);
		} finally {
			busyId = '';
		}
	}

	function beginAdopt(row: UnmanagedService) {
		adoptingId = row.command_session_id;
		adoptName = 'server';
	}

	function confirmAdopt(row: UnmanagedService) {
		const guessed = adoptName.trim();
		if (!guessed) return;
		void act(row.command_session_id, async () => {
			await adoptUnmanagedService(workspace, row.command_session_id, {
				name: guessed,
				port: null,
				health_url: ''
			});
			adoptingId = '';
			adoptName = '';
		});
	}

	$effect(() => {
		const ws = workspace;
		loaded = false;
		void load(ws);
	});

	onMount(() => {
		timer = setInterval(() => {
			if (workspace) void load(workspace);
		}, 5000);
	});

	onDestroy(() => {
		if (timer) clearInterval(timer);
	});
</script>

<section class="services">
	<div class="head">
		<h2 class="title">
			<Icon name="terminal" size={15} />
			{$t('dashboard.servicesTitle')}
		</h2>
		{#if !formOpen}
			<button class="link" onclick={beginAdd}>{$t('dashboard.servicesAdd')}</button>
		{/if}
	</div>
	<p class="hint">{$t('dashboard.servicesHint')}</p>

	{#if error}
		<p class="error">{error}</p>
	{/if}

	{#if formOpen}
		<form
			class="form"
			onsubmit={(event) => {
				event.preventDefault();
				void saveForm();
			}}
		>
			<label>
				<span>{$t('dashboard.servicesName')}</span>
				<input bind:value={name} placeholder="api" required />
			</label>
			<label class="grow">
				<span>{$t('dashboard.servicesCommand')}</span>
				<input bind:value={command} placeholder="uvicorn app:app --port 8000" required />
			</label>
			<label>
				<span>{$t('dashboard.servicesDir')}</span>
				<input bind:value={cwd} placeholder="." />
			</label>
			<label>
				<span>{$t('dashboard.servicesPort')}</span>
				<input bind:value={port} inputmode="numeric" placeholder="8000" />
			</label>
			<label class="grow">
				<span>{$t('dashboard.servicesHealth')}</span>
				<input bind:value={healthUrl} placeholder="http://127.0.0.1:8000/health" />
			</label>
			<div class="form-actions">
				<button class="btn-primary" type="submit" disabled={saving || !name.trim() || !command.trim()}>
					{$t('dashboard.servicesSave')}
				</button>
				<button class="btn-secondary" type="button" onclick={resetForm} disabled={saving}>
					{$t('common.cancel')}
				</button>
			</div>
		</form>
	{/if}

	{#if loaded && services.length === 0 && unmanaged.length === 0 && !formOpen}
		<div class="empty">{$t('dashboard.servicesEmpty')}</div>
	{/if}

	{#if services.length}
		<ul class="rows">
			{#each services as service (service.id)}
				<li class="row">
					<div class="main">
						<span class="pill" data-status={service.status}>{statusLabel(service.status)}</span>
						<strong>{service.name}</strong>
						{#if service.url}
							<span class="meta">{service.url}</span>
						{/if}
						<code>{service.command}</code>
					</div>
					<div class="actions">
						{#if service.status === 'stopped'}
							<button
								class="btn-secondary"
								disabled={busyId === service.id}
								onclick={() => act(service.id, () => startWorkspaceService(workspace, service.id))}
							>
								{$t('dashboard.servicesStart')}
							</button>
						{:else}
							<button
								class="btn-secondary"
								disabled={busyId === service.id}
								onclick={() => act(service.id, () => stopWorkspaceService(workspace, service.id))}
							>
								{$t('dashboard.servicesStop')}
							</button>
							<button
								class="btn-secondary"
								disabled={busyId === service.id}
								onclick={() =>
									act(service.id, () => restartWorkspaceService(workspace, service.id))}
							>
								{$t('dashboard.servicesRestart')}
							</button>
						{/if}
						<button class="link" onclick={() => beginEdit(service)}>{$t('dashboard.servicesEdit')}</button>
						<button
							class="link"
							disabled={busyId === service.id}
							onclick={() => act(service.id, () => deleteWorkspaceService(workspace, service.id))}
						>
							{$t('dashboard.servicesRemove')}
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}

	{#if unmanaged.length}
		<h3 class="sub">{$t('dashboard.servicesUnmanaged')}</h3>
		<ul class="rows">
			{#each unmanaged as row (row.command_session_id)}
				<li class="row">
					<div class="main">
						<span class="pill" data-status="running">{$t('dashboard.servicesRunning')}</span>
						<code>{row.command}</code>
						<span class="meta">pid {row.pid}</span>
					</div>
					<div class="actions">
						{#if adoptingId === row.command_session_id}
							<input class="adopt-name" bind:value={adoptName} placeholder="api" />
							<button class="btn-secondary" onclick={() => confirmAdopt(row)}>
								{$t('dashboard.servicesSave')}
							</button>
						{:else}
							<button class="link" onclick={() => beginAdopt(row)}>
								{$t('dashboard.servicesAdopt')}
							</button>
						{/if}
						<button
							class="btn-secondary"
							disabled={busyId === row.command_session_id}
							onclick={() =>
								act(row.command_session_id, () =>
									stopUnmanagedService(workspace, row.command_session_id)
								)}
						>
							{$t('dashboard.servicesStop')}
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.services {
		display: flex;
		flex-direction: column;
		gap: 0.625rem;
		max-width: 46rem;
	}

	.head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.title,
	.sub {
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

	.sub {
		margin-top: 0.5rem;
	}

	.hint,
	.meta,
	.error {
		margin: 0;
		font-size: 0.75rem;
		color: var(--app-fg-muted);
	}

	.error {
		color: var(--app-fg);
	}

	.empty {
		padding: 1rem;
		border: 1px dashed var(--app-border);
		border-radius: 0.625rem;
		color: var(--app-fg-muted);
		font-size: 0.8125rem;
	}

	.form {
		display: flex;
		flex-wrap: wrap;
		gap: 0.625rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		min-width: 8rem;
		font-size: 0.75rem;
		color: var(--app-fg-muted);
	}

	label.grow {
		flex: 1 1 16rem;
	}

	input {
		border: 1px solid var(--app-border);
		border-radius: 0.5rem;
		background: var(--app-bg);
		color: var(--app-fg);
		padding: 0.4rem 0.55rem;
		font: inherit;
		font-size: 0.8125rem;
	}

	.form-actions,
	.actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	.form-actions {
		flex-basis: 100%;
	}

	.rows {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}

	.row {
		display: flex;
		justify-content: space-between;
		gap: 0.75rem;
		align-items: flex-start;
		border: 1px solid var(--app-border);
		border-radius: 0.625rem;
		padding: 0.7rem 0.85rem;
	}

	.main {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.4rem 0.65rem;
		min-width: 0;
	}

	.main code {
		flex-basis: 100%;
		font-size: 0.75rem;
		color: var(--app-fg-muted);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.pill {
		font-size: 0.6875rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.pill[data-status='running'] {
		color: var(--app-fg);
	}

	.pill[data-status='stopped'],
	.pill[data-status='unhealthy'] {
		color: var(--app-fg-muted);
	}

	.btn-primary,
	.btn-secondary,
	.link {
		font-size: 0.75rem;
		cursor: pointer;
	}

	.btn-primary,
	.btn-secondary {
		display: inline-flex;
		align-items: center;
		padding: 0.35rem 0.65rem;
		border-radius: 0.5rem;
	}

	.btn-primary {
		background: var(--app-fg);
		color: var(--app-bg);
		border: 1px solid var(--app-fg);
	}

	.btn-secondary {
		background: transparent;
		color: var(--app-fg);
		border: 1px solid var(--app-border);
	}

	.btn-primary:disabled,
	.btn-secondary:disabled,
	.link:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.link {
		background: none;
		border: 0;
		padding: 0;
		color: var(--app-fg);
		text-decoration: underline;
		text-underline-offset: 2px;
	}

	.adopt-name {
		width: 7rem;
		border: 1px solid var(--app-border);
		border-radius: 0.5rem;
		background: var(--app-bg);
		color: var(--app-fg);
		padding: 0.3rem 0.45rem;
		font: inherit;
		font-size: 0.75rem;
	}
</style>
