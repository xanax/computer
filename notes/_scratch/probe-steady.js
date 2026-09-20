// Control: steady-state frame latency for whatever workspace is loaded.
const out = {};
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
          p95: +s[Math.min(Math.floor(s.length * 0.95), s.length - 1)].toFixed(1),
        });
      }
    }
    requestAnimationFrame(frame);
  });
}
const panels = [...document.querySelectorAll('.persisted-tab')];
out.tabs = panels.length;
out.hidden = panels.filter((p) => p.classList.contains('persisted-tab-hidden')).length;
out.dom_nodes = document.getElementsByTagName('*').length;
out.editors = document.querySelectorAll('.tiptap, [contenteditable="true"]').length;
// let it settle, then take the best of three windows
const runs = [];
for (let i = 0; i < 3; i++) runs.push(await fps(2500));
out.runs = runs;
out.best_fps = Math.max(...runs.map((r) => r.fps));
if (performance.memory) out.js_heap_mb = +(performance.memory.usedJSHeapSize / 1048576).toFixed(1);
return out;
