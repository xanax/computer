/**
 * The theme, kept in a cookie.
 *
 * Server-side preferences already hold the theme, but they only arrive with
 * `GET /api/state/preferences`, so the store has to start on the default and
 * repaint when the response lands — and a browser that is offline, slow, or
 * simply logging in for the first time shows the wrong palette for as long as
 * that takes. A cookie is readable *synchronously*, while the bundle is still
 * evaluating, so the first frame is already the user's own theme (see the
 * pre-paint script in `app.html`). It is also per browser profile rather than
 * per account, which is what you want when the same user looks at cptr from a
 * phone, a laptop and an e-ink panel.
 *
 * The server copy is still written, and still read as the fallback for a
 * browser that has never set the cookie — but for a browser that has, the
 * cookie is the answer.
 *
 * The cookie deliberately outlives the session (nothing clears it on logout):
 * the palette is a property of the display in front of you, and the server's
 * copy is shared by every device you log in from, so it is the one that moves.
 * A browser profile shared by two accounts is the trade-off — the second
 * account inherits the first one's theme until it picks its own.
 */
import { isTheme, type Theme } from '$lib/utils/appearance';

export const THEME_COOKIE = 'cptr_theme';

/** One year: a theme choice is not a session. */
const MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

/** Matches the cookie's value, anywhere in `document.cookie`. */
const READ_PATTERN = new RegExp(`(?:^|;\\s*)${THEME_COOKIE}=([^;]*)`);

/**
 * Read the theme cookie. Null when it is absent, unreadable, or holds something
 * that is not a theme (an old value, or a hand-edited cookie) — the caller then
 * falls back to the server's copy.
 */
export function readThemeCookie(): Theme | null {
	if (typeof document === 'undefined') return null;
	const match = document.cookie.match(READ_PATTERN);
	if (!match) return null;
	let value: string;
	try {
		value = decodeURIComponent(match[1]);
	} catch {
		return null;
	}
	return isTheme(value) ? value : null;
}

/**
 * Write the theme cookie. `path=/` so every route sees it; `SameSite=Lax`
 * because nothing here needs it on a cross-site request; `Secure` only over
 * https, so the same code works on `http://localhost`.
 */
export function writeThemeCookie(theme: Theme): void {
	if (typeof document === 'undefined') return;
	const secure =
		typeof location !== 'undefined' && location.protocol === 'https:' ? '; Secure' : '';
	document.cookie =
		`${THEME_COOKIE}=${encodeURIComponent(theme)}` +
		`; path=/; max-age=${MAX_AGE_SECONDS}; SameSite=Lax${secure}`;
}
