// Probe C: remove the note with the row's trash button and confirm it leaves
// both the list and the server.
const WS = '/home/brendan/computer';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const section = [...document.querySelectorAll('section.dashboard-section')].find(
  (s) => s.querySelector('h2')?.textContent.trim() === 'Notes'
);
if (!section) return JSON.stringify({ error: 'no Notes section' });

const before = [...section.querySelectorAll('.note-row')].length;
const remove = section.querySelector('.note-row .note-remove');
if (!remove) return JSON.stringify({ error: 'no remove button', before });
remove.click();
await sleep(2000);

const afterRows = [...section.querySelectorAll('.note-row')].map((r) =>
  r.querySelector('.note-text')?.textContent.trim()
);
const empty = section.querySelector('.empty-card p')?.textContent.trim() ?? null;
const error = section.querySelector('.notes-error')?.textContent.trim() ?? null;

const res = await fetch(
  '/api/state/workspace/notes?path=' + encodeURIComponent(WS),
  { credentials: 'include' }
);
const server = await res.json();
return JSON.stringify(
  { before, afterRows, empty, error, serverStatus: res.status, serverNotes: server.notes },
  null,
  1
);
