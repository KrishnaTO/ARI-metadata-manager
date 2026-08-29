// Left-panel navigation: the three tree views (alphabetical / tissue / symptoms),
// tab switching, tree-click handling, and header search.

// The row toggle. Leaf rows render an empty twisty so every label starts on the
// same x. When `subtype` is set (a disease that has child subtypes in the
// alphabetical hierarchy) the marker is accent-coloured so the "this disease has
// subtypes" affordance stands out from plain grouping rows.
function twisty(collapsed, leaf, subtype){
  if (leaf) return `<span class="twisty leaf"></span>`;
  return `<span class="twisty${subtype ? ' subtype' : ''}" title="${subtype ? 'Toggle subtypes' : ''}">${collapsed ? '▶' : '▼'}</span>`;
}

// Confirmed autoimmune diseases (diseaseCategory "Autoimmune") get a subtle
// bold + colour shift on their row so they stand out from unconfirmed entries
// in the hierarchical list, without adding icon clutter (issue #21).
function autoimmuneClass(d){
  return d && d.autoimmune ? ' autoimmune' : '';
}

// The bottom-of-list "Only" toggle restricts both tree views to confirmed
// autoimmune diseases. Grouping rows (alphabetical parents, tissue classes) are
// kept when they still contain a match, so a confirmed subtype under an
// unconfirmed parent stays reachable.
function filterAlphabetical(nodes){
  const out = [];
  for (const n of nodes){
    const children = filterAlphabetical(n.children || []);
    if (n.autoimmune || children.length) out.push({ ...n, children });
  }
  return out;
}

function filterTissue(nodes){
  const out = [];
  for (const n of nodes){
    const children = filterTissue(n.children || []);
    const diseases = (n.diseases || []).filter(d => d.autoimmune);
    if (children.length || diseases.length) out.push({ ...n, children, diseases });
  }
  return out;
}

function renderAlphabetical(){
  showLoading('#tree-pane');
  api('/api/v2/tree/alphabetical').then(all => {
    const roots = state.autoimmuneOnly ? filterAlphabetical(all) : all;
    const node = (n) => {
      const kids = n.children || [];
      const hasKids = kids.length > 0;
      const sel = state.activeIri === n.iri ? ' selected' : '';
      const obs = n.obsolete ? ' obsolete' : '';
      const obsTag = n.obsolete ? ' <span class="obsolete-tag">(obsolete)</span>' : '';
      let h = `<div class="node${hasKids ? ' collapsed' : ''}">`;
      h += `<div class="node-row disease-row${sel}${obs}${autoimmuneClass(n)}" data-iri="${esc(n.iri)}"
             role="treeitem" tabindex="-1" aria-selected="${sel ? 'true' : 'false'}"
             ${hasKids ? 'aria-expanded="false"' : ''}>${twisty(true, !hasKids, hasKids)}<span class="nm">${esc(n.name)}</span>${obsTag}</div>`;
      if (hasKids){ h += `<div class="children">${kids.map(node).join('')}</div>`; }
      h += `</div>`;
      return h;
    };
    $('#tree-pane').innerHTML = roots.length ? roots.map(node).join('')
      : `<div class="empty-state">${state.autoimmuneOnly ? 'No confirmed autoimmune diseases.' : 'No diseases.'}</div>`;
    primeTreeTabStop();
  }).catch(() => $('#tree-pane').innerHTML = '<div class="empty-state">Error loading list.</div>');
}

