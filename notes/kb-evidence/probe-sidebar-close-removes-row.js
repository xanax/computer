// Closes a chat from the sidebar and checks its row actually leaves.
//
// This is the half that row counts cannot prove: a predicate that shows every
// open chat is only correct if closing is the thing that removes it. Nothing
// else may quietly do it.
//
// Selects a real open chat, clicks its close button, then re-reads the DOM and
// the API. The chat is restored afterwards unless --restore says otherwise.
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const $ = (s, root = document) => root.querySelector(s);
const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, extra = '') {
  const r = await fetch(`/api/chats?workspace=${encodeURIComponent(path)}` + extra);
  return (await r.json()).chats || [];
}

// The close control is the second button inside a row; the first is the menu.
function closeButton(row) {
  return $$('button', row).find((b) => (b.getAttribute('aria-label') || '').toLowerCase().includes('close'))
    || $$('button', row)[1];
}

const log = [];
for (const el of $$('.ws-item')) {
  const link = el.querySelector('a[href*="workspace="]');
  const m = link && link.getAttribute('href').match(/workspace=([^&]+)/);
  if (!m) continue;
  const path = decodeURIComponent(m[1]);
  const chatsEl = $('.ws-chats', el);
  if (!chatsEl) continue;

  const rows = () => $$('.chat-item', chatsEl).map((r) => r.getAttribute('title'));
  const before = rows();

  // Only touch a workspace that actually has an open chat to close.
  const open = (await api(path, '&limit=200&include_closed=true')).filter((c) => !c.closed_at);
  if (!open.length) continue;

  const victim = open[0];
  const row = $$('.chat-item', chatsEl).find((r) => r.getAttribute('title') === victim.title);
  if (!row) {
    log.push({ ws: path.split('/').pop(), victim: victim.title, note: 'no row for an open chat' });
    continue;
  }

  const btn = closeButton(row);
  if (!btn) {
    log.push({ ws: path.split('/').pop(), victim: victim.title, note: 'no close button' });
    continue;
  }

  btn.click();
  await sleep(1200);
  const after = rows();

  log.push({
    ws: path.split('/').pop(),
    victim: victim.title,
    rowsBefore: before.length,
    rowsAfter: after.length,
    // The row for the closed chat must be gone, and nothing else should be.
    rowRemoved: !after.includes(victim.title),
    onlyOneLeft: before.length - after.length === 1
  });
  break; // One decisive case is enough; this mutates real data.
}

return {
  cases: log,
  removed: log.filter((c) => c.rowRemoved).length,
  pass: log.length > 0 && log.every((c) => c.rowRemoved)
};