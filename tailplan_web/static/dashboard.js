// Tailplan dashboard: instant actions on top of server-rendered forms. Every action also works without it.
const $ = (sel, node = document) => node.querySelector(sel);
const $$ = (sel, node = document) => [...node.querySelectorAll(sel)];
const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.append(...kids);
  return node;
};
const root = $('#dashboard-root');
const base = root ? root.dataset.base : ($('.brand')?.getAttribute('href') || '').replace(/\/dashboard$/, '');
const csrf = root?.dataset.csrf || $('input[name=csrf]')?.value || '';
const view = root?.dataset.view || '';
const search = $('#dash-q');
const toolbar = $('form.toolbar');
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
document.documentElement.classList.add('js-ready');

async function post(url, body) {
  const res = await fetch(url, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ csrf, ...body }),
  });
  let data = null;
  try { data = await res.json(); } catch { /* A non-JSON error page. */ }
  if (!res.ok || !data || data.ok === false) throw new Error(data?.error || `Request failed (${res.status}).`);
  return data;
}

// Only http and https URLs open. A details URL must also be on this origin.
function safeUrl(raw, { sameOrigin = false } = {}) {
  if (!raw) return null;
  let u;
  try { u = new URL(raw, location.origin); } catch { return null; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
  if (sameOrigin && u.origin !== location.origin) return null;
  return u.href;
}
function openShare(raw) { const url = safeUrl(raw); if (url) window.open(url, '_blank', 'noopener,noreferrer'); }
function openDetail(raw) { const url = safeUrl(raw, { sameOrigin: true }); if (url) location.assign(url); }

// Toasts and undo.
const toasts = el('div', { class: 'float toasts', role: 'status', 'aria-live': 'polite' });
document.body.append(toasts);
let undoLast = null;
function toast(text, { error = false, undo = null } = {}) {
  const node = el('div', { class: 'toast' + (error ? ' error' : '') }, el('span', {}, text));
  if (undo) {
    // Each button runs its own action. The `z` key runs the latest one.
    undoLast = { undo, node };
    const button = el('button', { type: 'button' }, 'Undo', el('kbd', {}, 'Z'));
    button.addEventListener('click', () => {
      node.remove();
      if (undoLast?.undo === undo) undoLast = null;
      undo();
    });
    node.append(button);
  }
  toasts.append(node);
  while (toasts.children.length > 3) toasts.firstChild.remove();
  setTimeout(() => node.remove(), error ? 7000 : 5000);
}
function runUndo() {
  const last = undoLast;
  undoLast = null;
  if (!last) return;
  last.node.remove();
  last.undo();
}
const fail = (err) => toast(err.message || 'Something went wrong.', { error: true });

// Keep the anchor element at the same place on screen while the page changes.
function keep(anchor, fn) {
  const top = anchor?.isConnected && anchor.getClientRects().length ? anchor.getBoundingClientRect().top : null;
  fn();
  if (top !== null && anchor.isConnected && anchor.getClientRects().length) {
    const shift = anchor.getBoundingClientRect().top - top;
    if (shift) window.scrollBy(0, shift);
  }
}

// Theme.
document.addEventListener('click', (event) => {
  const button = event.target.closest('.theme-switch button[name=theme]');
  if (button) { event.preventDefault(); setTheme(button.value); }
  if (event.target.closest('.search kbd')) openPalette();
});
async function setTheme(theme) {
  const html = document.documentElement;
  const before = html.dataset.theme || 'system';
  const apply = (value) => {
    if (value === 'system') delete html.dataset.theme; else html.dataset.theme = value;
    $$('.theme-switch button[name=theme]').forEach((b) => b.setAttribute('aria-pressed', String(b.value === value)));
    syncThemeColor(value);
  };
  apply(theme);
  try { await post(`${base}/dashboard/theme`, { theme }); } catch (err) { apply(before); fail(err); }
}

// The browser bar color follows the theme. System gets one meta per color scheme.
const THEME_COLORS = { light: '#ffffff', dark: '#0a0a0a' };
function syncThemeColor(value) {
  $$('meta[name="theme-color"]').forEach((meta) => meta.remove());
  const metas = value in THEME_COLORS ? [{ content: THEME_COLORS[value] }]
    : Object.entries(THEME_COLORS).map(([scheme, content]) => ({ media: `(prefers-color-scheme: ${scheme})`, content }));
  for (const attrs of metas) document.head.append(el('meta', { name: 'theme-color', ...attrs }));
}

// Rows, counts, and sections.
const owned = () => $$('.draft-row[data-draft-id]:not(.shared)');
const visibleRows = () => $$('.draft-row').filter((row) => !row.hidden && row.getClientRects().length);
const nodesFor = (id) => $$(`.draft-row[data-draft-id="${CSS.escape(id)}"]:not(.shared), .pin[data-draft-id="${CSS.escape(id)}"]`);
const rowFor = (id) => owned().find((row) => row.dataset.draftId === id);
// Keep the plain description of each row. A page loaded with ?q= has a snippet, so it uses data-desc.
function plainDesc(row) {
  if (row.dataset.desc === undefined) return $('.row-desc', row)?.cloneNode(true) || null;
  return row.dataset.desc ? el('p', { class: 'row-desc' }, row.dataset.desc) : null;
}
for (const row of $$('.draft-row')) row._desc = plainDesc(row);
for (const row of owned()) {
  row._home = row.dataset.archived;
  const box = $('.row-select', row);
  if (box) box.hidden = false;
}
for (const row of $$('.draft-row[data-share-url]')) {
  if (!row.dataset.shareUrl) continue;
  const button = el('button', { class: 'ghost sm copy-url', type: 'button', 'aria-label': `Copy URL of ${row.dataset.title}` }, 'Copy URL');
  const meta = $('.row-meta', row);
  meta?.insertBefore(button, $('.details', meta));
}
if (view !== 'shared') $$('section.group').forEach((sec) => { if ($('.draft-row.shared', sec)) sec.dataset.shared = '1'; });
if (search && !isMac) { const kbd = $('.search kbd'); if (kbd) kbd.textContent = 'Ctrl K'; }

function bump(key, delta) {
  if (!delta) return;
  const nums = [$(`.tab[data-view="${key}"] .num`), $(`.more-view[data-more-view="${key}"] .num`)];
  if (key === 'favorites') nums.push($('.shelf .section-head .num'));
  for (const num of nums) if (num) num.textContent = Math.max(0, (parseInt(num.textContent, 10) || 0) + delta);
}
function filtering() { return Boolean(search?.value.trim() || toolbar?.querySelector('input[name=tag],input[name=repo]')); }
function refresh() {
  const unstarred = (row) => view === 'favorites' && row.dataset.favorite !== '1';
  for (const row of owned()) row.hidden = Boolean(row._gone || row._match === false || unstarred(row));
  for (const row of $$('.draft-row.shared')) row.hidden = row._match === false;
  let shown = 0;
  for (const sec of $$('section.group')) {
    const count = $$('.draft-row', sec).filter((row) => !row.hidden).length;
    const num = $('.section-head .num', sec);
    if (num && !sec.dataset.shared) num.textContent = count;
    sec.hidden = !count || Boolean(sec.dataset.shared && filtering());
    if (!sec.dataset.shared) shown += count;
  }
  const shelf = $('.shelf');
  if (shelf) shelf.hidden = filtering() || !$$('.pin', shelf).some((pin) => !pin.hidden);
  let empty = $('.js-empty');
  if (!shown && !$('.empty:not(.js-empty)') && root) {
    if (!empty) { empty = el('div', { class: 'empty js-empty' }, el('p', {}, 'No drafts match.')); (shelf || toolbar)?.after(empty); }
    empty.hidden = false;
  } else if (empty) empty.hidden = true;
  if (cursor?.hidden) setCursor(null);
  syncBulk();
}

// Favorites.
function setFavorite(id, on) {
  if (on) restorePin(id);
  for (const node of nodesFor(id)) {
    node.dataset.favorite = on ? '1' : '0';
    if (node.matches('.draft-row')) node.classList.toggle('fav', on);
    const form = $('.star-form', node);
    const button = form && $('button', form);
    if (!button) continue;
    const label = on ? 'Remove from favorites' : 'Add to favorites';
    form.action = form.action.replace(/\/(un)?favorite$/, on ? '/unfavorite' : '/favorite');
    button.textContent = on ? '★' : '☆';
    button.classList.toggle('on', on);
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-pressed', String(on));
    button.title = label;
  }
  if (!on) unpin(id);
  refresh();
}
const pinSel = (id) => `.pin[data-draft-id="${CSS.escape(id)}"]`;
const unpinned = new Map();
function unpin(id) {
  for (const pin of $$(pinSel(id))) {
    unpinned.set(id, { pin, parent: pin.parentNode, next: pin.nextSibling });
    pin.remove();
  }
}
function restorePin(id) {
  if ($(pinSel(id))) return;
  const saved = unpinned.get(id);
  unpinned.delete(id);
  const row = rowFor(id);
  if (saved?.parent.isConnected) {
    saved.pin.hidden = row?.dataset.archived === '1';
    saved.parent.insertBefore(saved.pin, saved.next?.parentNode === saved.parent ? saved.next : null);
    return;
  }
  if (!row) return;
  const grid = $('.shelf-grid') || makeShelf();
  if (grid) grid.prepend(buildPin(row));
}
// The first star on the Active view adds the Favorites shelf.
function makeShelf() {
  if (view !== 'active' || !toolbar) return null;
  const grid = el('div', { class: 'shelf-grid' });
  const all = el('a', { href: viewUrl('favorites') }, 'View all ', el('span', { class: 'num' }, $('.tab[data-view="favorites"] .num')?.textContent || '0'));
  const shelf = el('section', { class: 'shelf', 'aria-label': 'Favorites' }, el('div', { class: 'section-head' }, el('h2', {}, 'Favorites'), all), grid);
  ($('.facets') || toolbar).after(shelf);
  return grid;
}
function buildPin(row) {
  const d = row.dataset;
  const pin = el('article', { class: 'pin', 'data-draft-id': d.draftId, 'data-favorite': '1', 'data-title': d.title,
    'data-share-url': d.shareUrl, 'data-detail-url': d.detailUrl });
  const meta = el('div', { class: 'pin-meta' });
  const ver = $('.ver', row);
  if (ver) meta.append(ver.cloneNode(true));
  const tag = (d.tags || '').split(' ')[0];
  if (tag) meta.append(el('span', {}, tag));
  const title = el('div', { class: 'pin-title' }, ...[...$('.row-title', row).childNodes].map((n) => n.cloneNode(true)));
  const detail = safeUrl(d.detailUrl, { sameOrigin: true });
  pin.append($('.star-form', row).cloneNode(true), el('div', { class: 'pin-body' }, title, meta),
    el('a', { class: 'details', ...(detail ? { href: detail } : {}) }, 'Details'));
  return pin;
}
const pending = new Set();
// The first visible element after `node` that stays on screen: a row, or the content below the shelf.
function nextVisible(node) {
  if (node.matches('.pin')) {
    let next = node.closest('.shelf')?.nextElementSibling;
    while (next && (next.hidden || !next.getClientRects().length)) next = next.nextElementSibling;
    return next || null;
  }
  const rows = visibleRows().filter((r) => r !== node);
  return rows.find((r) => node.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING)
    || rows.reverse().find((r) => node.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_PRECEDING) || null;
}
async function toggleFavorite(id, trigger = null) {
  const node = trigger?.closest?.('.draft-row, .pin') || nodesFor(id)[0];
  if (!node || pending.has(id)) return;
  const on = node.dataset.favorite !== '1';
  const live = rowFor(id)?.dataset.archived !== '1';
  // When the clicked item goes away, hold the next item in place.
  const leaves = !on && (node.matches('.pin') || view === 'favorites');
  const anchor = leaves ? nextVisible(node) : node;
  pending.add(id);
  keep(anchor, () => { setFavorite(id, on); if (live) bump('favorites', on ? 1 : -1); });
  try {
    await post(`${base}/dashboard/drafts/${id}/${on ? 'favorite' : 'unfavorite'}`, {});
  } catch (err) {
    keep(anchor, () => { setFavorite(id, !on); if (live) bump('favorites', on ? -1 : 1); });
    fail(err);
  } finally { pending.delete(id); }
}

// Archive.
function setArchived(row, archived) {
  const was = row.dataset.archived === '1';
  if (was === archived) return;
  row.dataset.archived = archived ? '1' : '0';
  row.classList.toggle('archived', archived);
  row._gone = row.dataset.archived !== row._home;
  const form = $('.archive-form', row);
  if (form) {
    form.action = form.action.replace(/\/(un)?archive$/, archived ? '/unarchive' : '/archive');
    $('button', form).textContent = archived ? 'Unarchive' : 'Archive';
  }
  const sign = archived ? -1 : 1;
  bump('active', sign);
  bump('archived', -sign);
  if (row.dataset.favorite === '1') bump('favorites', sign);
  if (+row.dataset.versions > 1) bump('living', sign);
  for (const pin of $$(`.pin[data-draft-id="${CSS.escape(row.dataset.draftId)}"]`)) pin.hidden = archived;
  if (archived) { const box = $('.row-select', row); if (box?.checked) { box.checked = false; syncBulk(); } }
}
async function archiveRows(rows, archived, { undo = false } = {}) {
  rows = rows.filter((row) => (row.dataset.archived === '1') !== archived && !pending.has(row.dataset.draftId));
  if (!rows.length) return;
  const others = visibleRows().filter((r) => !rows.includes(r));
  const next = others.find((r) => rows[0].compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING)
    || others.reverse().find((r) => rows[0].compareDocumentPosition(r) & Node.DOCUMENT_POSITION_PRECEDING) || null;
  const apply = (value) => keep(next, () => { rows.forEach((row) => setArchived(row, value)); refresh(); });
  const hadCursor = Boolean(cursor && rows.includes(cursor));
  rows.forEach((row) => pending.add(row.dataset.draftId));
  apply(archived);
  if (hadCursor && next) setCursor(next);
  try {
    const action = archived ? 'archive' : 'unarchive';
    if (rows.length === 1) await post(`${base}/dashboard/drafts/${rows[0].dataset.draftId}/${action}`, {});
    else await post(`${base}/dashboard/bulk`, { draftIds: rows.map((row) => row.dataset.draftId), action });
    const noun = rows.length === 1 ? `“${rows[0].dataset.title}”` : `${rows.length} drafts`;
    toast(`${undo ? 'Restored' : archived ? 'Archived' : 'Unarchived'} ${noun}`,
      { undo: undo ? null : () => archiveRows(rows, !archived, { undo: true }) });
  } catch (err) { apply(!archived); fail(err); } finally { rows.forEach((row) => pending.delete(row.dataset.draftId)); }
}

// Tags.
function addTagPill(row, tag) {
  const tags = (row.dataset.tags || '').split(' ').filter(Boolean);
  if (tags.includes(tag)) return;
  row.dataset.tags = [...tags, tag].join(' ');
  row.dataset.s += ` ${tag}`;
  $('.suggest-form', row)?.remove();
  if (tags.length >= 3) return;
  const meta = $('.row-meta', row);
  const tagPills = $$(':scope > a.pill', meta).slice(0, tags.length);
  const pill = el('a', { class: 'pill', href: `${base}/dashboard?tag=${encodeURIComponent(tag)}` }, tag);
  // A new tag goes after the last tag pill, before the repo pill and the other chips.
  if (tagPills.length) tagPills.at(-1).after(pill); else meta.prepend(pill);
}
async function acceptSuggestion(form) {
  const row = form.closest('.draft-row');
  const tag = form.elements.add_tag.value;
  const holder = form.parentNode;
  const after = form.nextSibling;
  const prevS = row.dataset.s;
  addTagPill(row, tag);
  try { await post(form.action, { add_tag: tag }); toast(`Tagged “${tag}”`); } catch (err) {
    row.dataset.tags = (row.dataset.tags || '').split(' ').filter((t) => t !== tag).join(' ');
    row.dataset.s = prevS;
    $$('.row-meta > a.pill', row).find((a) => a.textContent === tag)?.remove();
    holder.insertBefore(form, after);
    fail(err);
  }
}

document.addEventListener('submit', (event) => {
  const form = event.target;
  const item = form.closest('.draft-row, .pin');
  if (form === toolbar) { event.preventDefault(); closeMenus(); clearTimeout(timer); remoteSearch(); return; }
  if (!item) return;
  if (form.matches('.star-form')) { event.preventDefault(); toggleFavorite(item.dataset.draftId, item); }
  else if (form.matches('.archive-form')) { event.preventDefault(); archiveRows([item], item.dataset.archived !== '1'); }
  else if (form.matches('.suggest-form')) { event.preventDefault(); acceptSuggestion(form); }
});

// Copy.
async function copyUrl(url) {
  if (!url) return;
  try { await navigator.clipboard.writeText(url); } catch {
    const area = el('textarea', { readonly: '', style: 'position:fixed;opacity:0' });
    area.value = url;
    document.body.append(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    if (!ok) { toast('Copy failed. Select the URL and copy it by hand.', { error: true }); return; }
  }
  toast('Link copied');
}
document.addEventListener('click', (event) => {
  const button = event.target.closest('.copy-url');
  if (button) copyUrl(button.closest('.draft-row').dataset.shareUrl);
});

// Search: an instant local filter, then a full-text search on the server.
let timer = 0;
let seq = 0;
const OPERATORS = /[:"()*]|(^|\s)-|\b(OR|AND|NOT)\b/;
function localFilter() {
  const q = search.value.trim().toLowerCase();
  if (OPERATORS.test(search.value)) return;
  const words = q.split(/\s+/).filter(Boolean);
  for (const row of $$('.draft-row')) row._match = words.every((w) => (row.dataset.s || '').includes(w));
  refresh();
}
function params() {
  const out = new URLSearchParams();
  for (const [key, value] of new FormData(toolbar)) {
    if (value && !(key === 'group' && value === 'time') && !(key === 'view' && value === 'active')) out.set(key, value);
  }
  return out;
}
function snippet(html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const out = document.createDocumentFragment();
  for (const node of tpl.content.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) out.append(node.textContent);
    else if (node.nodeName === 'MARK' && !node.attributes.length && [...node.childNodes].every((c) => c.nodeType === Node.TEXT_NODE)) {
      out.append(el('mark', {}, node.textContent));
    } else return null;
  }
  return out;
}
function setDesc(row, draft) {
  const main = $('.row-main', row);
  const old = $('.row-desc', main);
  let next = row._desc?.cloneNode(true) || (draft.description ? el('p', { class: 'row-desc' }, draft.description) : null);
  const marked = draft.snippet ? snippet(String(draft.snippet)) : null;
  if (draft.snippet) {
    next = el('p', { class: 'row-desc' });
    if (marked) next.append(marked); else next.textContent = draft.description || '';
  }
  if (old) old.replaceWith(...(next ? [next] : [])); else if (next) main.append(next);
}
async function remoteSearch() {
  const mine = ++seq;
  const qs = params().toString();
  const page = `${base}/dashboard${qs ? `?${qs}` : ''}`;
  let state;
  try {
    const res = await fetch(`${base}/dashboard?${qs ? `${qs}&` : ''}format=json`, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    state = await res.json();
    if (!res.ok || !state.ok) throw new Error(state.error || 'Search failed.');
  } catch (err) { if (mine === seq) fail(err); return; }
  if (mine !== seq) return;
  // An archived row is gone from this view. If the server still lists it, load the page.
  const rows = new Map($$('section.group:not([data-shared]) .draft-row').filter((row) => !row._gone)
    .map((row) => [row.dataset.draftId, row]));
  if (state.view !== view || state.drafts.some((d) => !rows.has(d.draftId))) { location.assign(page); return; }
  const order = new Map(state.drafts.map((d, i) => [d.draftId, i]));
  for (const [id, row] of rows) {
    row._match = order.has(id);
    if (row._match) setDesc(row, state.drafts[order.get(id)]);
  }
  for (const row of $$('section.group[data-shared] .draft-row')) row._match = true;
  for (const list of $$('section.group:not([data-shared]) .list')) {
    [...list.children].sort((a, b) => (order.get(a.dataset.draftId) ?? 1e9) - (order.get(b.dataset.draftId) ?? 1e9))
      .forEach((row) => list.append(row));
  }
  syncSort(state);
  syncLinks();
  refresh();
  history.replaceState(history.state, '', page);
}
// Links that the server built for the old query get the new one.
function syncLinks() {
  const q = search.value.trim();
  for (const a of $$('a.dir, .facets a.pill, .sheet-facets a.pill, .row-meta > a.pill')) {
    const u = new URL(a.href, location.origin);
    if (q) u.searchParams.set('q', q); else u.searchParams.delete('q');
    a.href = u.href;
  }
  const qs = params();
  const show = ['q', 'tag', 'repo'].some((k) => qs.get(k));
  ['q', 'tag', 'repo'].forEach((k) => qs.delete(k));
  let clear = $('a.clear', toolbar);
  if (show && !clear) ($('.filters-sheet', toolbar) || toolbar).append(clear = el('a', { class: 'clear' }, 'Clear'));
  if (clear) { clear.hidden = !show; clear.href = `${base}/dashboard${qs.size ? `?${qs}` : ''}`; }
}
function syncSort(state) {
  const select = $('#dash-sort');
  if (!select) return;
  let relevance = [...select.options].find((o) => o.textContent === 'Relevance');
  if (state.ranked && !relevance) select.prepend(relevance = el('option', { value: 'relevance' }, 'Relevance'));
  if (!state.ranked && relevance) relevance.remove();
  const updated = [...select.options].find((o) => o.textContent === 'Updated');
  if (updated) updated.value = state.ranked ? 'updated' : '';
  for (const option of select.options) option.selected = (option.value || 'updated') === state.sort;
}
if (search && toolbar) {
  search.addEventListener('input', () => { localFilter(); clearTimeout(timer); timer = setTimeout(remoteSearch, 180); });
  for (const select of $$('select', toolbar)) select.addEventListener('change', () => { syncBadge(); toolbar.submit(); });
}

// The views and filters menus are details elements. Close them on an outside click or Escape.
const MENUS = 'details.views-more[open], details.filters-menu[open]';
function closeMenus(except) {
  for (const menu of $$(MENUS)) if (menu !== except) menu.open = false;
}
document.addEventListener('click', (event) => {
  const inside = event.target.closest?.('details.views-more, details.filters-menu, .filters-sheet');
  const menu = inside?.matches('.filters-sheet') ? inside.previousElementSibling : inside;
  closeMenus(menu);
});
// The badge counts the filters that differ from the defaults, the same as the server.
function syncBadge() {
  const summary = toolbar && $('.filters-menu > summary', toolbar);
  if (!summary) return;
  const value = (name) => toolbar.elements[name]?.value || '';
  const count = [!['', 'updated', 'relevance'].includes(value('sort')), Boolean(value('dir')),
    !['', 'time'].includes(value('group')), Boolean(value('tag')), Boolean(value('repo'))].filter(Boolean).length;
  let badge = $('.filters-count', summary);
  if (count && !badge) summary.append(badge = el('span', { class: 'filters-count' }));
  if (badge) { badge.hidden = !count; badge.textContent = String(count); }
  summary.setAttribute('aria-label', count ? `Filters, ${count} active` : 'Filters');
}

// Selection and the bulk bar.
const bar = el('div', { class: 'float bulk-bar', role: 'region', 'aria-label': 'Bulk actions', hidden: '' });
const barCount = el('span', { class: 'count' });
const tagInput = el('input', { type: 'text', name: 'tag', placeholder: 'Add tag', 'aria-label': 'Tag to add', maxlength: '40' });
const tagForm = el('form', {}, tagInput, el('button', { class: 'secondary sm', type: 'submit' }, 'Apply'));
const barButton = (label, fn) => { const b = el('button', { class: 'ghost sm', type: 'button' }, label); b.addEventListener('click', fn); return b; };
const archiveButton = barButton('Archive', () => {
  const rows = selected();
  archiveRows(rows, !rows.every((row) => row.dataset.archived === '1'));
});
bar.append(barCount, barButton('Star', () => bulk('favorite')), barButton('Unstar', () => bulk('unfavorite')),
  archiveButton, tagForm, barButton('Clear', () => clearSelection()));
tagForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const tag = tagInput.value.trim().toLowerCase();
  if (tag) bulk('add_tag', tag).then((ok) => { if (ok) tagInput.value = ''; });
});
if (root) document.body.append(bar);
const selected = () => owned().filter((row) => !row.hidden && $('.row-select', row)?.checked);
function syncBulk() {
  const rows = selected();
  for (const row of owned()) row.classList.toggle('selected', Boolean($('.row-select', row)?.checked));
  bar.hidden = !rows.length;
  document.body.classList.toggle('has-bulk', rows.length > 0);
  barCount.textContent = `${rows.length} selected`;
  archiveButton.textContent = rows.length && rows.every((row) => row.dataset.archived === '1') ? 'Unarchive' : 'Archive';
}
function clearSelection() { $$('.row-select').forEach((box) => { box.checked = false; }); syncBulk(); }
async function bulk(action, tag) {
  const rows = selected();
  if (!rows.length) return false;
  try {
    const data = await post(`${base}/dashboard/bulk`, { draftIds: rows.map((row) => row.dataset.draftId), action, tag });
    keep(rows[0], () => {
      for (const d of data.drafts) {
        const row = rowFor(d.draftId);
        if (row && (row.dataset.favorite === '1') !== d.favorite) {
          setFavorite(d.draftId, d.favorite);
          if (!d.archived) bump('favorites', d.favorite ? 1 : -1);
        }
        if (row && tag) addTagPill(row, tag);
      }
    });
    toast(`${{ favorite: 'Starred', unfavorite: 'Unstarred', add_tag: `Tagged “${tag}”:` }[action] || 'Changed'} ${data.count} draft${data.count === 1 ? '' : 's'}`);
    return true;
  } catch (err) { fail(err); return false; }
}
let anchor = null;
function toggleBox(row, range) {
  const box = row && $('.row-select', row);
  if (!box) return;
  const value = !box.checked;
  const rows = visibleRows();
  if (range && anchor && rows.includes(anchor)) {
    const [a, b] = [rows.indexOf(anchor), rows.indexOf(row)].sort((x, y) => x - y);
    rows.slice(a, b + 1).forEach((r) => { const c = $('.row-select', r); if (c) c.checked = value; });
  } else box.checked = value;
  anchor = row;
  syncBulk();
}
document.addEventListener('click', (event) => {
  const box = event.target.closest('.row-select');
  if (!box) return;
  const row = box.closest('.draft-row');
  if (event.shiftKey) { box.checked = !box.checked; toggleBox(row, true); } else { anchor = row; syncBulk(); }
});

// The row cursor.
let cursor = null;
function setCursor(row) {
  if (cursor) delete cursor.dataset.cursor;
  cursor = row;
  if (row) { row.dataset.cursor = '1'; row.scrollIntoView({ block: 'nearest' }); }
}
function move(step) {
  const rows = visibleRows();
  if (!rows.length) return;
  const at = rows.indexOf(cursor);
  setCursor(rows[at < 0 ? (step > 0 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, at + step))]);
}
const focused = () => cursor || document.activeElement?.closest?.('.draft-row') || null;

