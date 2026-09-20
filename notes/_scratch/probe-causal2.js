// Causal test with a settle phase, so we compare steady state, not "still loading".
// Throwaway headless browser: mutating the DOM is safe.
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
        });
      }
    }
    requestAnimationFrame(frame);
  });
}

// ── settle: poll until FPS stops climbing (two consecutive close readings) ──
const settle = [];
let prev = -1;
for (let i = 0; i < 20; i++) {
  const r = await fps(2000);
  settle.push(r.fps);
  if (prev > 0 && Math.abs(r.fps - prev) < Math.max(2, prev * 0.25)) break;
  prev = r.fps;
}
out.settle_fps_series = settle;
out.hidden_panels = hidden().length;
out.dom_nodes = document.getElementsByTagName('*').length;

// ── steady-state baselines, taken back to back right before the change ──
out.A1_baseline = await fps(2500);
out.A2_baseline = await fps(2500);

// ── B: components stay mounted, hidden panels taken out of layout ──
for (const el of hidden()) el.style.display = 'none';
await new Promise((r) => setTimeout(r, 800));
out.dom_nodes_after_display_none = document.getElementsByTagName('*').length;
out.B_display_none = await fps(2500);
out.B2_display_none = await fps(2500);

for (const el of hidden()) el.style.display = '';
await new Promise((r) => setTimeout(r, 800));
out.A3_restored_baseline = await fps(2500);

// ── C: drop the hidden subtrees entirely ──
for (const el of hidden()) el.remove();
await new Promise((r) => setTimeout(r, 800));
out.dom_nodes_after_removal = document.getElementsByTagName('*').length;
out.C_dom_removed = await fps(2500);
out.C2_dom_removed = await fps(2500);

if (performance.memory) out.js_heap_mb = +(performance.memory.usedJSHeapSize / 1048576).toFixed(1);
return out;