function renderTissue(){
  showLoading('#tree-pane');
  api('/api/v2/tree/tissue').then(all => {
    const tree = state.autoimmuneOnly ? filterTissue(all) : all;
    const node = (n) => {
      const subClasses = n.children || [];
      const diseases = n.diseases || [];
      const hasKids = subClasses.length > 0 || diseases.length > 0;
      const ari = n.ari_id ? `<span class="ari-chip">${esc(n.ari_id)}</span>` : '';
      let h = `<div class="node">`;
      h += `<div class="node-row tissue" data-toggle="1" role="treeitem" tabindex="-1"
             ${hasKids ? 'aria-expanded="false"' : ''}>${twisty(false, !hasKids)}<span class="nm">${esc(n.name)}</span>${ari}</div>`;
      if (hasKids){
        h += `<div class="children">`;
        h += subClasses.map(node).join('');
        for (const d of diseases){
          const sel = state.activeIri === d.iri ? ' selected' : '';
          const obs = d.obsolete ? ' obsolete' : '';
          const obsTag = d.obsolete ? ' <span class="obsolete-tag">(obsolete)</span>' : '';
          h += `<div class="node"><div class="node-row disease-row${sel}${obs}${autoimmuneClass(d)}" data-iri="${esc(d.iri)}"
             role="treeitem" tabindex="-1" aria-selected="${sel ? 'true' : 'false'}">${twisty(true, true)}<span class="nm">${esc(d.name)}</span>${obsTag}</div></div>`;
        }
        h += `</div>`;
      }
      return h;
    };
    $('#tree-pane').innerHTML = tree.length ? tree.map(node).join('')
      : `<div class="empty-state">${state.autoimmuneOnly ? 'No confirmed autoimmune diseases.' : 'No tissue hierarchy.'}</div>`;
    primeTreeTabStop();
  }).catch(() => $('#tree-pane').innerHTML = '<div class="empty-state">No tissue hierarchy available.</div>');
}

function renderSymptomsView(){
  showLoading('#tree-pane');
  api('/api/v2/symptoms').then(list => {
    if (!list.length){ $('#tree-pane').innerHTML = '<div class="empty-state">No symptoms in dataset.</div>'; return; }
    let html = '<div style="padding:4px">';
    html += `<div class="pane-note">${list.length} symptoms across diseases</div>`;
    for (const s of list){
      const lik = (s.likelihood || '').toLowerCase();
      let badge = 'badge-moderate';
      if (lik.includes('very common') || lik.includes('common')) badge = 'badge-common';
      if (lik.includes('rare') || lik.includes('weak')) badge = 'badge-rare';
      const obs = s.obsolete ? ' style="opacity:.5"' : '';
      const owner = s.diseases?.[0];
      html += `<div class="node-row" data-symptom-owner="${esc(s.diseases?.length ? owner : '')}"${obs} title="${esc((s.diseases||[]).join(', '))}">`;
      html += `<span class="badge ${badge}">${esc(s.likelihood || '')}</span> <span class="nm">${esc(s.name)}</span></div>`;
    }
    html += '</div>';
    $('#tree-pane').innerHTML = html;
  }).catch(() => $('#tree-pane').innerHTML = '<div class="empty-state">Error loading symptoms.</div>');
}

function renderTab(){
  if (state.activeTab === 'alphabetical') renderAlphabetical();
  else if (state.activeTab === 'tissue') renderTissue();
  else if (state.activeTab === 'symptoms') renderSymptomsView();
}

// Expand any collapsed ancestor rows of a disease and scroll it into view.
// Returns false when the row isn't in the current tree (still loading, or the
// disease lives only under a different tab) so the caller can retry.
function revealTreeRow(iri){
  if (!iri) return false;
  const pane = $('#tree-pane');
  const row = pane.querySelector(`[data-iri="${CSS.escape(iri)}"]`);
  if (!row) return false;
  for (let el = row.parentElement; el && el !== pane; el = el.parentElement){
    if (el.classList.contains('node')) el.classList.remove('collapsed');
  }
  row.scrollIntoView({ block: 'center' });
  return true;
}

// The tree list renders asynchronously on first paint, so a disease opened from
// a URL may not have its row yet. Poll briefly until it appears, then scroll.
function scrollTreeToActive(attempts){
  attempts = attempts == null ? 25 : attempts;
  if (revealTreeRow(state.activeIri) || attempts <= 0) return;
  setTimeout(() => scrollTreeToActive(attempts - 1), 100);
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t === tab));
    state.activeTab = tab.dataset.view;
    renderTab();
  });
});

$('#ai-only').addEventListener('change', e => {
  state.autoimmuneOnly = e.target.checked;
  renderTab();
});

