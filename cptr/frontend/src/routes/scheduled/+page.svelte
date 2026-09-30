<script lang="ts">
	/**
	 * The Tasks tab: every task the user has, in every workspace.
	 *
	 * A plain todo, a reminder, a deferred item and a recurring task are all rows
	 * of the same board, so this is one list rather than one page per kind.
	 */
	import TaskList from '$lib/components/tasks/TaskList.svelte';
	import { t } from '$lib/i18n';
</script>

<svelte:head>
	<title>{$t('tasks.title')} / Computer</title>
</svelte:head>

<TaskList />
