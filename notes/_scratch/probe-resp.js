// Honest responsiveness probe: frame gaps, hidden-tab payload, per-type breakdown.
const out = {};
const panels = [...document.querySelectorAll('.persisted-tab')];

// per-type breakdown from the mounted panels
const byType = {};
for (const p of panels) {
  const hidden = p.classList.contains('persisted-tab-hidden');
  let type = 'other';
  if (p.querySelector('.tiptap, [contenteditable="true"]')) type = 'chat';
  else if (p.querySelector('.cm-editor')) type = 'file';
  else if (p.querySelector('.xterm')) type = 'terminal';
  else if (p.querySelector('iframe')) type = 'browser';
  const k = (hidden ? 'hidden:' : 'visible:') + type;
  byType[k] = byType[k] || { n: 0, nodes: 0 };
  byType[k].n++;
  byType[k].nodes += p.getElementsByTagName('*').length;
}
out.by_type = byType;

// TipTap / contenteditable editors actually instantiated
out.contenteditable = document.querySelectorAll('[contenteditable="true"]').length;
out.prosemirror = document.querySelectorAll('.ProseMirror, .tiptap').length;

// ── Frame latency: the honest UI-responsiveness number ──────────────
const gaps = [];
let last = performance.now();
const t0 = performance.now();
await new Promise((resolve) => {
  function frame(now) {
    gaps.push(now - last);
    last = now;
    if (performance.now() - t0 < 3000) requestAnimationFrame(frame);
    else resolve();
  }
  requestAnimationFrame(frame);
});
const elapsed = performance.now() - t0;
const sorted = [...gaps].sort((a, b) => a - b);
const q = (p) => sorted[Math.min(Math.floor(sorted.length * p), sorted.length - 1)];
out.frames = gaps.length;
out.elapsed_ms = +elapsed.toFixed(0);
out.frame_gap_p50 = +q(0.5).toFixed(1);
out.frame_gap_p95 = +q(0.95).toFixed(1);
out.frame_gap_max = +q(1).toFixed(1);
out.fps_effective = +(gaps.length / (elapsed / 1000)).toFixed(1);

// ── Long tasks over the same window, with a sanity check ────────────
const tasks = [];
const obs = new PerformanceObserver((l) => {
  for (const e of l.getEntries()) tasks.push(e.duration);
});
try { obs.observe({ entryTypes: ['longtask'] }); } catch {}
const s0 = performance.now();
await new Promise((r) => setTimeout(r, 3000));
const sElapsed = performance.now() - s0;
obs.disconnect();
out.long_tasks_3s = tasks.length;
out.long_task_sum_ms = +tasks.reduce((a, b) => a + b, 0).toFixed(0);
out.long_task_window_ms = +sElapsed.toFixed(0);
if (performance.memory) out.js_heap_mb = +(performance.memory.usedJSHeapSize / 1048576).toFixed(1);
out.total_dom_nodes = document.getElementsByTagName('*').length;
return out;
