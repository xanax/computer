// Prove (or kill) the "markdown re-render per delta" hypothesis in the live page.
//
// Watches the visible tab's transcript for 12s and reports, per second:
//   * DOM mutations inside <code> elements (Shiki rewrites innerHTML per delta)
//   * long tasks (>= 80ms) with their attribution window
//   * transcript growth (a proxy for deltas arriving)
//
// Usage: node scripts/stream-probe.mjs [--seconds 12] [--port 9333]

const PORT = Number(process.argv[process.argv.indexOf('--port') + 1]) || 9333;
const SECONDS = Number(process.argv[process.argv.indexOf('--seconds') + 1]) || 12;

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
const ev = async (expr) => (await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result.value;

console.log('page:', await ev(`JSON.stringify({
  tabs: document.querySelectorAll('.persisted-tab').length,
  dom: document.getElementsByTagName('*').length,
  vis: document.visibilityState
})`));

// Install the observer inside the page.
await ev(`(() => {
  const buckets = new Map();      // second -> {codeMutations, textMutations, total}
  const t0 = performance.now();
  const sec = () => Math.floor((performance.now() - t0) / 1000);
  const bump = (k) => {
    const s = sec();
    const b = buckets.get(s) || { codeMutations: 0, childList: 0, charData: 0, attrs: 0 };
    b[k]++; buckets.set(s, b);
  };

  // Scope: whichever transcript is actually visible.
  const root = document.querySelector('.persisted-tab:not(.persisted-tab-hidden)') || document.body;
  const codeEls = () => Array.from(root.querySelectorAll('pre > code, pre code'));

  const mo = new MutationObserver((records) => {
    for (const r of records) {
      // Is the mutation inside a <code> (i.e. Shiki rewriting a highlighted block)?
      const inCode = r.target instanceof Element ? !!r.target.closest('code') : !!(r.target.parentElement && r.target.parentElement.closest('code'));
      if (r.type === 'childList') { inCode ? bump('codeMutations') : bump('childList'); }
      else if (r.type === 'characterData') { inCode ? bump('codeMutations') : bump('charData'); }
      else if (r.type === 'attributes') { bump('attrs'); }
    }
  });
  mo.observe(root, { childList: true, subtree: true, characterData: true, attributes: true });

  // Long tasks, with whatever the app marked as the last activity.
  const longTasks = [];
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) longTasks.push({ start: e.startTime, dur: e.duration });
    }).observe({ entryTypes: ['longtask'] });
  } catch {}

  window.__probe = {
    stop() {
      mo.disconnect();
      const out = [...buckets.entries()].sort((a, b) => a[0] - b[0]).map(([s, b]) => ({ s, ...b }));
      return {
        buckets: out,
        longTasks: longTasks.map((l) => ({ s: Math.floor((l.start - t0) / 1000), dur: Math.round(l.dur) })),
        codeBlocks: codeEls().length,
        codeChars: codeEls().reduce((n, el) => n + el.textContent.length, 0),
        transcriptChars: root.innerText.length
      };
    }
  };
  return 'installed';
})()`);

console.log(`watching ${SECONDS}s ...`);
await sleep(SECONDS * 1000);

const res = await ev('JSON.stringify(window.__probe.stop())');
const data = JSON.parse(res);

console.log('\nsec | codeMuts | childList | charData | attrs | longTasks(ms)');
let totCode = 0, totChild = 0, totChar = 0;
const ltsBySec = new Map();
for (const l of data.longTasks) {
  const a = ltsBySec.get(l.s) || []; a.push(l.dur); ltsBySec.set(l.s, a);
}
for (const b of data.buckets) {
  totCode += b.codeMutations; totChild += b.childList; totChar += b.charData;
  const lts = ltsBySec.get(b.s) || [];
  console.log(
    `${String(b.s).padStart(3)} | ${String(b.codeMutations).padStart(8)} | ${String(b.childList).padStart(9)} | ${String(b.charData).padStart(8)} | ${String(b.attrs).padStart(5)} | ${lts.join(', ')}`
  );
}
console.log('\ntotals: codeMutations=' + totCode + ' childList=' + totChild + ' charData=' + totChar);
console.log('code blocks=' + data.codeBlocks + ' codeChars=' + data.codeChars + ' transcriptChars=' + data.transcriptChars);
console.log('longTasks: n=' + data.longTasks.length +
  ' sum=' + data.longTasks.reduce((n, l) => n + l.dur, 0) + 'ms' +
  ' max=' + Math.max(0, ...data.longTasks.map((l) => l.dur)) + 'ms');

process.exit(0);
