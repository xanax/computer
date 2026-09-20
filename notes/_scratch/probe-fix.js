// Does CSS containment on the hidden panels fix it? Candidate one-line fixes, same page.
// Throwaway headless browser; style changes are reversible.
const out = { trials: [] };
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
  return { fps: Math.max(...runs.map((r) => r.fps)), p50: Math.min(...runs.map((r) => r.p50)) };
};
const record = async (tag, css) => {
  if (css !== undefined) {
    for (const el of hiddenPanels()) el.style.cssText = css;
    await new Promise((r) => setTimeout(r, 1500));
  }
  out.trials.push({
    tag,
    css: css ?? null,
    hidden: hiddenPanels().length,
    ...(await bestOf(2, 2500)),
  });
};

await new Promise((r) => setTimeout(r, 15000)); // settle
await record('baseline (current: visibility:hidden)');

await record('content-visibility: hidden', 'content-visibility: hidden;');

await record(
  'content-visibility: auto + contain-intrinsic-size',
  'content-visibility: auto; contain-intrinsic-size: auto 800px;'
);

await record('contain: strict', 'contain: strict;');

await record('display: none', 'display: none;');

await record('back to visibility: hidden', 'visibility: hidden;');

out.note = 'all candidates keep the Svelte components mounted; only rendering strategy changes';
if (performance.memory) out.js_heap_mb = +(performance.memory.usedJSHeapSize / 1048576).toFixed(1);
return out;
