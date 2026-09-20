// Causal test: is the ~2fps caused by the hidden panels' DOM/layout being kept alive?
// This runs in a throwaway headless browser, so mutating the DOM is safe.
const out = {};
const hidden = () => [...document.querySelectorAll('.persisted-tab-hidden')];

function fps(ms = 2000) {
  return new Promise((resolve) => {
    const gaps = [];
    let last = performance.now();
    const t0 = last;
    function frame(now) {
      gaps.push(now - last);
      last = now;
      if (now - t0 < ms) requestAnimationFrame(frame);
      else {
        const s = [...gaps].sort((a, b) => a - b);
        resolve({
          frames: gaps.length,
          fps: +(gaps.length / ((now - t0) / 1000)).toFixed(1),
          p50: +s[Math.floor(s.length * 0.5)].toFixed(1),
          p95: +s[Math.min(Math.floor(s.length * 0.95), s.length - 1)].toFixed(1),
        });
      }
    }
    requestAnimationFrame(frame);
  });
}

out.hidden_panels = hidden().length;
out.dom_nodes_before = document.getElementsByTagName('*').length;
out.A_baseline = await fps(2500);

// ── B: keep every component mounted, just take hidden panels out of layout ──
const saved = [];
for (const el of hidden()) {
  saved.push([el, el.style.display]);
  el.style.display = 'none';
}
await new Promise((r) => setTimeout(r, 500));
out.dom_nodes_after_display_none = document.getElementsByTagName('*').length;
out.B_display_none = await fps(2500);

// ── C: actually drop the hidden subtrees (components left mounted, DOM gone) ──
const removed = [];
for (const el of hidden()) {
  removed.push([el.parentNode, el, el.nextSibling]);
  el.remove();
}
await new Promise((r) => setTimeout(r, 500));
out.dom_nodes_after_removal = document.getElementsByTagName('*').length;
out.C_dom_removed = await fps(2500);

// No restore: this is a throwaway headless browser instance.
if (performance.memory) out.js_heap_mb = +(performance.memory.usedJSHeapSize / 1048576).toFixed(1);
return out;
