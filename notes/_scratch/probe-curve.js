// Map the curve: peel hidden panels away and measure frame latency after each chunk.
// In a throwaway headless browser, so mutating the DOM is safe.
const out = { steps: [] };
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
    fps: Math.max(...runs.map((r) => r.fps)),
    p50: Math.min(...runs.map((r) => r.p50)),
  };
};

await new Promise((r) => setTimeout(r, 15000)); // settle

const snap = async (tag) => {
  const r = await bestOf(2, 2500);
  out.steps.push({
    tag,
    hidden: hiddenPanels().length,
    mounted_tabs: document.querySelectorAll('.persisted-tab').length,
    dom_nodes: document.getElementsByTagName('*').length,
    heap_mb: performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null,
    ...r,
  });
};

await snap('baseline');

// peel 10 hidden panels at a time
let chunk = 0;
while (hiddenPanels().length > 3) {
  const batch = hiddenPanels().slice(0, 10);
  for (const el of batch) el.remove();
  chunk++;
  await new Promise((r) => setTimeout(r, 1500));
  await snap(`removed_${chunk * 10}`);
}

// finally drop the visible one too, to see the floor
const vis = document.querySelector('.persisted-tab:not(.persisted-tab-hidden)');
if (vis) vis.remove();
await new Promise((r) => setTimeout(r, 1500));
await snap('visible_also_removed');

out.floor_note = 'components stay mounted throughout; only DOM subtrees are removed';
return out;
