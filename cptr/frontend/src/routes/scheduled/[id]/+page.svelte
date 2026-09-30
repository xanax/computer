<script lang="ts">
	/**
	 * A task by id — the same list with that row open.
	 *
	 * Older links (`/scheduled/<automation id>`) still land here: the automations
	 * of the previous release are rows of this same board now.
	 */
	import { page } from '$app/stores';
	import TaskList from '$lib/components/tasks/TaskList.svelte';
	import { t } from '$lib/i18n';

	const taskId = $derived($page.params.id);
</script>

<svelte:head>
	<title>{$t('tasks.title')} / Computer</title>
</svelte:head>

<TaskList focus={taskId} />
