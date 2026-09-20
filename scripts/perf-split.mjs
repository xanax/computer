// Split the page-load cost into script / style / layout using CDP Performance
// metrics. Long-task and profiler numbers tell you how long the main thread was
// blocked but not by what; this separates JS from Blink work.
//
// Usage: node scripts/perf-split.mjs [--seconds 25] [--tag before]

const PORT = Number(process.argv[process.argv.indexOf('--port') + 1]) || 9333;
const SECONDS = Number(process.argv[process.argv.indexOf('--seconds') + 1]) || 25;
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
await cdp.send('Performance.enable');
await cdp.send('Page.addScriptToEvaluateOnNewDocument', {
  source: `window.__lt = []; try { new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__lt.push([Math.round(e.startTime), Math.round(e.duration)]); }).observe({ entryTypes: ['longtask'] }); } catch {}
           window.__hlCreates = 0;`
});
const ev = async (expr) => {
  const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  return r.exceptionDetails ? null : r.result.value;
};
const metrics = async () => {
  const { metrics: m } = await cdp.send('Performance.getMetrics');
  return Object.fromEntries(m.map((x) => [x.name, x.value]));
};

await cdp.send('Page.reload');
await sleep(2000);
const m0 = await metrics();
const wall0 = Date.now();
await sleep(SECONDS * 1000);
const m1 = await metrics();
const wall = (Date.now() - wall0) / 1000;

const lts = JSON.parse((await ev('JSON.stringify(window.__lt || [])')) || '[]');
const creates = await ev('window.__hlCreates || 0');
const dom = await ev("JSON.stringify({code:document.querySelectorAll('pre code').length,tabs:document.querySelectorAll('.persisted-tab').length,nodes:document.getElementsByTagName('*').length,spans:document.getElementsByTagName('span').length})");

const d = (k) => (m1[k] - m0[k]);
console.log(`\n=== [${TAG}] steady-state ${wall.toFixed(1)}s after load ===`);
console.log(`TaskDuration (main thread busy) : ${(d('TaskDuration') * 1000).toFixed(0)}ms`);
console.log(`  ScriptDuration (JS)           : ${(d('ScriptDuration') * 1000).toFixed(0)}ms`);
console.log(`  RecalcStyleDuration           : ${(d('RecalcStyleDuration') * 1000).toFixed(0)}ms`);
console.log(`  LayoutDuration                : ${(d('LayoutDuration') * 1000).toFixed(0)}ms`);
console.log(`  unaccounted                   : ${((d('TaskDuration') - d('ScriptDuration') - d('RecalcStyleDuration') - d('LayoutDuration')) * 1000).toFixed(0)}ms`);
console.log(`long tasks since load           : n=${lts.length} sum=${lts.reduce((n, l) => n + l[1], 0)}ms max=${Math.max(0, ...lts.map((l) => l[1]))}ms`);
console.log(`highlighters built              : ${creates}`);
console.log(`DOM                             : ${dom}`);
process.exit(0);