// Dialogs: the command palette and the shortcut list.
function dialog(cls, label) {
  const node = el('dialog', { class: cls, 'aria-label': label });
  node.addEventListener('click', (event) => { if (event.target === node) node.close(); });
  document.body.append(node);
  return node;
}
const palette = dialog('palette', 'Command palette');
const palInput = el('input', { type: 'text', role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'pal-list', 'aria-autocomplete': 'list', placeholder: 'Type a command or draft title…', 'aria-label': 'Command' });
const palList = el('ul', { id: 'pal-list', role: 'listbox' });
palette.append(palInput, palList);
let items = [];
let shown = [];
let active = 0;
const go = (url) => () => location.assign(url);
const viewUrl = (key) => $(`.tab[data-view="${key}"]`)?.href || `${base}/dashboard${key === 'active' ? '' : `?view=${key}`}`;
function commands() {
  const out = [];
  const row = focused();
  if (row && !row.matches('.shared')) {
    out.push({ label: `${row.dataset.favorite === '1' ? 'Unstar' : 'Star'} focused draft`, hint: 'F', run: () => toggleFavorite(row.dataset.draftId, row) });
    out.push({ label: `${row.dataset.archived === '1' ? 'Unarchive' : 'Archive'} focused draft`, hint: 'E', run: () => archiveRows([row], row.dataset.archived !== '1') });
  }
  if (row?.dataset.shareUrl) out.push({ label: 'Copy share URL', hint: 'C', run: () => copyUrl(row.dataset.shareUrl) });
  const seen = new Set();
  for (const node of $$('.draft-row, .pin')) {
    const d = node.dataset;
    if (seen.has(d.draftId) || !d.shareUrl) continue;
    seen.add(d.draftId);
    out.push({ label: d.title || 'Untitled', hint: d.detailUrl ? '↵ open · ⇧↵ details' : 'Open',
      run: (shift) => (shift && d.detailUrl ? openDetail(d.detailUrl) : openShare(d.shareUrl)) });
  }
  [['active', 'Active'], ['favorites', 'Favorites'], ['living', 'Living'], ['shared', 'Shared'], ['archived', 'Archived']]
    .forEach(([key, name], i) => out.push({ label: `View: ${name}`, hint: String(i + 1), run: go(viewUrl(key)) }));
  for (const pill of $$('.facets a.pill')) out.push({ label: `${pill.dataset.facet === 'repo' ? 'Repo' : 'Tag'}: ${pill.firstChild.textContent}`, hint: 'Filter', run: go(pill.href) });
  for (const [id, word] of [['dash-sort', 'Sort by'], ['dash-group', 'Group by']]) {
    const select = $(`#${id}`);
    for (const option of select ? select.options : []) {
      out.push({ label: `${word} ${option.textContent}`, hint: word.split(' ')[0], run: () => { select.value = option.value; toolbar.submit(); } });
    }
  }
  for (const theme of ['dark', 'light', 'system']) out.push({ label: `Theme: ${theme[0].toUpperCase()}${theme.slice(1)}`, hint: 'Theme', run: () => setTheme(theme) });
  out.push({ label: 'CLI setup', hint: 'Page', run: go(`${base}/cli/auth`) });
  out.push({ label: 'Keyboard shortcuts', hint: '?', run: () => help.showModal() });
  return out;
}
function renderPalette() {
  const words = palInput.value.toLowerCase().split(/\s+/).filter(Boolean);
  shown = items.filter((item) => words.every((w) => item.label.toLowerCase().includes(w))).slice(0, 60);
  active = Math.min(active, Math.max(0, shown.length - 1));
  palList.replaceChildren(...shown.map((item, i) => {
    const li = el('li', { role: 'option', id: `pal-${i}`, 'aria-selected': String(i === active) }, el('span', {}, item.label), el('small', { class: 'muted' }, item.hint));
    li.addEventListener('click', () => choose(i, false));
    li.addEventListener('mousemove', () => { if (active !== i) { active = i; mark(); } });
    return li;
  }));
  if (!shown.length) palList.append(el('li', { class: 'none', role: 'option', 'aria-disabled': 'true' }, 'No matches'));
  mark();
}
function mark() {
  $$('li[role=option]', palList).forEach((li, i) => li.setAttribute('aria-selected', String(i === active && shown.length > 0)));
  if (shown.length) palInput.setAttribute('aria-activedescendant', `pal-${active}`);
  else palInput.removeAttribute('aria-activedescendant');
  $(`#pal-${active}`)?.scrollIntoView({ block: 'nearest' });
}
function choose(i, shift) { const item = shown[i]; if (!item) return; palette.close(); item.run(shift); }
function openPalette() {
  if (palette.open) return;
  help.close();
  items = commands();
  palInput.value = '';
  active = 0;
  renderPalette();
  palette.showModal();
  palInput.focus();
}
palInput.addEventListener('input', () => { active = 0; renderPalette(); });
palInput.addEventListener('keydown', (event) => {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    if (shown.length) { active = (active + (event.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length; mark(); }
  } else if (event.key === 'Enter') { event.preventDefault(); choose(active, event.shiftKey); }
});

const help = dialog('shortcuts', 'Keyboard shortcuts');
const keys = [[isMac ? '⌘ K' : 'Ctrl K', 'Command palette'], ['/', 'Search'], ['j k or ↓ ↑', 'Move between drafts'],
  ['Enter', 'Open the share URL'], ['o', 'Open details'], ['f', 'Star or unstar'], ['e', 'Archive or unarchive'],
  ['z', 'Undo'], ['c', 'Copy the share URL'], ['x', 'Select'], ['Shift x', 'Select a range'], ['1 – 5', 'Switch view'],
  ['?', 'This list'], ['Esc', 'Close or clear']];
const closeHelp = el('button', { class: 'secondary sm', type: 'button' }, 'Close');
closeHelp.addEventListener('click', () => help.close());
help.append(el('h2', {}, 'Keyboard shortcuts'),
  el('dl', {}, ...keys.flatMap(([k, v]) => [el('dt', {}, el('kbd', {}, k)), el('dd', {}, v)])),
  el('div', { class: 'actions' }, closeHelp));

// Keyboard.
const TYPING = 'textarea,select,[contenteditable=""],[contenteditable=true],input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit])';
document.addEventListener('keydown', (event) => {
  const key = event.key;
  const menu = key === 'Escape' && !$('dialog[open]') && $(MENUS);
  if (menu) {
    event.preventDefault();
    menu.open = false;
    $('summary', menu).focus();
    return;
  }
  if ((event.metaKey || event.ctrlKey) && !event.altKey && key.toLowerCase() === 'k') {
    event.preventDefault();
    if (palette.open) palette.close(); else openPalette();
    return;
  }
  if ($('dialog[open]') || event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
  const target = event.target;
  if (target.closest?.(TYPING)) {
    if (key === 'Escape' && target === search) {
      if (search.value) { search.value = ''; search.dispatchEvent(new Event('input')); } else search.blur();
    }
    return;
  }
  const row = focused();
  const ownedRow = row && !row.matches('.shared') ? row : null;
  const handlers = {
    '/': () => search?.focus(),
    j: () => move(1), ArrowDown: () => move(1), k: () => move(-1), ArrowUp: () => move(-1),
    Enter: () => openShare(row?.dataset.shareUrl),
    o: () => openDetail(row?.dataset.detailUrl),
    f: () => ownedRow && toggleFavorite(ownedRow.dataset.draftId, ownedRow),
    e: () => ownedRow && archiveRows([ownedRow], ownedRow.dataset.archived !== '1'),
    z: runUndo,
    c: () => copyUrl(row?.dataset.shareUrl),
    x: () => toggleBox(ownedRow, false), X: () => toggleBox(ownedRow, true),
    '?': () => help.showModal(),
    Escape: () => { if (selected().length) clearSelection(); else setCursor(null); },
  };
  ['active', 'favorites', 'living', 'shared', 'archived'].forEach((v, i) => { handlers[String(i + 1)] = () => location.assign(viewUrl(v)); });
  const fn = handlers[key];
  if (!fn || (key === 'Enter' && target.closest?.('a,button,summary'))) return;
  if ((key === 'ArrowDown' || key === 'ArrowUp') && !root) return;
  event.preventDefault();
  fn();
});
document.addEventListener('focusin', (event) => {
  const row = event.target.closest?.('.draft-row');
  if (row && row !== cursor) { if (cursor) delete cursor.dataset.cursor; cursor = row; row.dataset.cursor = '1'; }
});
