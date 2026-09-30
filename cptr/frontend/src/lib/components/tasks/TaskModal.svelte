<script lang="ts">
	/**
	 * TaskModal — add something to the board.
	 *
	 * One form for every kind of row: a plain todo for the user, a reminder, or
	 * work a model does on a schedule. The trigger and the executor are the only
	 * two things that differ, and they are the only two choices here:
	 *
	 *   executor = 'human'  → it waits for the user (a todo, or a reminder)
	 *   executor = <model>  → a model runs it, now or at the picked time
	 *
	 * The picked schedule is translated into the shape `/api/jobs` takes: a
	 * one-off ("ONCE") becomes `trigger='at'`, anything repeating becomes
	 * `trigger='rrule'`, and "manual" means neither — a plain board entry.
	 */
	import Modal from '../Modal.svelte';
	import Icon from '../Icon.svelte';
	import DropdownMenu from '../DropdownMenu.svelte';
	import ModelSelector from '../common/ModelSelector.svelte';
	import ScheduleDropdown from './ScheduleDropdown.svelte';
	import { workspaceList } from '$lib/stores';
	import { chatModels, defaultModel } from '$lib/stores/chat';
	import { getPathDisplayName } from '$lib/utils/paths';
	import { createJob, type JobData, type TaskForm } from '$lib/apis/jobs';
	import { fromInputs } from '$lib/utils/when';
	import { toast } from 'svelte-sonner';
	import { t } from '$lib/i18n';

	interface Props {
		/** Preselects a workspace; without it the first one is used. */
		workspace?: string;
		onclose: () => void;
		onsave: (job: JobData) => void;
	}

	let { workspace = '', onclose, onsave }: Props = $props();

	let title = $state('');
	let payload = $state('');
	let modelId = $state($defaultModel || '');
	/** 'human' means the user does it; anything else is a model id. */
	let executor = $state('human');
	let rrule = $state('');
	let saving = $state(false);

	const MANUAL = '';

	// ── Workspace ───────────────────────────────────────────────

	let targetWorkspace = $state(workspace);
	let showWsMenu = $state(false);
	let workspaceButtonEl: HTMLButtonElement | undefined = $state();

	$effect(() => {
		if (!targetWorkspace && $workspaceList.length > 0) {
			targetWorkspace = $workspaceList[0].path;
		}
	});

	let workspaceMenuItems = $derived(
		$workspaceList.map((option) => ({
			label: option.name,
			icon: 'folder',
			active: option.path === targetWorkspace,
			check: true,
			onclick: () => {
				targetWorkspace = option.path;
			}
		}))
	);

	let selectedWorkspaceName = $derived(
		$workspaceList.find((w) => w.path === targetWorkspace)?.name ||
			getPathDisplayName(targetWorkspace) ||
			$t('tasks.selectWorkspace')
	);

	// ── Schedule → trigger ──────────────────────────────────────

	/** The instant a "ONCE" DTSTART line points at, or null if unreadable. */
	function onceInstant(value: string): number | null {
		const match = value.match(/DTSTART:(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/);
		if (!match) return null;
		return fromInputs(`${match[1]}-${match[2]}-${match[3]}`, `${match[4]}:${match[5]}`);
	}

	const isOnce = $derived(rrule.includes('COUNT=1'));

	/** A one-off in the past would be scheduled to never fire; catch it here. */
	const onceInPast = $derived.by(() => {
		if (!isOnce || !rrule) return false;
		const at = onceInstant(rrule);
		return at != null && at <= Date.now();
	});

	// ── Submit ──────────────────────────────────────────────────

	const canSave = $derived(
		!!title.trim() &&
			!!targetWorkspace &&
			(executor === 'human' || !!modelId) &&
			!onceInPast
	);

	async function handleSubmit() {
		if (!canSave || saving) return;
		saving = true;
		try {
			const form: TaskForm = {
				workspace: targetWorkspace,
				title: title.trim(),
				kind: 'task',
				executor: executor === 'human' ? 'human' : modelId,
				payload: payload.trim() || undefined
			};
			if (!rrule) {
				form.trigger = 'manual';
			} else if (isOnce) {
				const at = onceInstant(rrule);
				form.trigger = 'at';
				form.at = at != null ? new Date(at).toISOString() : undefined;
			} else {
				form.trigger = 'rrule';
				form.rrule = rrule;
			}
			onsave(await createJob(form));
			onclose();
		} catch (e: any) {
			toast.error(e?.message || $t('tasks.failedToCreate'));
		} finally {
			saving = false;
		}
	}
</script>

<Modal {onclose} class="w-full max-w-2xl">
	<div>
		<!-- Title -->
		<div class="px-5 pt-4 pb-2 flex items-center">
			<input
				class="w-full text-lg bg-transparent outline-none placeholder:text-gray-300 dark:placeholder:text-gray-700 text-gray-900 dark:text-white"
				type="text"
				bind:value={title}
				placeholder={$t('tasks.titlePlaceholder')}
				onkeydown={(e) => {
					if (e.key === 'Enter' && !e.shiftKey) void handleSubmit();
				}}
			/>
			<button
				class="shrink-0 ml-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
				onclick={onclose}
				title={$t('tasks.cancel')}
			>
				<Icon name="xmark" size={18} />
			</button>
		</div>

		<!-- Instructions: what the run is told to do -->
		{#if executor !== 'human'}
			<div class="px-5 pb-2">
				<div class="mb-1 text-[0.6875rem] text-gray-400 dark:text-gray-500">
					{$t('tasks.instructions')}
				</div>
				<textarea
					class="w-full text-xs bg-transparent outline-none placeholder:text-gray-300 dark:placeholder:text-gray-700 text-gray-700 dark:text-gray-300 resize-none"
					bind:value={payload}
					rows={7}
					style="min-height: 9rem;"
					placeholder={$t('tasks.promptPlaceholder')}
				></textarea>
			</div>
		{/if}

		<!-- Bottom toolbar -->
		<div class="flex items-center justify-between px-4 pb-3.5 pt-1 gap-2">
			<div class="flex items-center gap-1 flex-wrap flex-1 min-w-0">
				<!-- Who does it: the one choice that changes everything else -->
				<div class="flex items-center rounded-lg overflow-hidden">
					<button
						type="button"
						class="px-2 py-1 text-[0.6875rem] transition-colors duration-100 {executor === 'human'
							? 'bg-gray-100 dark:bg-white/10 text-gray-700 dark:text-gray-200'
							: 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'}"
						onclick={() => (executor = 'human')}
					>
						{$t('tasks.forMe')}
					</button>
					<button
						type="button"
						class="px-2 py-1 text-[0.6875rem] transition-colors duration-100 {executor !== 'human'
							? 'bg-gray-100 dark:bg-white/10 text-gray-700 dark:text-gray-200'
							: 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'}"
						onclick={() => {
							executor = modelId || $defaultModel || $chatModels[0]?.id || '';
						}}
					>
						{$t('tasks.forModel')}
					</button>
				</div>

				{#if executor !== 'human'}
					<ModelSelector bind:selectedModel={modelId} />
				{/if}

				<!-- When -->
				<ScheduleDropdown bind:rrule />

				<!-- Where -->
				<button
					bind:this={workspaceButtonEl}
					type="button"
					class="flex items-center gap-1 px-2 py-1 rounded-lg text-[0.6875rem] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 transition-colors duration-100"
					onclick={() => (showWsMenu = !showWsMenu)}
				>
					<Icon name="folder" size={12} />
					<span class="truncate max-w-[7.5rem]">{selectedWorkspaceName}</span>
					<Icon name="chevron-down" size={11} />
				</button>
			</div>

			<div class="flex items-center gap-2 shrink-0">
				{#if onceInPast}
					<span class="text-[0.6875rem] text-red-500">{$t('tasks.pastTime')}</span>
				{/if}
				<button
					class="px-3 py-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
					type="button"
					onclick={onclose}
				>
					{$t('tasks.cancel')}
				</button>
				<button
					class="px-3.5 py-1.5 text-xs bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-white/90 transition-colors rounded-full disabled:opacity-50"
					type="button"
					onclick={handleSubmit}
					disabled={saving || !canSave}
				>
					{saving ? $t('tasks.saving') : $t('tasks.createBtn')}
				</button>
			</div>
		</div>
	</div>
</Modal>

{#if showWsMenu && workspaceButtonEl}
	<DropdownMenu
		items={workspaceMenuItems}
		anchor={workspaceButtonEl}
		onclose={() => (showWsMenu = false)}
		preferAbove={true}
		maxHeight="15rem"
		className="w-48"
	/>
{/if}
