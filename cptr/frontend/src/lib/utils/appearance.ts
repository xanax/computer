import { setTextScale } from '$lib/utils/text-scale';

export type Theme = 'dark' | 'light' | 'system';

export type ThemeColors = {
	background?: string;
	foreground?: string;
	/** Secondary (non-active) text. When unset it is derived from the foreground. */
	muted?: string;
};

export type ResolvedThemeConfig = {
	background: string;
	foreground: string;
	/** Explicit secondary text colour, or null when it should be derived. */
	muted: string | null;
	uiFont: string;
};

export type ThemeConfig = {
	light?: ThemeColors;
	dark?: ThemeColors;
	uiFont?: string;
};

export type AppearancePreferences = {
	theme?: Theme;
	themeConfig?: ThemeConfig | null;
	textScale?: number | null;
	borderContrast?: number | null;
	highContrastBorders?: boolean;
	terminalFontSize?: number | null;
};

type ResolvedTheme = 'dark' | 'light';

const DEFAULT_UI_FONT =
	"'Inter', -apple-system, BlinkMacSystemFont, ui-sans-serif, system-ui, sans-serif";
export const DEFAULT_BORDER_CONTRAST = 1.5;
export const DEFAULT_DIVIDER_CONTRAST = 0.875;
export const MAX_BORDER_CONTRAST = 16;

/**
 * Terminal (xterm.js) constants. The font size is a pixel value handed
 * straight to xterm, so it does not follow --app-text-scale; it is stored
 * as its own preference instead.
 */
export const DEFAULT_TERMINAL_FONT_SIZE = 13;
export const MIN_TERMINAL_FONT_SIZE = 8;
export const MAX_TERMINAL_FONT_SIZE = 24;
export const TERMINAL_LINE_HEIGHT = 1.3;

export function normalizeTerminalFontSize(value: unknown): number | null {
	if (value === null || value === undefined || value === '') return null;
	const size = Number(value);
	if (!Number.isFinite(size)) return null;
	return Math.max(MIN_TERMINAL_FONT_SIZE, Math.min(MAX_TERMINAL_FONT_SIZE, Math.round(size)));
}

export function normalizeBorderContrast(value: unknown): number | null {
	if (value === null || value === undefined || value === '') return null;
	const contrast = Number(value);
	if (!Number.isFinite(contrast)) return null;
	return Math.max(
		DEFAULT_BORDER_CONTRAST,
		Math.min(MAX_BORDER_CONTRAST, Number(contrast.toFixed(1)))
	);
}

export function resolveThemeMode(theme: Theme): ResolvedTheme {
	if (theme === 'system' && typeof window !== 'undefined') {
		return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
	}
	return theme === 'light' ? 'light' : 'dark';
}

