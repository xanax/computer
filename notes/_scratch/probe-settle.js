// 1) Load -> usable time series: how long until the UI reaches 60fps?
// 2) Reveal cost: cost of making a hidden tab visible (what a tab switch pays).
// Read-only w.r.t. app state (visibility toggles only), safe to run on a live workspace.
const out = {};
const hiddenPanels = () => [...document.querySelectorAll('.persisted-tab-hidden')];

function fps(ms) {
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

// ── Phase 1: 1-second buckets from here until stable ────────────────
const series = [];
let usableAt = null;
let streak = 0;
for (let i = 0; i < 60; i++) {
  const r = await fps(1000);
  const nodes = document.getElementsByTagName('*').length;
  series.push({ s: i + 1, fps: r.fps, p50: r.p50, nodes });
  // Only trust a 60fps reading as "usable" once the mount storm has begun.
  const mounting = nodes > 1500;
  if (r.fps >= 50 && mounting) {
    streak++;
    if (streak >= 3 && usableAt === null) usableAt = i + 1 - 2;
  } else if (r.fps < 50) streak = 0;
  if (usableAt !== null && i > usableAt + 4) break;
}
out.tabs = document.querySelectorAll('.persisted-tab').length;
out.hidden = hiddenPanels().length;
out.dom_nodes = document.getElementsByTagName('*').length;
out.editors = document.querySelectorAll('.tiptap, [contenteditable="true"]').length;
out.time_to_sustained_60fps_s = usableAt;
out.first_5s_fps = series.slice(0, 5).map((r) => r.fps);
out.series = series.map((r) => `${r.s}s:${r.fps}fps/${r.nodes}n`).join(' ');
out.steady_fps = (await fps(2500)).fps;
if (performance.memory) out.heap_mb = +(performance.memory.usedJSHeapSize / 1048576).toFixed(1);

// ── Phase 2: reveal cost (what switching to a hidden tab pays) ──────
const targets = hiddenPanels().slice(0, 6);
const reveals = [];
for (const el of targets) {
  el.style.visibility = 'visible';
  const r = await fps(1500); // first 1.5s after revealing
  reveals.push({ nodes: el.getElementsByTagName('*').length, fps: r.fps, p50: r.p50 });
  el.style.visibility = 'hidden';
  await new Promise((x) => setTimeout(x, 600));
}
out.reveal_cost = reveals;
out.reveal_note = 'each entry = frame latency in the 1.5s window after a hidden tab is made visible';
return out;