// Tree interactions: twisty toggles, disease rows select.
$('#tree-pane').addEventListener('click', e => {
  const tw = e.target.closest('.twisty');
  if (tw && !tw.classList.contains('leaf')){
    const nodeEl = tw.closest('.node');
    if (nodeEl) nodeEl.classList.toggle('collapsed');
    // Keep the subtype box marker in sync with expanded/collapsed state.
    if (tw.classList.contains('subtype')) tw.textContent = nodeEl?.classList.contains('collapsed') ? '▶' : '▼';
    e.stopPropagation();
    return;
  }
  const tissue = e.target.closest('[data-toggle]');
  if (tissue){
    const nodeEl = tissue.closest('.node');
    if (nodeEl) nodeEl.classList.toggle('collapsed');
    return;
  }
  const dis = e.target.closest('[data-iri]');
  if (dis){ selectDisease(dis.dataset.iri); return; }
  const sym = e.target.closest('[data-symptom-owner]');
  if (sym && sym.dataset.symptomOwner){ selectDisease(sym.dataset.symptomOwner); }
});

// ----------------------------------------------------------------- SEARCH
// Sub-label for a result: the match reason (synonym/tissue) when the hit wasn't
// on the name, otherwise the local ontology id.
function searchSub(r){
  return (r.match && r.match !== 'name') ? r.match : r.local_name;
}
let searchTimer;
$('#search').addEventListener('input', e => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  if (!q){ $('#search-results').classList.add('hidden'); return; }
  searchTimer = setTimeout(async () => {
    const rs = await api('/api/v2/search?q=' + encodeURIComponent(q));
    if (!rs.length){ $('#search-results').innerHTML = '<div class="node-row pane-note">No matches</div>'; }
    else {
      let html = rs.slice(0, 12).map(r =>
        `<div class="node-row${r.obsolete?' obsolete':''}" data-iri="${esc(r.iri)}">${esc(r.name)} <span class="sub">${esc(searchSub(r))}</span></div>`
      ).join('');
      // Footer option: open the full results page in the middle pane.
      html += `<div class="node-row search-all-row" data-search-all="${esc(q)}">View all ${rs.length} result${rs.length===1?'':'s'} for &ldquo;${esc(q)}&rdquo;</div>`;
      $('#search-results').innerHTML = html;
    }
    $('#search-results').classList.remove('hidden');
  }, 200);
});
$('#search-results').addEventListener('click', e => {
  const all = e.target.closest('[data-search-all]');
  if (all){ showSearchResultsPage(all.dataset.searchAll); $('#search-results').classList.add('hidden'); return; }
  const r = e.target.closest('[data-iri]');
  if (r){ selectDisease(r.dataset.iri); $('#search-results').classList.add('hidden'); $('#search').value = ''; }
});
$('#search').addEventListener('keydown', e => {
  if (e.key === 'Enter'){ const q = e.target.value.trim(); if (q){ showSearchResultsPage(q); $('#search-results').classList.add('hidden'); } }
});
$('#search').addEventListener('blur', () => setTimeout(() => $('#search-results').classList.add('hidden'), 200));

// Full search-results page rendered in the middle pane.
async function showSearchResultsPage(q){
  state.activeIri = null;
  closeRightPanel();
  $('#tree-pane').querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
  showLoading('#detail-pane');
  let rs;
  try { rs = await api('/api/v2/search?q=' + encodeURIComponent(q)); }
  catch(err){ $('#detail-pane').innerHTML = '<div class="empty-state">Search failed.</div>'; return; }
  const diseases = rs.filter(r => r.is_disease);
  const others = rs.filter(r => !r.is_disease);
  let html = `<div class="search-page"><h1>Search results</h1>`+
    `<div class="search-page-sub">${rs.length} match${rs.length===1?'':'es'} for &ldquo;<strong>${esc(q)}</strong>&rdquo;</div>`;
  if (!rs.length){ html += '<div class="empty-state">No matches found.</div>'; }
  const section = (title, rows) => {
    if (!rows.length) return '';
    let h = `<div class="section-label">${title} (${rows.length})</div><div class="search-results-list">`;
    for (const r of rows){
      h += `<div class="search-result-row${r.obsolete?' obsolete':''}" data-iri="${esc(r.iri)}">`+
        `<span class="srr-name">${esc(r.name)}${r.obsolete?' <span class="obsolete-tag">(obsolete)</span>':''}</span>`+
        `<span class="srr-sub">${esc(searchSub(r))}</span></div>`;
    }
    return h + '</div>';
  };
  html += section('Diseases', diseases);
  html += section('Other matches', others);
  html += '</div>';
  $('#detail-pane').innerHTML = html;
  // The results page replaces the record, so the deep-dive container goes back
  // to the shell rather than being discarded with it.
  mountDeepDive();
  $('#detail-pane').querySelectorAll('[data-iri]').forEach(row =>
    row.addEventListener('click', () => selectDisease(row.dataset.iri)));
}

