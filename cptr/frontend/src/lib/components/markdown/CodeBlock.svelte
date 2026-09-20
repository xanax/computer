<script lang="ts">
	import { highlightCode } from '$lib/utils/highlighter';
	import { t } from '$lib/i18n';

	/** Coalescing window for re-highlighting a block while it streams. */
	const MIN_HIGHLIGHT_GAP_MS = 90;

	interface Props {
		language: string;
		code: string;
		/** Optional diff-style callbacks for chat tool approval */
		onapply?: ((code: string) => void) | undefined;
		onreject?: (() => void) | undefined;
	}

	let { language, code, onapply, onreject }: Props = $props();

	let codeEl: HTMLElement | undefined = $state();
	let copied = $state(false);

	// Detect diff blocks
	let isDiff = $derived(language === 'diff');

	// Parse diff lines for coloring
	let diffLines = $derived.by(() => {
		if (!isDiff) return [];
		return code.split('\n').map((line) => ({
			text: line,
			type: line.startsWith('+')
				? ('add' as const)
				: line.startsWith('-')
					? ('del' as const)
					: line.startsWith('@@')
						? ('range' as const)
						: ('ctx' as const)
		}));
	});

	// Highlight non-diff code: reactive so it re-runs on prop changes (streaming).
	//
	// Highlighting a growing block costs ~27ms warm, and a streaming message can
	// change it 20 times a second, so the work is coalesced: at most one pass per
	// MIN_HIGHLIGHT_GAP_MS, with a trailing pass so the final text is never left
	// un-highlighted. Static code highlights immediately (leading edge).
	let highlightTimer: ReturnType<typeof setTimeout> | undefined;
	let lastHighlightAt = 0;

	function applyHighlight(el: HTMLElement, text: string, lang: string) {
		lastHighlightAt = performance.now();
		void highlightCode(text, lang).then((result) => {
			// The block may have moved on (streaming) or been torn down.
			if (!result || !el.isConnected || el !== codeEl) return;
			el.innerHTML = result.html;
			for (const [name, value] of result.vars) el.style.setProperty(name, value);
		});
	}

	$effect(() => {
		if (isDiff || !codeEl) return;
		const el = codeEl;
		const text = code;
		const lang = language;

		if (highlightTimer !== undefined) clearTimeout(highlightTimer);
		const wait = MIN_HIGHLIGHT_GAP_MS - (performance.now() - lastHighlightAt);
		if (wait <= 0) {
			applyHighlight(el, text, lang);
		} else {
			highlightTimer = setTimeout(() => {
				highlightTimer = undefined;
				applyHighlight(el, text, lang);
			}, wait);
		}

		return () => {
			if (highlightTimer !== undefined) {
				clearTimeout(highlightTimer);
				highlightTimer = undefined;
			}
		};
	});

	function handleCopy() {
		navigator.clipboard.writeText(code);
		copied = true;
		setTimeout(() => {
			copied = false;
		}, 2000);
	}
</script>

<div
	class="not-prose rounded-2xl overflow-hidden bg-black/[0.03] dark:bg-white/[0.03] border border-black/[0.06] dark:border-white/[0.06]"
>
	<div class="flex items-center justify-between h-[1.875rem] px-2.5">
		<span class="text-[0.6875rem] font-medium text-gray-500 lowercase">{language || 'text'}</span>
		<div class="flex items-center gap-1">
			{#if isDiff && onapply}
				<button
					class="text-[0.6875rem] px-2 py-0.5 rounded text-green-600 hover:bg-green-600/10 transition-all duration-100"
					onclick={() => onapply?.(code)}>{$t('common.apply')}</button
				>
				<button
					class="text-[0.6875rem] px-2 py-0.5 rounded text-red-600 hover:bg-red-600/10 transition-all duration-100"
					onclick={() => onreject?.()}>{$t('common.reject')}</button
				>
			{/if}
			<button
				class="text-[0.6875rem] px-2 py-0.5 rounded text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-200 dark:hover:bg-white/[0.08] transition-all duration-100"
				onclick={handleCopy}
			>
				{copied ? '✓' : $t('common.copy')}
			</button>
		</div>
	</div>

	{#if isDiff}
		<pre
			class="!m-0 !pb-3 !px-4 overflow-x-auto text-[0.8125rem] leading-normal !bg-transparent font-mono"><code
				>{#each diffLines as line}<span class="diff-line diff-{line.type}"
						>{line.text}
</span>{/each}</code
			></pre>
	{:else}
		<pre
			class="!m-0 !pb-3 !px-4 overflow-x-auto text-[0.8125rem] leading-normal !bg-transparent text-gray-800 dark:text-gray-200 font-mono"><code
				class="font-[inherit]"
				bind:this={codeEl}>{code}</code
			></pre>
	{/if}
</div>

<style>
	@reference "../../../app.css";

	/* ── Shiki dual-theme: switch via CSS variables ── */

	pre :global(span) {
		color: var(--shiki-light);
	}

	:global(.dark) pre :global(span) {
		color: var(--shiki-dark);
	}

	/* ── Diff line coloring ──────────────────────── */

	.diff-line {
		display: block;
	}

	.diff-line.diff-add {
		background: rgba(22, 163, 74, 0.1);
		color: #16a34a;
	}

	:global(.dark) .diff-line.diff-add {
		background: rgba(34, 197, 94, 0.1);
		color: #4ade80;
	}

	.diff-line.diff-del {
		background: rgba(220, 38, 38, 0.08);
		color: #dc2626;
	}

	:global(.dark) .diff-line.diff-del {
		background: rgba(248, 113, 113, 0.1);
		color: #f87171;
	}

	.diff-line.diff-range {
		color: #8b5cf6;
	}

	:global(.dark) .diff-line.diff-range {
		color: #a78bfa;
	}

	/* Monochrome: the +/- each line already carries is the signal, and a diff
	   row is a surface rather than an accent. */
	:global(.mono) .diff-line.diff-add,
	:global(.mono) .diff-line.diff-del,
	:global(.mono) .diff-line.diff-range {
		background: none;
		color: var(--app-fg);
	}
</style>
