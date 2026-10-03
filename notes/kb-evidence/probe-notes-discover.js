// Probe 1: where is the dashboard, and is the notes card there at all?
const vis = (el) => {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return 'zero-rect';
  if (el.closest('.persisted-tab-hidden')) return 'hidden-tab';
  const cs = getComputedStyle(el);
  if (cs.visibility === 'hidden' || cs.display === 'none') return 'css-hidden';
  return 'visible';
};
const notesInput = [...document.querySelectorAll('input')].find((i) =>
  /note/i.test(i.placeholder || '')
);
const addBtn = [...document.querySelectorAll('button')].find(
  (b) => b.textContent.trim() === 'Add note'
);
const sections = [...document.querySelectorAll('section.dashboard-section')].map((s) => ({
  title: s.querySelector('h2')?.textContent.trim() ?? '',
  state: vis(s)
}));
const tabs = [...document.querySelectorAll('[role="tab"], .tab-bar button, button')]
  .map((b) => b.textContent.trim())
  .filter((t) => t && t.length < 24)
  .slice(0, 40);
return JSON.stringify(
  {
    url: location.href,
    ready: document.readyState,
    notesInput: notesInput
      ? { placeholder: notesInput.placeholder, state: vis(notesInput) }
      : null,
    addBtn: addBtn ? vis(addBtn) : null,
    sections,
    tabs
  },
  null,
  1
);
