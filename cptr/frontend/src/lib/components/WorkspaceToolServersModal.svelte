<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Modal from './Modal.svelte';
	import ToggleSwitch from './common/ToggleSwitch.svelte';
	import { t } from '$lib/i18n';
	import {
		getWorkspaceState,
		listWorkspaceToolServers,
		saveWorkspaceToolServers,
		type WorkspaceToolServer
	} from '$lib/apis/state';
	import { currentWorkspace } from '$lib/stores';

	interface Props {
		path: string;
		onclose: () => void;
	}

	let { path, onclose }: Props = $props();
	let loading = $state(true);
	let saving = $state(false);
	let servers = $state<WorkspaceToolServer[]>([]);
	let attached = $state<Set<string>>(new Set());

	async function load() {
		try {
			const [data, state] = await Promise.all([
				listWorkspaceToolServers(),
				getWorkspaceState(path)
			]);
			servers = data.servers || [];
			const current = Array.isArray(state.toolServers) ? (state.toolServers as string[]) : [];
			attached = new Set(current);
		} catch {
			toast.error($t('toolServers.loadError'));
		} finally {
			loading = false;
		}
	}

	function toggle(id: string, value: boolean) {
		const next = new Set(attached);
		if (value) next.add(id);
		else next.delete(id);
		attached = next;
	}

	async function save() {
		saving = true;
		try {
			const ids = [...attached];
			await saveWorkspaceToolServers(path, ids);
			currentWorkspace.update((ws) => (ws && ws.path === path ? { ...ws, toolServers: ids } : ws));
			toast.success($t('workspaceTools.saved'));
			onclose();
		} catch {
			toast.error($t('workspaceTools.saveFailed'));
		} finally {
			saving = false;
		}
	}

	load();
</script>

<Modal onclose={onclose} class="w-full max-w-[28rem] mx-4 max-md:mx-0">
	<div class="px-3.5 py-3">
		<h2 class="text-sm text-gray-900 dark:text-white">{$t('workspaceTools.title')}</h2>
		<p class="mt-1 text-[0.6875rem] text-gray-500 dark:text-gray-500">
			{$t('workspaceTools.hint')}
		</p>
	</div>
	<div class="px-3.5 pb-3 max-h-[22rem] overflow-y-auto">
		{#if loading}
			<p class="text-xs text-gray-400">{$t('files.retry')}</p>
		{:else if servers.length === 0}
			<p class="text-xs text-gray-400">{$t('toolServers.empty')}</p>
		{:else}
			<div class="flex flex-col gap-2">
				{#each servers as server (server.id)}
					<label class="flex items-start justify-between gap-3 py-1">
						<span class="min-w-0">
							<span class="block text-xs text-gray-800 dark:text-gray-200">{server.name}</span>
							<span class="block text-[0.6875rem] text-gray-400">
								{server.scope === 'global'
									? $t('workspaceTools.alwaysOn')
									: server.description || server.id}
							</span>
						</span>
						<ToggleSwitch
							value={server.scope === 'global' ? true : attached.has(server.id)}
							disabled={server.scope === 'global' || !server.enabled || saving}
							onchange={(value) => toggle(server.id, value)}
						/>
					</label>
				{/each}
			</div>
		{/if}
	</div>
	<div class="flex justify-end gap-2 px-3.5 py-3">
		<button
			class="text-[0.8125rem] text-gray-500 hover:text-gray-800 dark:hover:text-white"
			onclick={onclose}>{$t('common.cancel')}</button>
		>
		<button
			class="text-[0.8125rem] text-gray-800 dark:text-white disabled:opacity-50"
			onclick={save}
			disabled={saving}>{$t('settings.save')}</button
		>
	</div>
</Modal>