export function normalizeHexColor(value: unknown): string | undefined {
	if (typeof value !== 'string') return undefined;
	const color = value.trim();
	const short = /^#([0-9a-f]{3})$/i.exec(color);
	if (short) {
		return `#${short[1]
			.split('')
			.map((char) => char + char)
			.join('')}`.toLowerCase();
	}
	if (/^#[0-9a-f]{6}$/i.test(color)) return color.toLowerCase();
	return undefined;
}

/**
 * Perceptual colour mixing, mirroring CSS `color-mix(in oklab, a weight, b)`.
 * The app itself mixes in CSS; this exists so Settings can preview a derived
 * colour (e.g. the default secondary text) without touching the DOM.
 */
type Oklab = [number, number, number];

function srgbToLinear(value: number): number {
	return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function linearToSrgb(value: number): number {
	const clamped = Math.min(1, Math.max(0, value));
	return clamped <= 0.0031308 ? clamped * 12.92 : 1.055 * clamped ** (1 / 2.4) - 0.055;
}

function hexToOklab(hex: string): Oklab {
	const value = hex.replace('#', '');
	const [r, g, b] = [0, 2, 4].map((offset) =>
		srgbToLinear(Number.parseInt(value.slice(offset, offset + 2), 16) / 255)
	);
	const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
	const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
	const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
	return [
		0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
		1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
		0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
	];
}

function oklabToHex(lab: Oklab): string {
	const [L, a, b] = lab;
	const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
	const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
	const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
	return `#${[
		4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
		-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
		-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s
	]
		.map((channel) =>
			Math.round(linearToSrgb(channel) * 255)
				.toString(16)
				.padStart(2, '0')
		)
		.join('')}`;
}

function mixOklab(hexA: string, hexB: string, weightA: number): string {
	const a = hexToOklab(hexA);
	const b = hexToOklab(hexB);
	const at = (index: number) => a[index] * weightA + b[index] * (1 - weightA);
	return oklabToHex([at(0), at(1), at(2)]);
}

/** Weights mirror the --app-fg-muted / --app-fg-subtle rules in app.css. */
export const MUTED_TEXT_WEIGHT = 0.62;
export const SUBTLE_FROM_MUTED_WEIGHT = 0.72;

/** Default secondary text colour: the foreground blended towards the background. */
export function deriveMutedTextColor(colors: { background: string; foreground: string }): string {
	return mixOklab(colors.foreground, colors.background, MUTED_TEXT_WEIGHT);
}

function sanitizeThemeColors(value: unknown): ThemeColors | null {
	if (!value || typeof value !== 'object') return null;
	const raw = value as Record<string, unknown>;
	const next: ThemeColors = {};
	const background = normalizeHexColor(raw.background);
	const foreground = normalizeHexColor(raw.foreground);
	const muted = normalizeHexColor(raw.muted);
	if (background) next.background = background;
	if (foreground) next.foreground = foreground;
	if (muted) next.muted = muted;
	return Object.keys(next).length ? next : null;
}

export function sanitizeThemeConfig(value: unknown): ThemeConfig | null {
	if (!value || typeof value !== 'object') return null;
	const raw = value as Record<string, unknown>;
	const next: ThemeConfig = {};
	const light = sanitizeThemeColors(raw.light);
	const dark = sanitizeThemeColors(raw.dark);
	const legacy = sanitizeThemeColors(raw);
	if (light) next.light = light;
	if (dark) next.dark = dark;
	if (legacy && !light && !dark) {
		next.light = legacy;
		next.dark = legacy;
	}
	if (typeof raw.uiFont === 'string' && raw.uiFont.trim()) {
		next.uiFont = raw.uiFont.trim().slice(0, 240);
	}
	return Object.keys(next).length ? next : null;
}

export function defaultThemeConfig(theme: Theme): Omit<ResolvedThemeConfig, 'muted'> {
	const resolved = resolveThemeMode(theme);
	return {
		background: resolved === 'dark' ? '#0a0a0a' : '#ffffff',
		foreground: resolved === 'dark' ? '#d4d4d4' : '#525252',
		uiFont: DEFAULT_UI_FONT
	};
}

export function resolveThemeConfig(theme: Theme, config: ThemeConfig | null): ResolvedThemeConfig {
	const resolved = resolveThemeMode(theme);
	return {
		...defaultThemeConfig(theme),
		...(config?.[resolved] ?? {}),
		muted: config?.[resolved]?.muted ?? null,
		uiFont: config?.uiFont ?? DEFAULT_UI_FONT
	};
}

function setVar(name: string, value: string) {
	document.documentElement.style.setProperty(name, value);
}

export function applyAppearance(
	theme: Theme,
	config: ThemeConfig | null,
	textScale: number | null,
	borderContrast: number | null = null
) {
	if (typeof document === 'undefined') return;

	const resolved = resolveThemeMode(theme);
	const merged = resolveThemeConfig(theme, config);
	const borderMix = normalizeBorderContrast(borderContrast) ?? DEFAULT_BORDER_CONTRAST;
	const dividerMix =
		borderMix === DEFAULT_BORDER_CONTRAST
			? DEFAULT_DIVIDER_CONTRAST
			: Number(((borderMix * 2) / 3).toFixed(3));

	document.documentElement.classList.toggle('dark', resolved === 'dark');
	document.documentElement.style.colorScheme = resolved;

	setVar('--app-bg', merged.background);
	setVar('--app-fg', merged.foreground);
	if (merged.muted) {
		setVar('--app-fg-muted', merged.muted);
		setVar('--app-fg-subtle', mixOklab(merged.muted, merged.background, SUBTLE_FROM_MUTED_WEIGHT));
	} else {
		// Fall back to the colour-mix defaults declared in app.css.
		document.documentElement.style.removeProperty('--app-fg-muted');
		document.documentElement.style.removeProperty('--app-fg-subtle');
	}
	setVar('--app-border', `color-mix(in oklab, var(--app-fg) ${borderMix}%, transparent)`);
	setVar('--app-divider', `color-mix(in oklab, var(--app-fg) ${dividerMix}%, transparent)`);
	setVar('--app-ui-font', merged.uiFont);
	setVar('--font-sans', merged.uiFont);

	setTextScale(textScale ?? 1);

	const meta = document.querySelector('meta[name="theme-color"]');
	if (meta) meta.setAttribute('content', merged.background);
}
