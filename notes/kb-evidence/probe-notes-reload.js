// Probe B: reload the dashboard and see whether the note is still there, from
// the server rather than from component state.
const WS = '/home/brendan/computer';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const section = [...document.querySelectorAll('section.dashboard-section')].find(
  (s) => s.querySelector('h2')?.textContent.trim() === 'Notes'
);
if (!section) return JSON.stringify({ error: 'no Notes section' });

const rows = [...section.querySelectorAll('.note-row')].map((r) => ({
  text: r.querySelector('.note-text')?.textContent.trim(),
  detail: r.querySelector('.row-detail')?.textContent.replace(/\s+/g, ' ').trim()
}));
const hint = section.querySelector('.prompt-hint')?.textContent.trim();

const res = await fetch(
  '/api/state/workspace/notes?path=' + encodeURIComponent(WS),
  { credentials: 'include' }
);
const server = await res.json();
await sleep(50);
return JSON.stringify({ url: location.href, rows, hint, serverStatus: res.status, serverNotes: server.notes }, null, 1);
