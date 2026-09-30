<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		onclose: () => void;
		class?: string;
		overlayClass?: string;
		/** Fill the viewport with the panel instead of floating a centred card. */
		full?: boolean;
		children: Snippet;
	}

	let {
		onclose,
		class: className = '',
		overlayClass,
		full = false,
		children
	}: Props = $props();

	// Full-bleed panels must not inherit the card's rounding/inset: the base
	// classes are built here rather than overridden, since a later `rounded-none`
	// in `class` would not reliably win over `rounded-3xl` in the stylesheet.
	const overlay = $derived(
		overlayClass ?? (full ? 'bg-black/50 items-stretch' : 'bg-black/50 items-center justify-center')
	);
	const panel = $derived(
		full
			? `w-full h-full overflow-hidden border shadow-2xl ${className}`
			: `rounded-3xl overflow-visible border shadow-2xl ${className}`
	);

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="fixed inset-0 z-[100] flex {overlay}" onmousedown={onclose} onkeydown={() => {}}>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="app-theme app-surface {panel}"
		style="background: var(--app-bg); color: var(--app-fg);"
		onmousedown={(e) => e.stopPropagation()}
		onkeydown={() => {}}
	>
		{@render children()}
	</div>
</div>
