// DOM half of the "persist until closed" regression guard.
// Emits { counts: { <workspace path>: <chat row count> }, ... } so the shell
// script can compare against the API oracle.
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const $ = (s, root = document) => root.querySelector(s);
const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

const counts = {};
for (const el of $$('.ws-item')) {
  const link = el.querySelector('a[href*="workspace="]');
  if (!link) continue;
  const m = link.getAttribute('href').match(/workspace=([^&]+)/);
  if (!m) continue;
  counts[decodeURIComponent(m[1])] = $$('.ws-chats .chat-item', el).length;
}

return {
  wsItems: $$('.ws-item').length,
  counts,
  perWorkspaceShowMoreTotal: $$('.ws-chat-show-more').length,
  tailToggle: clean($('.ws-show-more')?.innerText) || null,
  // How many workspaces the ranking is hiding. 0 means the tail toggle is
  // correctly absent, which the shell guard treats as a pass, not a failure.
  hiddenWorkspaceCount: (() => {
    const m = clean($('.ws-show-more')?.innerText).match(/\((\d+)\)/);
    return m ? Number(m[1]) : 0;
  })()
};