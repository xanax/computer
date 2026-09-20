/**
 * The app's single Shiki highlighter.
 *
 * Building a highlighter is expensive: it loads the Oniguruma engine and the
 * themes, and it used to be handed ~30 grammars up front (~1.1s of main thread,
 * measured). So it must happen once per page — not once per code block.
 *
 * Two traps this module exists to avoid:
 *
 *  1. Scope. A `let highlighterPromise` inside a component's *instance*
 *     <script> is per instance, however the comment reads. A transcript with 70
 *     code blocks built 70 highlighters. Module scope is the fix.
 *  2. Up-front grammars. `createHighlighterCore` with 30 grammars costs ~1.1s;
 *     with none it costs ~138ms, and loading one grammar into a warm highlighter
 *     afterwards costs ~8ms. So grammars are loaded on first use.
 *
 * Both numbers are from `scripts/mount-trace.mjs` / `perf-split.mjs` runs
 * recorded in notes/NOTES-ui-multi-chat-slowdown.md.
 */

import type { HighlighterCore, LanguageInput } from 'shiki/core';

export type Highlighter = HighlighterCore;
export type ThemePair = { light: string; dark: string };

/** Dual-theme pair; colors are read through --shiki-light / --shiki-dark. */
export const THEMES: ThemePair = { light: 'github-light', dark: 'github-dark' };

/** Fence label used when a language is unknown or fails to load. */
export const PLAIN_TEXT = 'text';

/**
 * Grammars we ship. Kept as explicit static imports so Vite emits one chunk per
 * grammar and only fetches the ones a transcript actually uses.
 */
const LANG_LOADERS: Record<string, () => LanguageInput> = {
	javascript: () => import('shiki/langs/javascript.mjs'),
	typescript: () => import('shiki/langs/typescript.mjs'),
	python: () => import('shiki/langs/python.mjs'),
	bash: () => import('shiki/langs/bash.mjs'),
	json: () => import('shiki/langs/json.mjs'),
	html: () => import('shiki/langs/html.mjs'),
	css: () => import('shiki/langs/css.mjs'),
	markdown: () => import('shiki/langs/markdown.mjs'),
	yaml: () => import('shiki/langs/yaml.mjs'),
	toml: () => import('shiki/langs/toml.mjs'),
	rust: () => import('shiki/langs/rust.mjs'),
	go: () => import('shiki/langs/go.mjs'),
	c: () => import('shiki/langs/c.mjs'),
	cpp: () => import('shiki/langs/cpp.mjs'),
	java: () => import('shiki/langs/java.mjs'),
	sql: () => import('shiki/langs/sql.mjs'),
	svelte: () => import('shiki/langs/svelte.mjs'),
	dockerfile: () => import('shiki/langs/dockerfile.mjs'),
	xml: () => import('shiki/langs/xml.mjs'),
	ruby: () => import('shiki/langs/ruby.mjs'),
	php: () => import('shiki/langs/php.mjs'),
	swift: () => import('shiki/langs/swift.mjs'),
	kotlin: () => import('shiki/langs/kotlin.mjs'),
	lua: () => import('shiki/langs/lua.mjs'),
	tsx: () => import('shiki/langs/tsx.mjs'),
	jsx: () => import('shiki/langs/jsx.mjs'),
	scss: () => import('shiki/langs/scss.mjs'),
	graphql: () => import('shiki/langs/graphql.mjs'),
	makefile: () => import('shiki/langs/makefile.mjs')
};

/**
 * Fence labels that are not grammar file names. Shiki resolves a grammar's own
 * aliases (bash brings sh/zsh/shell, typescript brings ts, …); this covers the
 * rest of what people actually type in a fence.
 */
const LANG_ALIASES: Record<string, string> = {
	mjs: 'javascript',
	cjs: 'javascript',
	js: 'javascript',
	py: 'python',
	python3: 'python',
	sh: 'bash',
	zsh: 'bash',
	shell: 'bash',
	shellsession: 'bash',
	console: 'bash',
	yml: 'yaml',
	md: 'markdown',
	rs: 'rust',
	golang: 'go',
	'c++': 'cpp',
	cxx: 'cpp',
	htm: 'html',
	docker: 'dockerfile',
	make: 'makefile'
};

let highlighterPromise: Promise<Highlighter> | null = null;

/**
 * The shared highlighter. Safe to call from anywhere: concurrent callers share
 * one promise, and a failed creation is not cached so the next code block can
 * retry.
 */
