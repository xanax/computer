// Best-of-3 causal retest: steady state, then drop hidden subtrees, then re-measure.
const out = {};
const hiddenPanels = () => [...document.querySelectorAll('.persisted-tab-hidden')];
function fps(ms = 2500) {
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
          fps: +(gaps.length / ((now - t0) / 1000)).toFixed(1),
          p50: +s[Math.floor(s.length * 0.5)].toFixed(1),
        });
      }
    }
    requestAnimationFrame(frame);
  });
}
const bestOf = async (n, ms) => {
  const runs = [];
  for (let i = 0; i < n; i++) runs.push(await fps(ms));
  return {
    runs,
    best_fps: Math.max(...runs.map((r) => r.fps)),
    best_p50: Math.min(...runs.map((r) => r.p50)),
  };
};

out.tabs = document.querySelectorAll('.persisted-tab').length;
out.hidden = hiddenPanels().length;
out.dom_nodes = document.getElementsByTagName('*').length;
out.editors = document.querySelectorAll('.tiptap, [contenteditable="true"]').length;
await new Promise((r) => setTimeout(r, 15000)); // settle
out.A_with_hidden.memory = performance.memory
  ? (out.A_with_hidden_mb = +(performance.memory.usedJSHeapSize / 1048576).toFixed(1))
  : null;
out.A_with_hidden = await bestOf(3, 2500);

for (const el of hiddenPanels()) el.remove();
await new Promise((r) => setTimeout(r, 2000));
out.dom_nodes_after = document.getElementsByTagName('*').length;
out.B_without_hidden = await bestOf(3, 2500);
out.js_heap_after_mb = performance.memory
  ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1)
  : null;
return out;
