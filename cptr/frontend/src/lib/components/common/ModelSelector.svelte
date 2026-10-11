<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { chatModels } from '$lib/stores/chat';
	import { pinnedModels, togglePinnedModel } from '$lib/stores/pinnedModels';
	import DropdownMenu from '../DropdownMenu.svelte';
	import { t } from '$lib/i18n';

	interface Props {
		selectedModel: string | null;
		preferAbove?: boolean;
		align?: 'start' | 'end';
		nullable?: boolean;
		nullLabel?: string;
		onchange?: (model: string | null) => void;
		onclose?: () => void;
	}

	/** One row of the menu: a model, or a divider between the two groups. */
	interface MenuItem {
		label: string;
		tooltip?: string;
		active?: boolean;
		check?: boolean;
		divider?: boolean;
		highlighted?: boolean;
		onclick: () => void;
		actionIcon?: string;
		actionLabel?: string;
		actionActive?: boolean;
		actionOnclick?: (anchor: HTMLElement) => void;
	}

	let {
		selectedModel = $bindable(),
		preferAbove = true,
		align = 'end',
		nullable = false,
		nullLabel = 'Current model',
		onchange,
		onclose
	}: Props = $props();

	let btnEl: HTMLButtonElement | undefined = $state();
	let searchInputEl: HTMLInputElement | undefined = $state();
	let open = $state(false);
	let search = $state('');
	let highlightedIndex = $state(0);
	let isSmallViewport = $state(false);

	const selectorMaxHeight = $derived(isSmallViewport ? '7.5rem' : '15rem');

	/**
	 * The list as shown: pinned models first, then the rest, each group keeping
	 * the order the server sent. Only the pinned set moves, so a model you
	 * scrolled to yesterday is still where you left it.
	 */
	const visibleModels = $derived.by(() => {
		const query = search.trim().toLowerCase();
		const matches = query
			? $chatModels.filter((model) => model.name.toLowerCase().includes(query))
			: $chatModels;
		const pinned = new Set($pinnedModels);
		const isPinned = (model: { id: string }) => pinned.has(model.id);
		return [
			...matches.filter(isPinned).map((model) => ({ model, pinned: true })),
			...matches.filter((model) => !isPinned(model)).map((model) => ({ model, pinned: false }))
		];
	});

	/** A divider only earns its space when both groups are actually present. */
	const showPinDivider = $derived(
		visibleModels.some((entry) => entry.pinned) && visibleModels.some((entry) => !entry.pinned)
	);

	const menuItems = $derived.by(() => {
		const items: MenuItem[] = [];

		if (nullable) {
			items.push({
				label: nullLabel,
				tooltip: nullLabel,
				active: selectedModel === null || selectedModel === '',
				check: true,
				onclick: () => {
					selectedModel = null;
					onchange?.(null);
				}
			});
		}

		let dividerAdded = false;
		for (const entry of visibleModels) {
			if (showPinDivider && !entry.pinned && !dividerAdded) {
				items.push({ label: '', divider: true, onclick: () => {} });
				dividerAdded = true;
			}
			items.push({
				label: entry.model.name,
				tooltip: entry.model.name,
				active: entry.model.id === selectedModel,
				check: true,
				onclick: () => {
					selectedModel = entry.model.id;
					onchange?.(entry.model.id);
				},
				// Pinning is the one thing the row's own click cannot do, so it
				// gets the trailing button. Toggling only reorders the list and
				// leaves the menu open, so several can be pinned in one go.
				actionIcon: entry.pinned ? 'pin-filled' : 'pin',
				actionLabel: entry.pinned ? $t('modelSelector.unpin') : $t('modelSelector.pin'),
				actionActive: entry.pinned,
				actionOnclick: () => togglePinnedModel(entry.model.id)
			});
		}

		const highlighted = Math.min(highlightedIndex, Math.max(items.length - 1, 0));
		return items.map((item, index) => ({ ...item, highlighted: index === highlighted }));
	});

	function updateViewportSize() {
		isSmallViewport = (window.visualViewport?.width ?? window.innerWidth) < 640;
	}

	onMount(() => {
		updateViewportSize();
		window.addEventListener('resize', updateViewportSize);
		window.visualViewport?.addEventListener('resize', updateViewportSize);
		return () => {
			window.removeEventListener('resize', updateViewportSize);
			window.visualViewport?.removeEventListener('resize', updateViewportSize);
		};
	});

	async function focusSearchInput() {
		await tick();
		await tick();
		searchInputEl?.focus();
		searchInputEl?.select();
	}

	/** Index of the next pickable row, stepping `delta` rows from `from`. */
	function stepSelectableIndex(from: number, delta: number): number {
		const total = menuItems.length;
		if (total === 0) return -1;
		let index = from;
		for (let step = 0; step < total; step++) {
			index = (index + delta + total) % total;
			if (!menuItems[index]?.divider) return index;
		}
		return -1;
	}

	function resetHighlightedIndex() {
		// Start on what is selected, so Enter means "keep this" and the arrow
		// keys move from the model the user is actually on.
		const activeIndex = menuItems.findIndex((item) => item.active && !item.divider);
		highlightedIndex = activeIndex >= 0 ? activeIndex : Math.max(stepSelectableIndex(-1, 1), 0);
	}

	function moveHighlightedIndex(delta: number) {
		const next = stepSelectableIndex(highlightedIndex, delta);
		if (next >= 0) highlightedIndex = next;
	}

	export async function openSelector() {
		if ($chatModels.length === 0 && !nullable) return;
		open = true;
		search = '';
		resetHighlightedIndex();
		await focusSearchInput();
	}

	async function toggle() {
		if (open) {
			open = false;
			onclose?.();
			return;
		}
		await openSelector();
	}

	function closeSelector() {
		open = false;
		onclose?.();
	}
