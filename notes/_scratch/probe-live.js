// Live UI cost probe: what is mounted, and what does a layout/measure pass cost?
const out = {};

// 1. What is mounted right now
const panels = [...document.querySelectorAll('.persisted-tab')];
out.persisted_tabs = panels.length;
out.visible_tabs = panels.filter((p) => !p.classList.contains('persisted-tab-hidden')).length;
out.total_dom_nodes = document.getElementsByTagName('*').length;
out.persisted_tab_nodes = panels.reduce((n, p) => n + p.getElementsByTagName('*').length, 0);
out.messages_els = document.querySelectorAll('[data-question]').length;
out.textareas = document.querySelectorAll('textarea, [contenteditable]').length;
out.codemirror = document.querySelectorAll('.cm-editor').length;
out.xterm = document.querySelectorAll('.xterm').length;
out.canvases = document.querySelectorAll('canvas').length;
out.iframes = document.querySelectorAll('iframe').length;

// 2. Cost of a full style+layout recalc on the *current* DOM
//    (force invalidation, then read back -> synchronous layout)
function layoutCost() {
  const host = document.body;
  const mark = host.getAttribute('data-probe') || '';
  const t0 = performance.now();
  host.setAttribute('data-probe', mark + '.');
  void document.body.offsetHeight; // force style recalc + layout of everything
  const t1 = performance.now();
  host.setAttribute('data-probe', mark);
  return t1 - t0;
}
out.layout_cost_ms = [];
for (let i = 0; i < 5; i++) out.layout_cost_ms.push(+layoutCost().toFixed(1));

// 3. Cost of the ChatPanel scrolled-question walk (querySelectorAll + rect per row)
function questionWalk() {
  const boxes = [...document.querySelectorAll('.persisted-tab')];
  const t0 = performance.now();
  let seen = 0;
  for (const box of boxes) {
    const scroll = box.querySelector('[data-question]')?.closest('div[style*="overflow"], div');
    if (!scroll) continue;
    for (const el of document.querySelectorAll('[data-question]')) {
      const r = el.getBoundingClientRect();
      seen++;
      if (r.bottom > 200) break;
    }
  }
  return { ms: +(performance.now() - t0).toFixed(1), seen };
}
out.question_walk = questionWalk();

// 4. How many chat panels hold a live socket subscription / memory
out.chat_panels = document.querySelectorAll('.persisted-tab').length;
if (performance.memory) {
  out.js_heap_mb = +(performance.memory.usedJSHeapSize / 1048576).toFixed(1);
  out.js_heap_limit_mb = +(performance.memory.jsHeapSizeLimit / 1048576).toFixed(1);
}

// 5. Live long-task sampling for ~3s while we sit idle
const tasks = [];
const obs = new PerformanceObserver((list) => {
  for (const e of list.getEntries()) tasks.push(+e.duration.toFixed(1));
});
try { obs.observe({ entryTypes: ['longtask'] }); } catch {}
await new Promise((r) => setTimeout(r, 3000));
obs.disconnect();
out.idle_long_tasks_3s = tasks.length;
out.idle_long_task_ms = tasks;

return out;
