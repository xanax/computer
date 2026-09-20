// Attach to the ALREADY-RUNNING page on :9333 (does not navigate, so it does not
// restart the mount storm) and capture a CPU profile while chats are streaming.
//
// Usage: node cpu-profile.mjs [--seconds 10] [--port 9333]
//
// Prints self-time attribution: which functions actually burn main-thread time.

import { readFileSync } from 'node:fs';

const PORT = Number(process.argv[process.argv.indexOf('--port') + 1]) || 9333;
const SECONDS = Number(process.argv[process.argv.indexOf('--seconds') + 1]) || 10;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map();
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data);
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
  static async connect(wsUrl) {
    const ws = new WebSocket(wsUrl);
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

const ev = async (expr) => (await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true })).result.value;

// ── what is the page doing right now? ───────────────────────────────────────
const before = await ev(`JSON.stringify({
  tabs: document.querySelectorAll('.persisted-tab').length,
  hidden: document.querySelectorAll('.persisted-tab-hidden').length,
  dom: document.getElementsByTagName('*').length,
  scrollBoxes: document.querySelectorAll('[data-question]').length,
  visibility: document.visibilityState,
  focus: document.hasFocus()
})`);
console.log('page state before:', before);

// Poll the app's own telemetry endpoint is not possible from here, so line up
// the profile window with wall time and correlate against ui_events afterwards.
const t0 = Date.now();
await cdp.send('Profiler.enable');
await cdp.send('Profiler.setSamplingInterval', { interval: 200 }); // 200us
await cdp.send('Profiler.start');
console.log(`profiling ${SECONDS}s ...`);
await sleep(SECONDS * 1000);
const { profile } = await cdp.send('Profiler.stop');
await cdp.send('Profiler.disable');
const t1 = Date.now();

// ── aggregate self time by function ────────────────────────────────────────
const byId = new Map();
for (const n of profile.nodes) byId.set(n.id, n);
const self = new Map();
let total = 0;
for (let i = 0; i < profile.samples.length; i++) {
  const id = profile.samples[i];
  const dt = profile.timeDeltas[i] || 0;
  total += dt;
  self.set(id, (self.get(id) || 0) + dt);
}
const rows = [];
for (const [id, us] of self) {
  const n = byId.get(id);
  if (!n) continue;
  const cf = n.callFrame;
  rows.push({
    us,
    fn: cf.functionName || '(anonymous)',
    url: (cf.url || '').replace(/^https?:\/\/127\.0\.0\.1:4200/, ''),
    line: cf.lineNumber,
  });
}
rows.sort((a, b) => b.us - a.us);

const pctOf = (us) => (total ? ((us / total) * 100).toFixed(1) : '0');
console.log(`\nprofile window ${((t1 - t0) / 1000).toFixed(1)}s, ${profile.samples.length} samples, ${(total / 1000).toFixed(0)}ms accounted`);

// separate the non-JS buckets
const buckets = { idle: 0, gc: 0, program: 0, compile: 0, js: 0 };
const agg = new Map();
for (const r of rows) {
  if (r.fn === '(idle)') { buckets.idle += r.us; continue; }
  if (r.fn === '(garbage collector)') { buckets.gc += r.us; continue; }
  if (r.fn === '(program)' || r.fn === '(root)' || r.fn === '(no name)') { buckets.program += r.us; continue; }
  if (r.fn === '(compiler)' || r.fn === '(compiled code)') { buckets.compile += r.us; continue; }
  buckets.js += r.us;
  const key = `${r.fn}  @ ${r.url}:${r.line}`;
  agg.set(key, (agg.get(key) || 0) + r.us);
}
console.log('\n=== main-thread time split ===');
for (const [k, v] of Object.entries(buckets).sort((a, b) => b[1] - a[1])) {
  if (v > 0) console.log(`  ${k.padEnd(10)} ${(v / 1000).toFixed(0).padStart(7)}ms  ${pctOf(v).padStart(5)}%`);
}

console.log('\n=== top self-time functions (JS only) ===');
const top = [...agg.entries()].sort((a, b) => b[1] - a[1]).slice(0, 30);
for (const [k, v] of top) {
  console.log(`  ${(v / 1000).toFixed(0).padStart(6)}ms  ${pctOf(v).padStart(5)}%  ${k}`);
}

// ── total (inclusive) time and callers per function ────────────────────────
const parent = new Map();
for (const n of profile.nodes) if (n.parent) parent.set(n.id, n.parent);
const label = (id) => {
  const n = byId.get(id);
  if (!n) return '?';
  const cf = n.callFrame;
  const u = (cf.url || '').replace(/^https?:\/\/127\.0\.0\.1:4200\/_app\/immutable\//, '');
  return `${cf.functionName || '(anon)'} @ ${u}:${cf.lineNumber}`;
};
// group self-time by the nearest *user* (app) ancestor
const appFrame = (id) => {
  let cur = id;
  for (let i = 0; i < 40 && cur; i++) {
    const n = byId.get(cur);
    if (!n) break;
    const u = n.callFrame.url || '';
    if (u.includes('/_app/immutable/nodes/')) return label(cur);
    cur = parent.get(cur);
  }
  return null;
};
const byFrame = new Map();
for (const [id, us] of self) {
  const f = appFrame(id);
  if (!f) continue;
  byFrame.set(f, (byFrame.get(f) || 0) + us);
}
console.log('\n=== self-time grouped by owning route/component frame ===');
for (const [k, v] of [...byFrame.entries()].sort((a, b) => b[1] - a[1]).slice(0, 15)) {
  console.log(`  ${(v / 1000).toFixed(0).padStart(6)}ms  ${pctOf(v).padStart(5)}%  ${k}`);
}

console.log('\n=== callers of the top hot leaf (tokenize / codeToHtml) ===');
for (const [leafId, us] of [...self.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6)) {
  const nl = label(leafId);
  console.log(`\n  LEAF ${nl}  (self ${(us / 1000).toFixed(0)}ms)`);
  const callers = [];
  let cur = parent.get(leafId);
  for (let i = 0; i < 6 && cur; i++) { callers.push(label(cur)); cur = parent.get(cur); }
  for (const c of callers) console.log('      <-', c);
}

// Cross-check: how many long_task rows did the app itself record in this window?
const after = await ev(`JSON.stringify({ dom: document.getElementsByTagName('*').length })`);
console.log('\npage state after:', after);
const domAfter = JSON.parse(after).dom;
const domBefore = JSON.parse(before).dom;
if (domAfter < domBefore * 0.5) {
  console.log(`\n!! PAGE RELOADED MID-PROFILE (dom ${domBefore} -> ${domAfter}) — profile invalid`);
  process.exit(2);
}
console.log(`\nWINDOW_MS ${t0} ${t1}`);
process.exit(0);