</script>

<span class="relative inline-flex {open ? 'z-[1001]' : ''}">
	<button
		bind:this={btnEl}
		class="flex items-center gap-1 px-2 py-1 rounded-lg text-[0.6875rem] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 transition-colors duration-100"
		onclick={toggle}
	>
		<span class="truncate max-w-[10rem]"
			>{selectedModel === null || selectedModel === ''
				? nullLabel
				: $chatModels.length === 0
					? $t('modelSelector.noModels')
					: $chatModels.find((m) => m.id === selectedModel)?.name ||
						$t('modelSelector.selectModel')}</span
		>
		{#if $chatModels.length > 0 || nullable}
			<svg
				class="w-3 h-3 opacity-50"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2.5"
				stroke-linecap="round"
				stroke-linejoin="round"
			>
				<polyline points="6 9 12 15 18 9" />
			</svg>
		{/if}
	</button>

	{#if open && btnEl && ($chatModels.length > 0 || nullable)}
		<DropdownMenu
			items={menuItems}
			anchor={btnEl}
			onclose={closeSelector}
			{preferAbove}
			forceAbove={preferAbove}
			maxHeight={selectorMaxHeight}
			className="w-52"
			scrollActiveIntoView
			scrollActiveBlock="center"
			{align}
		>
			{#snippet header()}
				<div class="flex items-center gap-1.5 h-6 px-2 mt-0.5">
					<svg
						class="w-3 h-3 shrink-0 text-gray-300 dark:text-gray-600"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
					</svg>
					<input
						bind:this={searchInputEl}
						value={search}
						placeholder={$t('modelSelector.search')}
						class="w-full bg-transparent text-[0.6875rem] text-gray-500 dark:text-gray-400 placeholder:text-gray-300 dark:placeholder:text-gray-600 outline-none"
						oninput={(e) => {
							search = e.currentTarget.value;
							resetHighlightedIndex();
						}}
						onkeydown={(e) => {
							if (e.key === 'Escape') {
								closeSelector();
							} else if (e.key === 'ArrowDown') {
								e.preventDefault();
								moveHighlightedIndex(1);
							} else if (e.key === 'ArrowUp') {
								e.preventDefault();
								moveHighlightedIndex(-1);
							} else if (e.key === 'Enter') {
								e.preventDefault();
								const item = menuItems[Math.min(highlightedIndex, menuItems.length - 1)];
								if (item && !item.divider) item.onclick();
								closeSelector();
							}
						}}
					/>
				</div>
			{/snippet}
			{#snippet empty()}
				<div class="px-3 py-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-500 text-center">
					{$t('modelSelector.noMatches')}
				</div>
			{/snippet}
		</DropdownMenu>
	{/if}
</span>
