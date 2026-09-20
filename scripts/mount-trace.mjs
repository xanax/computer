// Trace the page-load window and report where main-thread time actually goes.
//
// The V8 CPU profiler only samples JS; a chat page with 45 mounted tabs spends
// most of its mount budget outside JS (style, layout, paint, GC). This records
// a devtools.timeline trace, computes *self* time for each event, and prints the
// top buckets plus the top JS functions with real url attribution.
//
// Usage: node scripts/mount-trace.mjs [--seconds 20] [--tag before] [--top 25]

import { writeFileSync } from 'node:fs';

const PORT = Number(process.argv[process.argv.indexOf('--port') + 1]) || 9333;
const SECONDS = Number(process.argv[process.argv.indexOf('--seconds') + 1]) || 20;
const TAG = process.argv.includes('--tag') ? process.argv[process.argv.indexOf('--tag') + 1] : '';
const TOP = Number(process.argv[process.argv.indexOf('--top') + 1]) || 25;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = [];
    ws.addEventListener('message', (e) => {
      const m = JSON.parse(e.data);
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      } else if (m.method) for (const h of this.handlers) h(m.method, m.params);
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  on(f) { this.handlers.push(f); }
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

let stream = null;
cdp.on(async (m, p) => {
  if (m === 'Tracing.dataCollected' && stream) stream.push(...p.value);
  if (m === 'Tracing.tracingComplete') stream = stream || [];
  if (m === 'IO.read' && p.data) (globalThis.__io = globalThis.__io || []).push(p.data);
});

console.log(`[${TAG}] tracing reload for ${SECONDS}s ...`);
await cdp.send('Tracing.start', {
  categories: 'devtools.timeline,disabled-by-default-devtools.timeline,v8,blink.user_timing',
  options: 'sampling-frequency=1000',
  transferMode: 'ReportEvents'
});
stream = [];
await cdp.send('Page.reload');
await sleep(SECONDS * 1000);
const done = new Promise((r) => cdp.on((m) => m === 'Tracing.tracingComplete' && r()));
await cdp.send('Tracing.end');
await Promise.race([done, sleep(15000)]);
const events = stream;
console.log(`collected ${events.length} trace events`);

// ── self time per (name) across the main thread(s) ──────────────────────────
const byThread = new Map();
for (const e of events) {
  if (e.ph !== 'X' || !e.dur) continue;
  const key = `${e.pid}:${e.tid}`;
  if (!byThread.has(key)) byThread.set(key, []);
  byThread.get(key).push(e);
}
// main renderer thread = the one with the most total dur
let mainKey = null, best = -1;
for (const [k, list] of byThread) {
  const sum = list.reduce((n, e) => n + e.dur, 0);
  if (sum > best) { best = sum; mainKey = k; }
}
const main = byThread.get(mainKey);
main.sort((a, b) => a.ts - b.ts || b.dur - a.dur);
const stack = []; const selfByName = new Map(); const selfByFn = new Map();
for (const e of main) {
  while (stack.length && e.ts >= stack[stack.length - 1].ts + stack[stack.length - 1].dur) {
    const done_ = stack.pop();
    const self = done_.dur - (done_.childSum || 0);
    selfByName.set(done_.name, (selfByName.get(done_.name) || 0) + self);
    if (done_.name === 'FunctionCall') {
      const fn = done_.args?.data?.functionName || '(anonymous)';
      const url = (done_.args?.data?.url || done_.args?.data?.scriptName || '').replace(/^.*\/immutable\//, '');
      const k = `${fn} @ ${url}`;
      selfByFn.set(k, (selfByFn.get(k) || 0) + self);
    }
  }
  if (stack.length) {
    const parent = stack[stack.length - 1];
    // only nest when fully contained
    if (e.ts + e.dur <= parent.ts + parent.dur) parent.childSum = (parent.childSum || 0) + e.dur;
  }
  stack.push(e);
}
while (stack.length) {
  const done_ = stack.pop();
  const self = done_.dur - (done_.childSum || 0);
  selfByName.set(done_.name, (selfByName.get(done_.name) || 0) + self);
  if (done_.name === 'FunctionCall') {
    const fn = done_.args?.data?.functionName || '(anonymous)';
    const url = (done_.args?.data?.url || done_.args?.data?.scriptName || '').replace(/^.*\/immutable\//, '');
    const k = `${fn} @ ${url}`;
    selfByFn.set(k, (selfByFn.get(k) || 0) + self);
  }
}

const ms = (v) => `${(v / 1000).toFixed(0)}ms`;
console.log(`\n=== [${TAG}] main thread ${mainKey}, ${SECONDS}s window ===`);
console.log('top self time by event name:');
for (const [k, v] of [...selfByName.entries()].sort((a, b) => b[1] - a[1]).slice(0, TOP)) {
  console.log(`  ${ms(v).padStart(9)}  ${k}`);
}
console.log('\ntop self time inside JS (FunctionCall):');
for (const [k, v] of [...selfByFn.entries()].sort((a, b) => b[1] - a[1]).slice(0, TOP)) {
  if (v < 20000) continue;
  console.log(`  ${ms(v).padStart(9)}  ${k}`);
}

const out = `/tmp/mount-trace-${TAG || 'run'}.json`;
writeFileSync(out, JSON.stringify(events));
console.log(`\nraw trace -> ${out}`);
process.exit(0);
