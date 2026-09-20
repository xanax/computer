// Idle churn census: what keeps mutating the DOM (and growing it) when nothing happens?
const out = {};
const t0 = performance.now();
const total0 = document.getElementsByTagName('*').length;
const panels0 = document.querySelectorAll('.persisted-tab').length;

function label(node) {
  let el = node instanceof Element ? node : node.parentElement;
  while (el && el !== document.body) {
    if (el.classList?.contains('persisted-tab')) {
      const has = (s) => !!el.querySelector(s);
      const type = has('.tiptap, [contenteditable="true"]')
        ? 'chat'
        : has('.cm-editor')
          ? 'file'
          : has('.xterm')
            ? 'terminal'
            : has('iframe')
              ? 'browser'
              : 'other';
      const hidden = el.classList.contains('persisted-tab-hidden') ? 'hidden' : 'visible';
      return `${hidden}:${type}`;
    }
    el = el.parentElement;
  }
  return '(outside tabs)';
}

const added = {};
const removed = {};
const touched = {}; // childList mutations per bucket
let mutations = 0;
const samples = [];

const obs = new MutationObserver((records) => {
  for (const r of records) {
    mutations++;
    const bucket = label(r.target);
    touched[bucket] = (touched[bucket] || 0) + 1;
    added[bucket] = (added[bucket] || 0) + r.addedNodes.length;
    removed[bucket] = (removed[bucket] || 0) + r.removedNodes.length;
  }
});
obs.observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });

// sample node count every second for 15s
for (let i = 0; i < 15; i++) {
  await new Promise((r) => setTimeout(r, 1000));
  samples.push({
    t: +((performance.now() - t0) / 1000).toFixed(0),
    nodes: document.getElementsByTagName('*').length,
    tabs: document.querySelectorAll('.persisted-tab').length,
    heap_mb: performance.memory
      ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1)
      : null,
  });
}
obs.disconnect();

out.duration_s = 15;
out.nodes_start = total0;
out.nodes_end = samples[samples.length - 1].nodes;
out.nodes_delta = samples[samples.length - 1].nodes - total0;
out.panels_start = panels0;
out.panels_end = samples[samples.length - 1].tabs;
out.mutations = mutations;
out.per_second_samples = samples;
const top = (o) =>
  Object.entries(o)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([k, v]) => `${k}: ${v}`);
out.mutations_by_bucket = top(touched);
out.nodes_added_by_bucket = top(added);
out.nodes_removed_by_bucket = top(removed);
return out;
