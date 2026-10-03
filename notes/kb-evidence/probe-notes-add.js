// Probe A: add a note through the dashboard UI, then read back what the DOM
// and the server both think.
const WS = '/home/brendan/computer';
const text = 'probe note from cdp ' + Date.now();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const section = [...document.querySelectorAll('section.dashboard-section')].find(
  (s) => s.querySelector('h2')?.textContent.trim() === 'Notes'
);
if (!section) return JSON.stringify({ error: 'no Notes section' });

const input = section.querySelector('input.todo-input');
const addBtn = [...section.querySelectorAll('button')].find(
  (b) => b.textContent.trim() === 'Add note'
);
if (!input || !addBtn) return JSON.stringify({ error: 'no input/button' });

const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
setValue.call(input, text);
input.dispatchEvent(new Event('input', { bubbles: true }));
await sleep(300);
const btnDisabled = addBtn.disabled;
addBtn.click();
await sleep(2500);

const rows = [...section.querySelectorAll('.note-row')].map((r) => ({
  text: r.querySelector('.note-text')?.textContent.trim(),
  detail: r.querySelector('.row-detail')?.textContent.replace(/\s+/g, ' ').trim()
}));
const error = section.querySelector('.notes-error')?.textContent.trim() ?? null;
const empty = section.querySelector('.empty-card p')?.textContent.trim() ?? null;
const draftAfter = input.value;

const res = await fetch(
  '/api/state/workspace/notes?workspace=' + encodeURIComponent(WS),
  { credentials: 'include' }
);
const server = await res.json();

return JSON.stringify(
  {
    sent: text,
    btnDisabled,
    draftAfterAdd: draftAfter,
    error,
    empty,
    uiRows: rows,
    serverStatus: res.status,
    serverNotes: server.notes
  },
  null,
  1
);