export function getHighlighter(): Promise<Highlighter> {
	if (!highlighterPromise) {
		highlighterPromise = (async () => {
			const [{ createHighlighterCore }, { createOnigurumaEngine }] = await Promise.all([
				import('shiki/core'),
				import('shiki/engine/oniguruma')
			]);
			const highlighter = await createHighlighterCore({
				themes: [import('shiki/themes/github-light.mjs'), import('shiki/themes/github-dark.mjs')],
				langs: [],
				engine: createOnigurumaEngine(import('shiki/wasm'))
			});
			// Small probe surface: this must stay at 1 for the page's lifetime, and
			// the loaded grammar list shows what lazy loading actually pulled in.
			// Read it from .cptr/harness or scripts/count-highlighters.mjs.
			if (typeof window !== 'undefined') {
				const w = window as unknown as {
					__cptrShiki?: { highlighters: number; hl?: Highlighter };
				};
				w.__cptrShiki = {
					highlighters: (w.__cptrShiki?.highlighters ?? 0) + 1,
					hl: highlighter
				};
			}
			return highlighter;
		})().catch((error) => {
			highlighterPromise = null;
			throw error;
		});
	}
	return highlighterPromise;
}

/**
 * Map a fence label to a grammar the highlighter can use, loading it if needed.
 * Always returns a usable name: unknown labels fall back to plain text.
 */
export async function resolveLanguage(highlighter: Highlighter, raw: string): Promise<string> {
	const name = (raw || '').trim().toLowerCase();
	const loaded = highlighter.getLoadedLanguages();
	if (loaded.includes(name)) return name;

	const canonical = LANG_ALIASES[name] ?? name;
	if (loaded.includes(canonical)) return canonical;

	const loader = LANG_LOADERS[canonical];
	if (!loader) return PLAIN_TEXT;

	try {
		await highlighter.loadLanguage(loader());
	} catch {
		return PLAIN_TEXT;
	}
	return highlighter.getLoadedLanguages().includes(canonical) ? canonical : PLAIN_TEXT;
}

export interface Highlighted {
	/** innerHTML for the caller's <code> element. */
	html: string;
	/** CSS custom properties that must sit on <code> for colors to resolve. */
	vars: Array<[string, string]>;
}

// ── result cache ────────────────────────────────────────────────────────────
// Keyed by language + source, so re-renders (tab switches, re-lexing, revisiting
// a transcript) reuse the markup instead of re-highlighting. Bounded by total
// markup size rather than entry count, since block sizes vary wildly.
const CACHE_MAX_CHARS = 2_000_000;
const resultCache = new Map<string, Highlighted | null>();
let resultCacheChars = 0;

function remember(key: string, value: Highlighted | null): void {
	const size = (value?.html.length ?? 0) + key.length;
	if (resultCacheChars + size > CACHE_MAX_CHARS) {
		for (const [oldKey, oldValue] of resultCache) {
			if (resultCacheChars + size <= CACHE_MAX_CHARS) break;
			resultCache.delete(oldKey);
			resultCacheChars -= (oldValue?.html.length ?? 0) + oldKey.length;
		}
		resultCacheChars = Math.max(0, resultCacheChars);
	}
	resultCache.set(key, value);
	resultCacheChars += size;
}

function extract(html: string): Highlighted {
	const holder = document.createElement('div');
	holder.innerHTML = html;
	const pre = holder.querySelector('pre');
	const code = holder.querySelector('code');
	const vars: Array<[string, string]> = [];
	const style = pre?.getAttribute('style') ?? '';
	for (const match of style.matchAll(/(--shiki[\w-]*)\s*:\s*([^;]+)/g)) {
		vars.push([match[1], match[2].trim()]);
	}
	return { html: code?.innerHTML ?? html, vars };
}

/**
 * Highlight `code` as `language`, returning markup for a <code> element plus the
 * theme variables that belong on it. Null means "no highlighting available" and
 * callers should leave the plain text in place.
 */
export async function highlightCode(code: string, language: string): Promise<Highlighted | null> {
	const key = `${language}\u0000${code}`;
	const cached = resultCache.get(key);
	if (cached !== undefined) return cached;

	let value: Highlighted | null = null;
	try {
		const highlighter = await getHighlighter();
		const lang = await resolveLanguage(highlighter, language);
		value = extract(
			highlighter.codeToHtml(code, { lang, themes: THEMES, defaultColor: false })
		);
	} catch {
		value = null;
	}
	remember(key, value);
	return value;
}

// ── token caching for line-level renderers (diffs) ──────────────────────────
// A diff gives one line per component, so this cache sees far more keys than the
// block cache above and needs its own, smaller bound.
export interface HighlightToken {
	content: string;
	offset: number;
	variants?: { light?: { color?: string }; dark?: { color?: string } };
}

const TOKEN_CACHE_MAX = 2000;
const tokenCache = new Map<string, HighlightToken[]>();

export function takeCachedTokens(key: string): HighlightToken[] | undefined {
	const tokens = tokenCache.get(key);
	if (tokens) {
		tokenCache.delete(key); // LRU: re-insert so it is the newest
		tokenCache.set(key, tokens);
	}
	return tokens;
}

export function cacheTokens(key: string, tokens: HighlightToken[]): void {
	tokenCache.set(key, tokens);
	while (tokenCache.size > TOKEN_CACHE_MAX) {
		const oldest = tokenCache.keys().next().value;
		if (oldest === undefined) break;
		tokenCache.delete(oldest);
	}
}
