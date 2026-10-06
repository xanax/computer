<script lang="ts">
	/**
	 * Reasoning-effort selector, shown next to the model selector.
	 *
	 * The levels are not hardcoded here — they come from the selected model's
	 * `effort_levels`, which the server resolves per (connection, model) from
	 * the model id and the admin `chat.models` config. So this component is
	 * provider-blind: it renders whatever levels it is handed, and renders
	 * nothing at all when the list has no real level (i.e. only "default"),
	 * which is how a model without an effort knob stays out of the way.
	 *
	 * See cptr/utils/effort.py for the resolution rules.
	 */
	import DropdownMenu from '../DropdownMenu.svelte';
	import { t } from '$lib/i18n';

	interface Props {
		/** Levels for the selected model, from ChatModel.effort_levels. */
		levels: string[];
		/** Selected level. `default` means "send nothing". */
		selected: string | null;
		preferAbove?: boolean;
		align?: 'start' | 'end';
		onchange?: (level: string | null) => void;
		onclose?: () => void;
	}
	let {
		levels,
		selected = $bindable(),
		preferAbove = true,
		align = 'end',
		onchange,
		onclose
	}: Props = $props();

	let btnEl: HTMLButtonElement | undefined = $state();
	let open = $state(false);
	let highlightedIndex = $state(0);

	/**
	 * "default" is the provider's own default and always sorts first; the rest
	 * arrive ascending from the server. Rendering is driven off this list, so a
	 * level the user somehow holds but the model no longer offers shows as
	 * unset rather than as a dangling value.
	 */
	const options = $derived(
		levels.length > 0
			? levels
			: selected
				? ['default', selected]
				: ['default']
	);

	const current = $derived(
		options.includes(selected ?? 'default') ? (selected ?? 'default') : 'default'
	);

	const label = $derived($t(`effortSelector.${current}`));

	// A model with no real level has nothing to offer: no button, no menu.
	const hasLevels = $derived(options.some((level) => level !== 'default'));

	const menuItems = $derived.by(() => {
		const items = options.map((level) => ({
			label: $t(`effortSelector.${level}`),
			tooltip: $t(`effortSelector.${level}`),
			active: level === current,
			check: true,
			onclick: () => {
				selected = level;
				onchange?.(level);
			}
		}));
		const highlighted = Math.min(highlightedIndex, Math.max(items.length - 1, 0));
		return items.map((item, index) => ({ ...item, highlighted: index === highlighted }));
	});

	function resetHighlightedIndex() {
		const index = options.indexOf(current);
		highlightedIndex = index >= 0 ? index : 0;
	}

	function moveHighlightedIndex(delta: number) {
		const total = menuItems.length;
		if (total === 0) return;
		highlightedIndex = (highlightedIndex + delta + total) % total;
	}

	export async function openSelector() {
		if (!hasLevels) return;
		open = true;
		resetHighlightedIndex();
	}

	function toggle() {
		if (open) {
			open = false;
			onclose?.();
			return;
		}
		openSelector();
	}

	function closeSelector() {
		open = false;
		onclose?.();
	}
</script>

{#if hasLevels}
	<span class="relative inline-flex {open ? 'z-[1001]' : ''}">
		<button
			bind:this={btnEl}
			class="flex items-center gap-1 px-2 py-1 rounded-lg text-[0.6875rem] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 transition-colors duration-100"
			title={$t('effortSelector.title')}
			aria-label={$t('effortSelector.title')}
			onclick={toggle}
			onkeydown={(e) => {
				if (open && e.key === 'ArrowDown') {
					e.preventDefault();
					moveHighlightedIndex(1);
				} else if (open && e.key === 'ArrowUp') {
					e.preventDefault();
					moveHighlightedIndex(-1);
				} else if (open && e.key === 'Enter') {
					e.preventDefault();
					menuItems[Math.min(highlightedIndex, menuItems.length - 1)]?.onclick();
					closeSelector();
				} else if (e.key === 'Escape' && open) {
					closeSelector();
				}
			}}
		>
			<span class="truncate max-w-[6rem]">{label}</span>
			<svg
				class="w-3 h-3 opacity-50"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2.5"
				stroke-linecap="round"
				stroke-linejoin="round"
				aria-hidden="true"
			>
				<polyline points="6 9 12 15 18 9" />
			</svg>
		</button>

		{#if open && btnEl}
			<DropdownMenu
				items={menuItems}
				anchor={btnEl}
				onclose={closeSelector}
				{preferAbove}
				forceAbove={preferAbove}
				className="w-36"
				scrollActiveIntoView
				{align}
			/>
		{/if}
	</span>
{/if}