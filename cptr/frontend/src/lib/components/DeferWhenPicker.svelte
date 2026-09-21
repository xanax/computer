<script lang="ts">
	/**
	 * When-picker for "run later": quick offsets first, then an explicit date and
	 * time, with the resolved instant echoed underneath.
	 *
	 * It writes the pick to `at` as RFC 3339 carrying this browser's offset, since
	 * the jobs API rejects a bare wall-clock time. `at` stays '' while the pick is
	 * unusable — nothing chosen, or already past — so the caller's Schedule button
	 * reads the same signal and the reason sits next to the fields.
	 */
	import { t } from '$lib/i18n';
	import {
		DAY_MS,
		HOUR_MS,
		MINUTE_MS,
		atLocalTime,
		ceilTo,
		formatWhen,
		fromInputs,
		toDateInputValue,
		toRfc3339,
		toTimeInputValue
	} from '$lib/utils/when';

	interface Props {
		/** RFC 3339 instant to run at, or '' when nothing usable is picked. */
		at?: string;
	}

	let { at = $bindable('') }: Props = $props();

	const PRESETS: { key: string; labelKey: string; at: () => number }[] = [
		{ key: '15m', labelKey: 'dashboard.deferIn15m', at: () => Date.now() + 15 * MINUTE_MS },
		{ key: '1h', labelKey: 'dashboard.deferIn1h', at: () => Date.now() + HOUR_MS },
		{ key: '3h', labelKey: 'dashboard.deferIn3h', at: () => Date.now() + 3 * HOUR_MS },
		{ key: 'tomorrow', labelKey: 'dashboard.deferTomorrow', at: () => atLocalTime(1, 9) },
		{ key: 'week', labelKey: 'dashboard.deferNextWeek', at: () => Date.now() + 7 * DAY_MS }
	];

	/**
	 * A default, not a commitment: the form opens ready to submit, five minutes
	 * out and rounded up, instead of empty with a dead button.
	 */
	const initial = ceilTo(Date.now() + 5 * MINUTE_MS);

	let dateStr = $state(toDateInputValue(initial));
	let timeStr = $state(toTimeInputValue(initial));
	let pickedMs = $state<number | null>(initial);
	let problem = $state('');
	/** Which preset produced the current pick, so an edited pick stops claiming it. */
	let preset = $state<string | null>(null);

	function applyPreset(p: (typeof PRESETS)[number]) {
		const ms = p.at();
		dateStr = toDateInputValue(ms);
		timeStr = toTimeInputValue(ms);
		preset = p.key;
	}

	function onEdit() {
		preset = null;
	}

	$effect(() => {
		const ms = fromInputs(dateStr, timeStr);
		pickedMs = ms;
		if (ms === null) {
			problem = $t('dashboard.deferNeedTime');
			at = '';
			return;
		}
		if (ms <= Date.now()) {
			problem = $t('dashboard.deferPast');
			at = '';
			return;
		}
		problem = '';
		at = toRfc3339(ms);
	});
</script>

<div class="when">
	<div class="presets" role="group" aria-label={$t('dashboard.deferWhen')}>
		{#each PRESETS as p (p.key)}
			<button
				type="button"
				class="chip"
				class:on={preset === p.key}
				aria-pressed={preset === p.key}
				onclick={() => applyPreset(p)}
			>
				{$t(p.labelKey)}
			</button>
		{/each}
	</div>

	<div class="fields">
		<span class="when-label">{$t('dashboard.deferWhen')}</span>
		<input
			type="date"
			class="when-input"
			bind:value={dateStr}
			min={toDateInputValue(Date.now())}
			aria-label={$t('dashboard.deferDate')}
			oninput={onEdit}
		/>
		<input
			type="time"
			class="when-input"
			bind:value={timeStr}
			aria-label={$t('dashboard.deferTime')}
			oninput={onEdit}
		/>
	</div>

	{#if problem}
		<span class="when-problem">{problem}</span>
	{:else if pickedMs !== null}
		<span class="when-preview">{formatWhen(pickedMs, $t)}</span>
	{/if}
</div>

<style>
	.when {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		/* Its own line inside the wrapping .defer-form row. */
		flex-basis: 100%;
	}

	.presets {
		display: flex;
		flex-wrap: wrap;
		gap: 0.3rem;
	}

	.chip {
		padding: 0.15rem 0.5rem;
		border-radius: 0.375rem;
		border: 1px solid var(--app-border);
		background: transparent;
		color: var(--app-fg);
		font-size: 0.6875rem;
		line-height: 1.5;
	}

	.chip:hover {
		border-color: var(--app-fg);
	}

	/* The chosen preset is a solid inversion: ink surface, paper text. */
	.chip.on {
		background: var(--app-fg);
		border-color: var(--app-fg);
		color: var(--app-bg);
	}

	.fields {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem;
	}

	.when-label {
		font-size: 0.6875rem;
		color: var(--app-fg-muted);
	}

	.when-input {
		padding: 0.3rem 0.4rem;
		border-radius: 0.375rem;
		border: 1px solid var(--app-border);
		background: transparent;
		color: var(--app-fg);
		font-family: inherit;
		font-size: 0.8125rem;
	}

	.when-input:focus-visible {
		outline: 1px solid var(--app-fg);
		outline-offset: 1px;
	}

	.when-preview {
		font-size: 0.6875rem;
		color: var(--app-fg-muted);
	}

	/* Why Schedule is off. Underlined like other inline notices, never a wash. */
	.when-problem {
		font-size: 0.6875rem;
		color: var(--app-fg);
		text-decoration: underline;
		text-decoration-style: dotted;
		text-underline-offset: 2px;
	}
</style>
