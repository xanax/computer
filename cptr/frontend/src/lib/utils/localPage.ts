/**
 * Recognising pages served on this machine.
 *
 * A loopback URL only resolves in the browser that shares a network namespace
 * with whatever started the server. cptr's own Browser tab does not depend on
 * that: the server fetches the page and hands it to the tab through
 * `/api/browser/frame/...`, so `localhost:5173` works even when cptr runs inside
 * WSL, a container, or on a remote box the user's browser cannot route to.
 *
 * Markdown therefore sends local links to that tab instead of a new browser
 * window — see InlineRenderer.svelte (clicks) and MarkdownRenderer.svelte (which
 * linkifies bare `localhost:5173` references, since GFM only autolinks URLs that
 * carry a scheme).
 */

/** Hosts that mean "the machine cptr runs on", matching the server's own list. */
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]']);

const SCHEME_RE = /^[a-z][a-z0-9+.-]*:\/\//i;
const BARE_PORT_RE = /^\d{1,5}$/;
/** A loopback reference as a chat writes it: `localhost:5173`, `127.0.0.1:8000/api`. */
const BARE_LOCAL_SOURCE = String.raw`(?:https?:\/\/)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0):\d{1,5}(?:[/?#][^\s<>"'\x60)\]]*)?`;

export interface LocalPage {
	/** Normalised absolute URL, with a scheme and a trailing slash on a bare host. */
	url: string;
	/** Short tab label: `host:port`. */
	label: string;
}

/** Whether an href names cptr's own server, rather than a page to show inside it. */
function isOwnApp(parsed: URL): boolean {
	if (typeof window === 'undefined' || !window.location) return false;
	if (parsed.host === window.location.host) return true;
	// localhost / 127.0.0.1 / ::1 all name this machine, so the same port is us too.
	return parsed.port === window.location.port;
}

/**
 * Parse `href` as a page on this machine, or return null when it is not one
 * (another host, a non-http scheme, unparseable input, or cptr itself).
 */
export function localPage(href: string): LocalPage | null {
	const value = (href || '').trim();
	if (!value) return null;
	let raw = value;
	if (BARE_PORT_RE.test(value)) raw = `http://localhost:${value}/`;
	else if (!SCHEME_RE.test(value)) raw = `http://${value}`;
	let parsed: URL;
	try {
		parsed = new URL(raw);
	} catch {
		return null;
	}
	if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
	const host = parsed.hostname.toLowerCase();
	if (!LOCAL_HOSTS.has(host)) return null;
	if (isOwnApp(parsed)) return null;
	return { url: parsed.href, label: parsed.port ? `${host}:${parsed.port}` : host };
}

/**
 * The earliest loopback reference in `text` (optionally from `from`), as written.
 *
 * Scan positions are skipped when the reference is glued to a word or to a path
 * we did not consume, so `atlocalhost:5173` and the tail of
 * `http://localhost:5173` are not treated as separate links.
 */
export function findBareLocalPage(
	text: string,
	from = 0
): { index: number; raw: string; page: LocalPage } | null {
	if (!text) return null;
	const re = new RegExp(BARE_LOCAL_SOURCE, 'gi');
	re.lastIndex = from;
	let match: RegExpExecArray | null;
	while ((match = re.exec(text))) {
		const before = match.index === 0 ? '' : text[match.index - 1];
		if (before && /[\w@/:.-]/.test(before)) continue;
		// Sentence punctuation glues itself to the reference: strip the tail.
		const raw = match[0].replace(/[.,;:!?]+$/, '');
		const page = localPage(raw);
		if (page) return { index: match.index, raw, page };
	}
	return null;
}