// ------------------------------------------------------------------ KEYBOARD
// The disease index was plain divs with click handlers: no tabindex, no role,
// no focus ring, so it could not be reached without a mouse and screen readers
// saw unlabelled containers. One roving tabindex over the visible rows makes
// the whole rail keyboard-operable (issue #100).

function visibleTreeRows(){
  return [...$('#tree-pane').querySelectorAll('.node-row')]
    .filter(el => el.offsetParent !== null);   // collapsed branches are not reachable
}

function focusTreeRow(row){
  if (!row) return;
  visibleTreeRows().forEach(el => el.tabIndex = -1);
  row.tabIndex = 0;
  row.focus();
}

// Keep exactly one row in the tab order as the tree re-renders.
function primeTreeTabStop(){
  const rows = visibleTreeRows();
  if (!rows.length) return;
  const current = rows.find(el => el.tabIndex === 0) ||
                  rows.find(el => el.classList.contains('selected')) || rows[0];
  rows.forEach(el => el.tabIndex = el === current ? 0 : -1);
}

$('#tree-pane').addEventListener('keydown', e => {
  const row = e.target.closest('.node-row');
  if (!row) return;
  const rows = visibleTreeRows();
  const i = rows.indexOf(row);
  const twist = row.querySelector('.twisty');
  const node = row.closest('.node');

  switch (e.key) {
    case 'ArrowDown': e.preventDefault(); focusTreeRow(rows[i + 1]); break;
    case 'ArrowUp':   e.preventDefault(); focusTreeRow(rows[i - 1]); break;
    case 'Home':      e.preventDefault(); focusTreeRow(rows[0]); break;
    case 'End':       e.preventDefault(); focusTreeRow(rows[rows.length - 1]); break;
    case 'ArrowRight':
      if (node && node.classList.contains('collapsed')){ e.preventDefault(); twist?.click(); }
      break;
    case 'ArrowLeft':
      if (node && !node.classList.contains('collapsed') && twist){ e.preventDefault(); twist.click(); }
      else { e.preventDefault(); focusTreeRow(rows[i - 1]); }
      break;
    case 'Enter':
    case ' ':
      e.preventDefault();
      row.click();
      break;
    default: return;
  }
});

$('#tree-pane').addEventListener('focusin', e => {
  const row = e.target.closest('.node-row');
  if (row) visibleTreeRows().forEach(el => el.tabIndex = el === row ? 0 : -1);
});

// ---- search suggestions: arrow keys + Enter, with aria-activedescendant ----
let _searchActive = -1;

function searchOptions(){
  return [...$('#search-results').querySelectorAll('.node-row')];
}

function highlightSearchOption(n){
  const opts = searchOptions();
  if (!opts.length) return;
  _searchActive = (n + opts.length) % opts.length;
  opts.forEach((el, i) => {
    const on = i === _searchActive;
    el.classList.toggle('kb-active', on);
    if (on){
      el.id = el.id || 'search-opt-' + i;
      $('#search').setAttribute('aria-activedescendant', el.id);
      el.scrollIntoView({ block: 'nearest' });
    }
  });
}

$('#search').addEventListener('keydown', e => {
  const opts = searchOptions();
  const open = !$('#search-results').classList.contains('hidden') && opts.length;
  if (e.key === 'ArrowDown' && open){ e.preventDefault(); highlightSearchOption(_searchActive + 1); return; }
  if (e.key === 'ArrowUp' && open){ e.preventDefault(); highlightSearchOption(_searchActive - 1); return; }
  if (e.key === 'Escape'){ $('#search-results').classList.add('hidden'); _searchActive = -1; return; }
  // Enter on a highlighted suggestion opens THAT disease. Enter with nothing
  // highlighted keeps the old behaviour of opening the full results page —
  // which was previously the only thing the keyboard could do here at all.
  if (e.key === 'Enter' && open && _searchActive >= 0){
    e.preventDefault();
    opts[_searchActive].click();
    _searchActive = -1;
  }
});
