// Profile the page-load (mount) window: reload, then record V8 self-time.
//
// Baseline/A-B harness for the code-block rendering work. Prints total JS
// self-time, the long-task budget, and the functions that own the time, so a
// change to the highlight path can be judged on numbers rather than vibes.
//
// Usage: node scripts/mount-profile.mjs [--seconds 20] [--tag before]

const PORT = Number(process.argv[process.argv.indexOf('--port') + 1]) || 9333;
const SECONDS = Number(process.argv[process.argv.indexOf('--seconds') + 1]) || 20;
const TAG = process.argv.includes('--tag') ? process.argv[process.argv.indexOf('--tag') + 1] : '';
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
await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
  source: `window.__lt = []; try { new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__lt.push([Math.round(e.startTime), Math.round(e.duration)]); }).observe({ entryTypes: ['longtask'] }); } catch {}
           window.__hlCreates = 0;`
});
const ev = async (expr) => {
  const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  return r.exceptionDetails ? null : r.result.value;
};

await cdp.send('Profiler.enable');
await cdp.send('Profiler.setSamplingInterval', { interval: 250 });
await cdp.send('Profiler.start');
console.log(`[${TAG}] reloading under profiler for ${SECONDS}s ...`);
const wall0 = Date.now();
await cdp.send('Page.reload');
await sleep(SECONDS * 1000);
const { profile } = await cdp.send('Profiler.stop');
await cdp.send('Profiler.disable');
const wallMs = Date.now() - wall0;

const lts = JSON.parse((await ev('JSON.stringify(window.__lt || [])')) || '[]');
const creates = await ev('window.__hlCreates || 0');
const codeBlocks = await ev("document.querySelectorAll('pre code').length");

// ── aggregate self time ─────────────────────────────────────────────────────
const byId = new Map(profile.nodes.map((n) => [n.id, n]));
const self = new Map();
for (let i = 0; i < profile.samples.length; i++) {
  self.set(profile.samples[i], (self.get(profile.samples[i]) || 0) + (profile.timeDeltas[i] || 0));
}
const bucket = (n) => {
  const cf = n.callFrame;
  const u = cf.url || '';
  if (cf.functionName === '(idle)') return '(idle)';
  if (cf.functionName === '(program)') return '(program)';
  if (cf.functionName === '(garbage collector)') return '(garbage collector)';
  const chunk = (u.match(/chunks\/([^/]+)\.js/) || u.match(/nodes\/([^/]+)\.js/) || [])[1] || u.replace(/^.*\//, '') || '(inline)';
  return `${cf.functionName || '(anon)'} @ ${chunk}`;
};
const agg = new Map();
let total = 0;
for (const [id, us] of self) {
  const n = byId.get(id); if (!n) continue;
  const ms = us / 1000; total += ms;
  const k = bucket(n);
  agg.set(k, (agg.get(k) || 0) + ms);
}
console.log(`\n=== [${TAG}] mount window ${(wallMs / 1000).toFixed(1)}s ===`);
console.log(`JS self-time total : ${Math.round(total)}ms`);
console.log(`long tasks         : n=${lts.length} sum=${lts.reduce((n, l) => n + l[1], 0)}ms max=${Math.max(0, ...lts.map((l) => l[1]))}ms`);
console.log(`highlighters built : ${creates}   code blocks: ${codeBlocks}`);
console.log('\ntop self-time:');
for (const [k, v] of [...agg.entries()].sort((a, b) => b[1] - a[1]).slice(0, 22)) {
  const pct = ((v / total) * 100).toFixed(1).padStart(5);
  console.log(`  ${Math.round(v).toString().padStart(7)}ms  ${pct}%  ${k}`);
}
process.exit(0);
