// Count Shiki highlighter instantiations (and Oniguruma WASM engines) across a
// page load, and time the mount.
//
// CodeBlock.svelte declares `let highlighterPromise` inside the instance
// <script> while its comment calls it a "singleton". If that is right, every
// code block in the transcript builds its own highlighter: its own Oniguruma
// WASM instance, and its own compile of 30 language grammars.
//
// We can't read component state from outside, but we can count the one thing
// only a fresh engine does: instantiate the wasm module. So wrap WebAssembly
// before the app boots and count.
//
// Usage: node scripts/highlighter-count.mjs [--seconds 25]

const PORT = Number(process.argv[process.argv.indexOf('--port') + 1]) || 9333;
const SECONDS = Number(process.argv[process.argv.indexOf('--seconds') + 1]) || 22;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map();
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
if (!page) { console.error('no cptr page target'); process.exit(1); }
const cdp = await Cdp.connect(page.webSocketDebuggerUrl);
await cdp.send('Runtime.enable');
await cdp.send('Page.enable');

const ev = async (expr) => {
  const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) return { __err: r.exceptionDetails.text + ' ' + (r.exceptionDetails.exception?.description || '') };
  return r.result.value;
};

// ── install counters that survive the reload ────────────────────────────────
await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
  source: `
    window.__hl = { wasm: 0, wasmAt: [], onig: 0, t0: performance.now(), lts: [] };
    const bump = (name) => {
      try { window.__hl[name] = (window.__hl[name] || 0) + 1;
            window.__hl.wasmAt.push(Math.round(performance.now())); } catch {}
    };
    const _i = WebAssembly.instantiate.bind(WebAssembly);
    WebAssembly.instantiate = function (...a) {
      const buf = a[0];
      const bytes = buf && (buf.byteLength || (buf.buffer && buf.buffer.byteLength)) || 0;
      if (bytes > 20000) bump('wasm');   // onig.wasm is ~500KB; skip tiny modules
      return _i(...a);
    };
    if (WebAssembly.instantiateStreaming) {
      const _s = WebAssembly.instantiateStreaming.bind(WebAssembly);
      WebAssembly.instantiateStreaming = function (...a) { bump('wasm'); return _s(...a); };
    }
    try {
      new PerformanceObserver((l) => {
        for (const e of l.getEntries()) window.__hl.lts.push([Math.round(e.startTime), Math.round(e.duration)]);
      }).observe({ entryTypes: ['longtask'] });
    } catch {}
    // Mark how many code blocks have ever been attached.
    window.__hl.blocks = new Set();
    new MutationObserver(() => {
      document.querySelectorAll('pre code').forEach((c) => { if (!window.__hl.blocks.has(c)) window.__hl.blocks.add(c); });
    }).observe(document.documentElement, { childList: true, subtree: true });
    window.__hl.ready = false;
    document.addEventListener('DOMContentLoaded', () => { window.__hl.domAt = Math.round(performance.now()); });
    window.addEventListener('load', () => { window.__hl.loadAt = Math.round(performance.now()); });
  `
});

console.log('reloading page with wasm counter installed ...');
await cdp.send('Page.reload', { ignoreCache: false });
await sleep(SECONDS * 1000);

const snap = await ev(`JSON.stringify({
  wasm: window.__hl.wasm,
  wasmAt: window.__hl.wasmAt,
  blocks: window.__hl.blocks.size,
  domAt: window.__hl.domAt,
  loadAt: window.__hl.loadAt,
  nowAt: Math.round(performance.now()),
  codeBlocksNow: document.querySelectorAll('pre code').length,
  tabs: document.querySelectorAll('.persisted-tab').length,
  lts: window.__hl.lts
})`);

if (typeof snap !== 'string') {
  console.log('eval failed:', JSON.stringify(snap));
  process.exit(1);
}
const d = JSON.parse(snap);

console.log('\n─── highlighter inventory ───');
console.log(`DOMContentLoaded at ${d.domAt}ms, load at ${d.loadAt}ms, measured at ${d.nowAt}ms`);
console.log(`code blocks present now : ${d.codeBlocksNow}`);
console.log(`code blocks ever seen   : ${d.blocks}`);
console.log(`chat tabs mounted       : ${d.tabs}`);
console.log(`Oniguruma wasm engines  : ${d.wasm}   <- 1 means singleton, ~= code blocks means per-instance`);

const lts = d.lts || [];
const tot = lts.reduce((n, l) => n + l[1], 0);
console.log(`\n─── mount-phase long tasks ───`);
console.log(`n=${lts.length}  sum=${tot}ms  max=${Math.max(0, ...lts.map((l) => l[1]))}ms`);
console.log('  first 25 (start:duration):');
console.log('  ' + lts.slice(0, 25).map((l) => l[0] + ':' + l[1]).join('  '));

const at = d.wasmAt || [];
if (at.length > 1) {
  const gaps = at.slice(1).map((v, i) => v - at[i]);
  console.log(`\nwasm instantiations at: ${at.slice(0, 30).join(', ')}`);
  console.log(`gaps between them (ms): ${gaps.slice(0, 30).join(', ')}`);
}
process.exit(0);
