// Correlate incoming socket.io frames with main-thread JS cost.
//
// The ui_events table says long tasks pile up while chats work but almost none
// are attributed. This script skips the app's attribution entirely: it watches
// the raw WebSocket frames via CDP Network events, and samples the V8 profiler
// at the same time, so we can ask "how much JS did we burn in the second after
// this frame type arrived?".
//
// Usage: node scripts/stream-correlate.mjs [--seconds 20] [--port 9333]

const PORT = Number(process.argv[process.argv.indexOf('--port') + 1]) || 9333;
const SECONDS = Number(process.argv[process.argv.indexOf('--seconds') + 1]) || 16;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = [];
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
      } else if (m.method) {
        for (const h of this.handlers) h(m.method, m.params);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  on(fn) { this.handlers.push(fn); }
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

// ── 1. watch raw socket frames ──────────────────────────────────────────────
// Timestamps are in seconds (monotonic, same clock family as Profiler/LongTask).
const frames = [];
cdp.on((method, params) => {
  if (method === 'Network.webSocketFrameReceived' || method === 'Network.webSocketFrameSent') {
    const dir = method.endsWith('Received') ? 'in' : 'out';
    const payload = params.response?.payloadData ?? '';
    let type = '?', chatId = null, size = payload.length;
    // socket.io frame: "42[...]" -> JSON array ["events:chat", {...}]
    try {
      const body = payload.startsWith('42') ? payload.slice(2) : payload;
      const parsed = JSON.parse(body);
      if (Array.isArray(parsed)) {
        type = parsed[0];
        const d = parsed[1];
        if (d && typeof d === 'object') {
          chatId = d.chat_id ?? null;
          const kind = d.delta ? 'delta' : d.output ? 'output' : d.type ?? (d.done ? 'done' : '');
          if (kind) type += ':' + kind;
        }
      } else type = 'raw';
    } catch { type = 'unparsed'; }
    frames.push({ t: params.timestamp, dir, type, chatId, size });
  }
});

await cdp.send('Network.enable');

// Page-side long task log, on the same monotonic clock.
await ev(`(()=>{ window.__sc={lts:[]}; try{ new PerformanceObserver(l=>{for(const e of l.getEntries())window.__sc.lts.push([e.startTime,e.duration]);}).observe({entryTypes:['longtask']}); }catch{} return 'ok'; })()`);

const t0 = Date.now();
await cdp.send('Profiler.enable');
await cdp.send('Profiler.setSamplingInterval', { interval: 200 });
await cdp.send('Profiler.start');
console.log(`watching ${SECONDS}s (frames + profiler) ...`);
await sleep(SECONDS * 1000);
const { profile } = await cdp.send('Profiler.stop');
await cdp.send('Profiler.disable');
const lts = JSON.parse(await ev('JSON.stringify(window.__sc.lts)'));
await cdp.send('Network.disable');

// ── 2. who owns the JS time ─────────────────────────────────────────────────
const byId = new Map();
for (const n of profile.nodes) byId.set(n.id, n);
const parent = new Map();
for (const n of profile.nodes) if (n.parent) parent.set(n.id, n.parent);

// sample timestamps are microseconds relative to profile start.
const profT0Ms = profile.startTime / 1000; // CDP startTime is epoch ms
const self = new Map();   // nodeId -> us
const samples = profile.samples;
const deltas = profile.timeDeltas;
for (let i = 0; i < samples.length; i++) {
  self.set(samples[i], (self.get(samples[i]) || 0) + (deltas[i] || 0));
}

const label = (id) => {
  const n = byId.get(id); if (!n) return '?';
  const cf = n.callFrame;
  return `${cf.functionName || '(anon)'} @ ${(cf.url || '').replace(/^.*\/_app\/immutable\//, '')}:${cf.lineNumber}`;
};
const leafOf = new Map();  // sampleIndex-ordered leaves, with absolute ms
const sampleTimes = [];    // absolute epoch ms per sample
let acc = 0;
for (let i = 0; i < samples.length; i++) {
  acc += (deltas[i] || 0) / 1000;
  sampleTimes.push(profT0Ms + acc);
}

const frameTimes = frames.map((f) => f.t);

// ── 3. JS cost in the 400ms after each frame type ───────────────────────────
const LABEL = 400;
let totalJs = 0;
const perType = new Map();
for (let i = 0; i < samples.length; i++) {
  const us = deltas[i] || 0;
  const ms = us / 1000;
  totalJs += ms;
  const t = sampleTimes[i];
  // find the most recent inbound frame within LABEL before t
  let lo = 0, hi = frames.length - 1, best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].t <= t) { best = mid; lo = mid + 1; } else hi = mid - 1;
  }
  let key = '(no recent frame)';
  if (best >= 0 && frames[best].dir === 'in' && (t - frames[best].t) <= LABEL) key = frames[best].type;
  const b = perType.get(key) || { ms: 0, n: 0 };
  b.ms += ms; b.n++;
  perType.set(key, b);
}

console.log(`\nwindow ${(Date.now() - t0) / 1000}s`);
console.log(`frames: ${frames.length}  (in ${frames.filter(f => f.dir === 'in').length}, out ${frames.filter(f => f.dir === 'out').length})`);
console.log(`long tasks: n=${lts.length} sum=${Math.round(lts.reduce((n, l) => n + l[1], 0))}ms max=${Math.round(Math.max(0, ...lts.map(l => l[1])))}ms`);
console.log(`total JS self-time: ${Math.round(totalJs)}ms over ${samples.length} samples`);

console.log('\n=== JS time attributed to the frame that preceded it (within ' + LABEL + 'ms) ===');
for (const [k, b] of [...perType.entries()].sort((a, b) => b[1].ms - a[1].ms).slice(0, 15)) {
  console.log(`  ${Math.round(b.ms).toString().padStart(6)}ms  ${((b.ms / totalJs) * 100).toFixed(1).padStart(5)}%  ${k}`);
}

// ── 4. inbound frame inventory ──────────────────────────────────────────────
console.log('\n=== inbound frame types ===');
const fc = new Map();
for (const f of frames) if (f.dir === 'in') {
  const b = fc.get(f.type) || { n: 0, bytes: 0 };
  b.n++; b.bytes += f.size; fc.set(f.type, b);
}
for (const [k, b] of [...fc.entries()].sort((a, b) => b[1].bytes - a[1].bytes).slice(0, 20)) {
  console.log(`  ${String(b.n).padStart(5)} frames  ${String(Math.round(b.bytes / 1024)).padStart(6)} KB  ${k}`);
}

// ── 5. top functions with the frame type that preceded them ─────────────────
console.log('\n=== top JS self-time by function ===');
const fnAgg = new Map();
for (const [id, us] of self) {
  const k = label(id);
  fnAgg.set(k, (fnAgg.get(k) || 0) + us / 1000);
}
for (const [k, v] of [...fnAgg.entries()].sort((a, b) => b[1] - a[1]).slice(0, 20)) {
  console.log(`  ${Math.round(v).toString().padStart(6)}ms  ${k}`);
}

console.log('\nWINDOW ' + t0 + ' ' + Date.now());
process.exit(0);
