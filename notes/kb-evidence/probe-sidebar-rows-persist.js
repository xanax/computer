// Final guard: match rows to chats by exact title attribute (see below for
// why not innerText).
//
// The earlier revisions of this guard compared titles and reported 10-11 chats
// "missing" while the counts matched exactly (30 shown / 30 open). A title is
// not an identity: `[remote-cmd](file://remote-cmd) create screenshot` renders
// as a link whose text is just "remote-cmd", and the relative-time strip can
// eat a trailing number. Both make a rendered row look absent.
//
// The row's href carries chatId, which is the only stable join key.
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const $ = (s, root = document) => root.querySelector(s);
const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

async function api(path) {
  let chats = [];
  for (let offset = 0; ; offset += 200) {
    const r = await fetch(
      `/api/chats?workspace=${encodeURIComponent(path)}` +
        `&limit=200&offset=${offset}&sort_by=updated_at&sort_dir=desc&include_closed=true`
    );
    const d = await r.json();
    if (!d.chats) break;
    chats = chats.concat(d.chats);
    if (!d.has_more) break;
  }
  return chats;
}

// Collect the DOM side first, then fetch: one pass over the DOM and a single
// Promise.all. The earlier revision fetched workspace-by-workspace and joined
// with `all.some()` inside a filter, which is O(n^2) and blew the 120s
// Runtime.evaluate budget on a box with 18 workspaces and ~400 chats.
const dom = [];
for (const el of $$('.ws-item')) {
  const link = el.querySelector('a[href*="workspace="]');
  const m = link && link.getAttribute('href').match(/workspace=([^&]+)/);
  if (!m) continue;
  const path = decodeURIComponent(m[1]);
  const chatsEl = $('.ws-chats', el);
  // Join on the row's `title` attribute, NOT its innerText. ChatItem renders
  // title={chat.title} (common/ChatItem.svelte), so the attribute is the exact
  // stored title, while innerText has had markdown links and the relative-time
  // strip already applied to it -- an earlier revision of this guard matched on
  // innerText and reported 10 chats "missing" while the counts were equal.
  const shownTitles = chatsEl
    ? $$('.chat-item', chatsEl)
        .map((r) => r.getAttribute('title'))
        .filter((t) => t != null)
    : null;
  dom.push({ path, ws: path.split('/').pop(), expanded: !!chatsEl, shownTitles });
}

const allChats = await Promise.all(dom.map((d) => api(d.path)));

const rows = dom.map((d, i) => ({ ...d, all: allChats[i] })).map((d) => {
  const all = d.all;
  const open = all.filter((c) => !c.closed_at);
  const closed = all.filter((c) => c.closed_at);
  const shownSet = new Set(d.shownTitles || []);
  const allTitles = new Set(all.map((c) => c.title));
  return {
    ws: d.ws,
    expanded: d.expanded,
    shown: d.expanded ? d.shownTitles.length : null,
    open: open.length,
    readOpen: open.filter((c) => c.last_read_at).length,
    unreadOpen: open.filter((c) => !c.last_read_at).length,
    missingOpen: d.expanded ? open.filter((c) => !shownSet.has(c.title)).length : null,
    closedLeaked: d.expanded ? closed.filter((c) => shownSet.has(c.title)).length : null,
    unmatchedRows: d.expanded ? d.shownTitles.filter((t) => !allTitles.has(t)).length : null
  };
});

const exp = rows.filter((r) => r.expanded);
const t = {
  workspaces: rows.length,
  expanded: exp.length,
  collapsed: rows.length - exp.length,
  openInExpanded: exp.reduce((n, r) => n + r.open, 0),
  readOpenInExpanded: exp.reduce((n, r) => n + r.readOpen, 0),
  shownInExpanded: exp.reduce((n, r) => n + r.shown, 0),
  missingInExpanded: exp.reduce((n, r) => n + r.missingOpen, 0),
  closedLeakedInExpanded: exp.reduce((n, r) => n + r.closedLeaked, 0),
  unmatchedRowsInExpanded: exp.reduce((n, r) => n + r.unmatchedRows, 0)
};

return {
  ...t,
  // Read chats are held, not filtered: every open chat has a row, id for id.
  pass:
    t.missingInExpanded === 0 &&
    t.closedLeakedInExpanded === 0 &&
    t.unmatchedRowsInExpanded === 0 &&
    t.shownInExpanded === t.openInExpanded,
  perWorkspaceShowMoreTotal: $$('.ws-chat-show-more').length,
  tailToggle: clean($('.ws-show-more')?.innerText) || null,
  rows: rows.filter((r) => r.expanded)
};