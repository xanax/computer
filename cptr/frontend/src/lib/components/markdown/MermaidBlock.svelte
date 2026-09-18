<script lang="ts">
	import { t } from '$lib/i18n';

	let { code }: { code: string } = $props();

	let containerEl: HTMLDivElement | undefined = $state();
	let error = $state<string | null>(null);
	let rendered = $state(false);

	/**
	 * Mermaid builds its whole palette out of `themeVariables`, and derives
	 * further shades from whatever it is handed. Monochrome hands it the two
	 * palette colours and nothing else, so every node, edge, label and note
	 * lands on black or white instead of a mermaid grey.
	 */
	function monoThemeVariables() {
		const dark = document.documentElement.classList.contains('bw-dark');
		const foreground = dark ? '#ffffff' : '#000000';
		const background = dark ? '#000000' : '#ffffff';
		return {
			background,
			darkMode: dark,
			primaryColor: background,
			primaryTextColor: foreground,
			primaryBorderColor: foreground,
			secondaryColor: background,
			tertiaryColor: background,
			lineColor: foreground,
			textColor: foreground,
			mainBkg: background,
			nodeBorder: foreground,
			nodeTextColor: foreground,
			clusterBkg: background,
			clusterBorder: foreground,
			titleColor: foreground,
			edgeLabelBackground: background,
			labelBackground: background,
			labelTextColor: foreground,
			noteBkgColor: background,
			noteTextColor: foreground,
			noteBorderColor: foreground,
			actorBkg: background,
			actorBorder: foreground,
			actorTextColor: foreground,
			actorLineColor: foreground,
			signalColor: foreground,
			signalTextColor: foreground,
			labelBoxBkgColor: background,
			labelBoxBorderColor: foreground,
			loopTextColor: foreground,
			activationBkgColor: background,
			activationBorderColor: foreground,
			sequenceNumberColor: background,
			errorBkgColor: background,
			errorTextColor: foreground,
			// Pie, git and quadrant charts derive a ramp of their own.
			cScale0: foreground,
			cScale1: background,
			cScale2: foreground,
			cScale3: background,
			cScale4: foreground,
			cScale5: background,
			cScale6: foreground,
			cScale7: background
		};
	}

	$effect(() => {
		if (!containerEl || rendered) return;
		const currentCode = code;

		(async () => {
			try {
				const mermaid = (await import('mermaid')).default;
				const mono = document.documentElement.classList.contains('mono');
				mermaid.initialize({
					startOnLoad: false,
					theme: mono
						? 'base'
						: document.documentElement.classList.contains('dark')
							? 'dark'
							: 'default',
					themeVariables: mono ? monoThemeVariables() : undefined,
					securityLevel: 'strict',
					fontFamily: 'inherit'
				});

				const id = `mermaid-${Math.random().toString(36).slice(2, 9)}`;
				const { svg } = await mermaid.render(id, currentCode);

				if (containerEl) {
					containerEl.innerHTML = svg;
					rendered = true;
				}
			} catch (e: any) {
				error = e.message || $t('preview.diagramRenderError');
			}
		})();
	});
</script>

<div class="mermaid-block">
	{#if error}
		<div class="mermaid-error">
			<span class="mermaid-error-label">{$t('preview.diagramError')}</span>
			<pre class="mermaid-error-msg">{error}</pre>
			<pre class="mermaid-source">{code}</pre>
		</div>
	{:else}
		<div bind:this={containerEl} class="mermaid-container"></div>
	{/if}
</div>

<style>
	@reference "../../../app.css";

	.mermaid-block {
		margin: 0 0 0.75rem;
		border-radius: 0.5rem;
		overflow: hidden;
		border: 1px solid var(--app-border);
	}

	.mermaid-container {
		display: flex;
		justify-content: center;
		padding: 1rem;
		background: var(--app-hover);
		overflow-x: auto;
	}

	.mermaid-container :global(svg) {
		max-width: 100%;
		height: auto;
	}

	.mermaid-error {
		padding: 0.75rem 1rem;
		background: rgba(220, 38, 38, 0.04);
	}

	.mermaid-error-label {
		font-size: 0.6875rem;
		font-weight: 500;
		color: #dc2626;
	}

	.mermaid-error-msg {
		margin: 0.25rem 0 0.5rem;
		font-size: 0.75rem;
		color: #dc2626;
		white-space: pre-wrap;
		font-family: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
	}

	.mermaid-source {
		margin: 0;
		padding: 0.5rem 0.75rem;
		font-size: 0.75rem;
		background: var(--app-hover);
		border-radius: 0.25rem;
		color: var(--app-fg-muted);
		font-family: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
	}

</style>
