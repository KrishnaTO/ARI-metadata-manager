// Bootstrap: load overview + the editable-field schema, then render the
// active tree. Runs last so every module's functions and wiring are ready.

async function init(){
  const o = await api('/api/v2/overview');
  $('#onto-meta').innerHTML =
    `Manager <b>${esc(o.app_version || '')}</b> &middot; <b>${esc(o.disease_count)}</b> disease(s) &middot; <b>${o.individuals}</b> individuals &middot; <b>${o.classes}</b> classes &middot; data v<b>${esc(o.version)}</b>`;
  if (!Object.keys(state.schema).length){ try { state.schema = await api('/api/v2/schema'); } catch(e){} }
  if (!Object.keys(XREF_DB).length){ try { setXrefDatabases(await api('/api/v2/xref-databases')); } catch(e){} }
  renderTab();
  // Open the disease named directly in the URL (a shared/bookmarked deep link)
  // and scroll the list to it once the tree has rendered.
  openDiseaseFromHash();
}

// Load the disease named in the URL fragment (if any). A stale or malformed link
// shouldn't throw — show a friendly note in the detail pane instead.
function openDiseaseFromHash(){
  const iri = diseaseHashIri();
  // No disease in the URL: the start state, so Back out of the first record
  // lands somewhere coherent instead of leaving it on screen.
  if (!iri){ if (state.activeIri) showStartPage(); return; }
  if (iri === state.activeIri) return;
  selectDisease(iri, { history: false, scroll: true }).catch(() => {
    $('#detail-pane').innerHTML = '<div class="empty-state">That disease link could not be opened.</div>';
  });
}

// The wordmark returns to the start state without reloading the app.
document.querySelector('.brand')?.addEventListener('click', e => {
  e.preventDefault();
  if (window.location.hash) history.pushState(null, '', window.location.pathname);
  showStartPage();
});

// Keep the selection in sync with the URL for Back/Forward navigation (popstate)
// and manual edits of the fragment (hashchange). selectDisease uses history:false
// here so it doesn't push a duplicate entry back onto the stack.
function navigateToHash(){ openDiseaseFromHash(); }
window.addEventListener('popstate', navigateToHash);
window.addEventListener('hashchange', navigateToHash);

// ----------------------------------------------------------------- THEME
// Theme is a set-and-forget preference, so it lives in the preferences popover rather
// than paying permanent toolbar rent.
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('ari-theme', theme); } catch (e) {}
  document.querySelectorAll('#theme-seg button').forEach(b => b.classList.toggle('on', b.dataset.theme === theme));
}
function initTheme() {
  const saved = (() => { try { return localStorage.getItem('ari-theme'); } catch (e) { return ''; } })();
  applyTheme(saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
}
document.getElementById('theme-seg')?.addEventListener('click', e => {
  const b = e.target.closest('button'); if (b) applyTheme(b.dataset.theme);
});
initTheme();

document.getElementById('help-btn')?.addEventListener('click', () => Help.open());

// ------------------------------------------------------------ TOOLBAR MENU
// The preferences popover closes on an outside click and on Escape, keeping the button's
// aria-expanded in step. A click inside adjusts a preference, so it stays open;
// the one-shot items close it through their own handlers.
(function () {
  const btn = document.getElementById('settings-btn');
  const menu = document.getElementById('app-menu');
  if (!btn || !menu) return;
  window.closeAppMenu = () => { menu.classList.remove('open'); btn.setAttribute('aria-expanded', 'false'); };
  btn.addEventListener('click', e => {
    e.stopPropagation();
    const open = !menu.classList.contains('open');
    menu.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', String(open));
  });
  menu.addEventListener('click', e => {
    e.stopPropagation();
    // Preference rows (theme) stay open; one-shot actions close the popover.
    if (e.target.closest('.menu-item')) closeAppMenu();
  });
  document.addEventListener('click', () => closeAppMenu());
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeAppMenu(); });
})();

// ---------------------------------------------------------------- RAIL DRAWER
// Below 1200px the index rail is an overlay; the header's hamburger opens it and
// picking a disease (or Escape, or the scrim) closes it again.
(function () {
  const btn = document.getElementById('rail-toggle');
  if (!btn) return;
  const close = () => document.body.classList.remove('rail-open');
  btn.addEventListener('click', e => {
    e.stopPropagation();
    document.body.classList.toggle('rail-open');
  });
  document.getElementById('tree-pane').addEventListener('click', e => {
    if (e.target.closest('[data-iri], [data-symptom-owner]')) close();
  });
  document.addEventListener('click', e => { if (!e.target.closest('.left-col')) close(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
})();

// Below 1024px the deep dive is a drawer over the record, so it dismisses like
// one: a click on the scrim or Escape closes it. Above that it is an inline
// expansion and stays put until its own Close button is used.
(function () {
  const isDrawer = () => window.matchMedia('(max-width: 1024px)').matches;
  const isOpen = () => document.getElementById('right-col').classList.contains('open');
  // The record's own actions (Edit record, Copy link) and the category lines are
  // not "outside" — they drive the panel rather than dismissing it.
  document.addEventListener('click', e => {
    if (isDrawer() && isOpen() && !e.target.closest('#right-col, .box, .rec-actions')) requestCloseDeepDive();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && isDrawer() && isOpen()) requestCloseDeepDive();
  });
})();

init();
