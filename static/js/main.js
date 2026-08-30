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
  // No disease in the URL: the start state. Also the first thing drawn on load,
  // so the pane is never empty and never points at a list that may be hidden.
  if (!iri){ showStartPage(); return; }
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

// ------------------------------------------------------------- TEXT SIZE
// One key, shared with the cross-reference matrix, so the choice is made once.
// js/boot.js applies it before the first paint; this keeps the control in step.
function applyTextSize(size) {
  document.documentElement.dataset.textsize = size;
  try { localStorage.setItem('ari-textsize', size); } catch (e) { /* storage may be unavailable */ }
  document.querySelectorAll('#textsize-seg button')
    .forEach(b => b.classList.toggle('on', b.dataset.textsize === size));
}
applyTextSize(document.documentElement.dataset.textsize || 'standard');
document.getElementById('textsize-seg')?.addEventListener('click', e => {
  const b = e.target.closest('button'); if (b) applyTextSize(b.dataset.textsize);
});

// The symptom word cloud repeats the list directly above it, at sizes that encode
// only what the Likelihood column already says, for about a third of the panel's
// height. Off unless asked for (issue #101).
(function () {
  const box = document.getElementById('pref-wordcloud');
  if (!box) return;
  box.checked = wordCloudOn();
  box.addEventListener('change', () => {
    try { localStorage.setItem('ari-wordcloud', box.checked ? 'on' : 'off'); } catch (e) { /* ignore */ }
    // Redraw an open Symptoms panel so the change is visible immediately.
    if (state.activeBox === 'symptoms' && state.detail) openBoxDetail(state.detail, 'symptoms');
  });
})();

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

// ---------------------------------------------------------------- INDEX RAIL
// The rail collapses by choice, not by viewport width. It used to disappear the
// moment the window fell under 1200px — which is where a 1366px laptop lands as
// soon as the reader raises the text size, so making the app readable was also
// what took the navigation away (issue #94).
//
// Two modes, one button. Docked (above 1200px) it pins or collapses the rail, and
// the choice is remembered. Overlay (below 1200px, where there is no room to dock
// it) it opens and closes the rail over the record, and picking a disease,
// clicking away or Escape closes it again.
(function () {
  const btn = document.getElementById('rail-toggle');
  if (!btn) return;
  const KEY = 'ari-rail';
  const overlay = () => window.matchMedia('(max-width: 1200px)').matches;
  const shown = () => overlay()
    ? document.body.classList.contains('rail-open')
    : !document.body.classList.contains('rail-hidden');

  function sync() {
    const on = shown();
    const label = on ? 'Hide the disease index' : 'Show the disease index';
    btn.setAttribute('aria-expanded', String(on));
    btn.setAttribute('aria-label', label);
    btn.title = label;
  }

  function setRail(show) {
    if (overlay()) {
      document.body.classList.toggle('rail-open', show);
    } else {
      document.body.classList.toggle('rail-hidden', !show);
      try { localStorage.setItem(KEY, show ? 'pinned' : 'hidden'); } catch (e) { /* storage may be unavailable */ }
    }
    sync();
  }

  // The start state's "Open the disease index" button, which is the way back to
  // the list when the rail is collapsed or off-screen.
  window.showDiseaseIndex = () => {
    setRail(true);
    document.querySelector('.left-col .tab')?.focus();
  };

  try { if (localStorage.getItem(KEY) === 'hidden') document.body.classList.add('rail-hidden'); }
  catch (e) { /* storage may be unavailable */ }
  sync();
  window.addEventListener('resize', sync);

  btn.addEventListener('click', e => { e.stopPropagation(); setRail(!shown()); });
  // Only the overlay dismisses itself; a docked rail stays where it was put.
  document.getElementById('tree-pane').addEventListener('click', e => {
    if (overlay() && e.target.closest('[data-iri], [data-symptom-owner]')) setRail(false);
  });
  document.addEventListener('click', e => {
    if (overlay() && !e.target.closest('.left-col, #rail-toggle')) setRail(false);
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && overlay()) setRail(false); });
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
