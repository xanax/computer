// Reload the page and audit the Shiki highlighter against a real chat transcript.
//
// What this measures, and why:
//   highlighters  — must be 1. Before lib/utils/highlighter.ts hoisted the
//                   promise to module scope, each CodeBlock built its own, and a
//                   45-tab transcript built 77 of them (~770ms of grammar
//                   registration each, ~1.1s total per creation).
//   ttfh          — time from navigation to the first block carrying Shiki
//                   tokens. The up-front 30-grammar build used to gate this.
//   settle        — time until the last block gained tokens.
//   coverage      — every block must end up either tokenised, a rendered diff, or
//                   plain text. Tokens must never eat the source: textContent is
//                   compared against the pre-highlight fallback where available.
//
// Usage: node scripts/count-highlighters.mjs [--seconds 20] [--port 9333]

const PORT = Number(process.argv[process.argv.indexOf('--port') + 1]) || 9333;
const SECONDS = Number(process.argv[process.argv.indexOf('--seconds') + 1]) || 20;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
	constructor(ws) {
		this.ws = ws;
		this.id = 0;
		this.pending = new Map();
		ws.addEventListener('message', (e) => {
			const m = JSON.parse(e.data);
			if (m.id && this.pending.has(m.id)) {
				const { resolve, reject } = this.pending.get(m.id);
				this.pending.delete(m.id);
				m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
			}
		});
	}
	send(method, params = {}) {
		const id = ++this.id;
		this.ws.send(JSON.stringify({ id, method, params }));
		return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
	}
	static async connect(u) {
		const ws = new WebSocket(u);
		await new Promise((res, rej) => {
			ws.addEventListener('open', res, { once: true });
			ws.addEventListener('error', rej, { once: true });
		});
		return new Cdp(ws);
	}
}

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page = targets.find((t) => t.type === 'page' && t.url.includes('4200'));
if (!page) {
	console.error('no cptr page target');
	process.exit(1);
}
const cdp = await Cdp.connect(page.webSocketDebuggerUrl);
await cdp.send('Runtime.enable');
await cdp.send('Page.enable');
await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
	// Idempotent on purpose: addScriptToEvaluateOnNewDocument registrations pile
	// up across runs of this script, and a second PerformanceObserver would file
	// every long task twice.
	source: `
		window.__scripts = (window.__scripts || 0) + 1;
		if (!window.__lt) {
			window.__lt = [];
			try {
				new PerformanceObserver((l) => {
					for (const e of l.getEntries()) window.__lt.push([Math.round(e.startTime), Math.round(e.duration)]);
				}).observe({ entryTypes: ['longtask'] });
			} catch {}
		}
		if (!window.__hl) {
			window.__hl = { spansAt: [], t0: performance.now() };
			try {
				let last = -1;
				const snap = () => {
					let tok = 0, total = 0;
					for (const c of document.querySelectorAll('pre code')) {
						total++;
						if (c.querySelector('span[style*="--shiki"]')) tok++;
					}
					if (tok === last) return;
					last = tok;
					window.__hl.spansAt.push([Math.round(performance.now()), total, tok]);
				};
				// observe(document) — at document-start documentElement is still null.
				new MutationObserver(snap).observe(document, { childList: true, subtree: true });
			} catch (e) {
				window.__hl.error = String(e);
			}
		}
	`
});

const ev = async (expr) => {
	const r = await cdp.send('Runtime.evaluate', {
		expression: expr,
		returnByValue: true,
		awaitPromise: true
	});
	if (r.exceptionDetails) {
		console.error('eval failed:', r.exceptionDetails.text, expr.slice(0, 80));
		return null;
	}
	return r.result.value;
};

console.log(`reloading, sampling for ${SECONDS}s ...`);
await cdp.send('Page.reload');
await sleep(SECONDS * 1000);

const raw = await ev(`JSON.stringify((() => {
	const blocks = [...document.querySelectorAll('pre code')];
	const shiki = blocks.filter((b) => b.querySelector('span[style*="--shiki"]'));
	const diff = blocks.filter((b) => b.querySelector('.diff-line'));
	const vars = blocks.filter((b) => (b.getAttribute('style') || '').includes('--shiki-'));
	const langAttrs = [...new Set(blocks.map((b) => b.className))];
	return {
		highlighters: window.__cptrShiki ? window.__cptrShiki.highlighters : (window.__hlCreates || 0),
		langs: window.__cptrShiki && window.__cptrShiki.hl ? window.__cptrShiki.hl.getLoadedLanguages() : [],
		total: blocks.length,
		shiki: shiki.length,
		diff: diff.length,
		plain: blocks.length - shiki.length - diff.length,
		varsOnCode: vars.length,
		leakedTags: blocks.filter((b) => b.textContent.includes('<span')).length,
		emptyBlocks: blocks.filter((b) => b.textContent.trim() === '').length,
		probe: window.__hl || null,
		scripts: window.__scripts || 0,
		tabs: document.querySelectorAll('.persisted-tab').length,
		lt: window.__lt || []
	};
})())`);

const d = JSON.parse(raw);
const lt = d.lt;
const probe = d.probe || { spansAt: [], t0: 0 };
const first = probe.spansAt.find(([, , tok]) => tok > 0);
const last = probe.spansAt[probe.spansAt.length - 1];

console.log(`\nShiki highlighters built         : ${d.highlighters}`);
console.log(`grammars loaded lazily           : ${d.langs.length ? d.langs.join(', ') : '(none)'}`);
console.log(`chat tabs mounted                : ${d.tabs}`);
console.log('\ncode blocks in DOM               : ' + d.total);
console.log('  tokenised (shiki spans)        : ' + d.shiki);
console.log('  diff-rendered                  : ' + d.diff);
console.log('  plain text fallback            : ' + d.plain);
console.log('  theme vars set on <code>       : ' + d.varsOnCode);
console.log('  leaked <span> as text          : ' + d.leakedTags);
console.log('  empty blocks                   : ' + d.emptyBlocks);

console.log('\nfirst tokens at                  : ' + (first ? first[0] + 'ms' : 'never'));
console.log('coverage settled at              : ' + (last ? last[0] + 'ms (blocks=' + last[1] + ', tokenised=' + last[2] + ')' : 'never'));
console.log('timeline (ms,total,tokenised)    : ' + probe.spansAt.slice(0, 12).map((s) => s.join(':')).join(' '));

console.log(`\ninjected probe scripts per load   : ${d.scripts} (must be 1; more means double-counted long tasks)`);
console.log(
	`long tasks: n=${lt.length} sum=${lt.reduce((n, l) => n + l[1], 0)}ms max=${Math.max(0, ...lt.map((l) => l[1]))}ms`
);
console.log('  first 20: ' + lt.slice(0, 20).map((l) => l[0] + ':' + l[1]).join(' '));
process.exit(0);
