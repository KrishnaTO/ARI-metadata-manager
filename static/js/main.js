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
  if (!iri || iri === state.activeIri) return;
  selectDisease(iri, { history: false, scroll: true }).catch(() => {
    $('#detail-pane').innerHTML = '<div class="empty-state">That disease link could not be opened.</div>';
  });
}

// Keep the selection in sync with the URL for Back/Forward navigation (popstate)
// and manual edits of the fragment (hashchange). selectDisease uses history:false
// here so it doesn't push a duplicate entry back onto the stack.
function navigateToHash(){ openDiseaseFromHash(); }
window.addEventListener('popstate', navigateToHash);
window.addEventListener('hashchange', navigateToHash);

// ----------------------------------------------------------------- THEME
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('ari-theme', theme); } catch (e) {}
}
function initTheme() {
  const saved = (() => { try { return localStorage.getItem('ari-theme'); } catch (e) { return ''; } })();
  const pref = saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  applyTheme(pref);
}
document.getElementById('theme-toggle')?.addEventListener('click', () => {
  const cur = document.documentElement.getAttribute('data-theme');
  applyTheme(cur === 'dark' ? 'light' : 'dark');
});
initTheme();

init();
