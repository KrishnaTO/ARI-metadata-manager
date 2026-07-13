// Disease curator (/ref-curate): a disease-centric companion to the ref-edits
// matrix. Pick one disease from the left; the center stacks every review
// database as a card showing existing ids, previously-curated judgments and
// exact-match predictions. Review each id (correct / needs-change), add missing
// ids and Save, preview the source page on the right, then Publish a PR scoped
// to the diseases you touched this session.
//
// State model, keys and the /api/v2 contract (xrefs, predictions, mappings,
// disease PUT, publish) are deliberately identical to ref-edits.js so the two
// pages write the same SSSOM + equivalency files and can be used interchangeably.
(function () {
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = id => String(id).replace(/^[A-Za-z]+:/, '');
  const enc = encodeURIComponent;
  const apiUrl = p => new URL('../api/v2/' + p, location.href).href;
  async function api(p, opts = {}) {
    if (opts.body) { opts.headers = { 'content-type': 'application/json' }; opts.body = JSON.stringify(opts.body); }
    const r = await fetch(apiUrl(p), opts);
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || r.statusText); }
    return r.json();
  }

  // Database columns are built from the shared registry (/api/v2/xref-databases,
  // app/xref_registry.py) — same single source of truth the ref-edits page uses.
  let DBS = [], DBMAP = {}, PREFIX = {};
  function buildDatabases(list) {
    const fillId = (t, id) => t.replace('{num}', num(id)).replace('{id}', enc(id));
    const fillName = (t, n) => t.replace('{name}', enc(n));
    DBS = (list || []).filter(d => d.review).map(d => ({
      key: d.key, label: d.label, noframe: d.noframe,
      link: id => fillId(d.link, id),
      search: n => fillName(d.search, n),
    }));
    DBMAP = Object.fromEntries(DBS.map(d => [d.key, d]));
    PREFIX = Object.fromEntries((list || []).map(d => [d.key, d.prefix]));
  }

  let ROWS = [], me = null, reviewed = {}, edited = {}, sessionBranch = null;
  let currentIri = null, active = null;   // active = {db, id} currently previewed
  // reviewed/edited keys: `${iri}|${db}|${id}` (per-ID, matches ref-edits.js)
  const idKey = (iri, db, id) => iri + '|' + db + '|' + id;
  // Pre-existing curated judgments keyed `${ari_id}|${prefix}|${id}` -> 'positive'|'negative'.
  let mappings = {};
  // Predicted matches (issue #42) keyed `${ari_id}|${prefix}|${id}` -> {label, match_field, confidence}.
  let predicted = {};

  const $ = s => document.querySelector(s);

  // ---- shared judgment helpers (mirror ref-edits.js) ----
  function predFor(r, dbkey) {
    const ari = r.ari_id, prefix = PREFIX[dbkey];
    if (!ari || !prefix || (r[dbkey] || []).length) return [];
    const out = [];
    for (const [k, meta] of Object.entries(predicted)) {
      const [a, p, id] = k.split('|');
      if (a === ari && p === prefix && mappings[k] !== 'negative')
        out.push({ id, label: meta.label, match_field: meta.match_field, confidence: meta.confidence });
    }
    return out;
  }
  function preJudgmentId(r, dbkey, id) {
    const ari = r.ari_id, prefix = PREFIX[dbkey];
    if (!ari || !prefix) return null;
    const j = mappings[ari + '|' + prefix + '|' + id];
    return j === 'positive' ? 'pos' : j === 'negative' ? 'neg' : null;
  }

  // ---- publish payload assembly (identical shape to ref-edits.js) ----
  function reviewedCells(verdict) {
    const cellMap = {};
    for (const [k, v] of Object.entries(reviewed)) {
      if (v !== verdict) continue;
      const [iri, db, id] = k.split('|');
      const ck = iri + '|' + db;
      if (!cellMap[ck]) {
        const r = ROWS.find(x => x.iri === iri);
        cellMap[ck] = { ari_id: r ? r.ari_id : null, iri, name: r ? r.name : null, db, ids: [] };
      }
      cellMap[ck].ids.push(id);
    }
    return Object.values(cellMap).filter(c => c.ids.length > 0);
  }
  const confirmedList = () => reviewedCells('ok');
  const flaggedList = () => reviewedCells('bad');

  function reviewMessage() {
    const iris = new Set();
    Object.keys(edited).forEach(k => iris.add(k.split('|')[0]));
    for (const [k, v] of Object.entries(reviewed)) if (v === 'ok') iris.add(k.split('|')[0]);
    const ari = [...iris].map(i => (ROWS.find(x => x.iri === i) || {}).ari_id).filter(Boolean).sort();
    let lab = ari.slice(0, 6).join(', ');
    if (ari.length > 6) lab += ', +' + (ari.length - 6) + ' more';
    return '[' + (lab || 'cross-references') + '] - mappings review';
  }

  function counts() {
    const ok = Object.values(reviewed).filter(v => v === 'ok').length;
    const bad = Object.values(reviewed).filter(v => v === 'bad').length;
    const ed = Object.keys(edited).length;
    $('#counts').textContent = `confirmed ${ok} · flagged ${bad} · edited ${ed}`;
    $('#publish').disabled = !(me && me.authenticated && (ed > 0 || ok > 0 || bad > 0));
  }

  // Per-disease curation progress for the left-list dot: how many review DBs
  // have at least one judged/edited/pre-judged id vs. how many have any id.
  function diseaseProgress(r) {
    let withIds = 0, touched = 0;
    for (const db of DBS) {
      const ids = r[db.key] || [];
      if (!ids.length) continue;
      withIds++;
      const done = ids.some(id => {
        const k = idKey(r.iri, db.key, id);
        return reviewed[k] || edited[k] || preJudgmentId(r, db.key, id);
      });
      if (done) touched++;
    }
    return { withIds, touched };
  }

  // -------------------------------------------------- LEFT: disease picker
  function renderPicker(filter) {
    const q = (filter || '').trim().toLowerCase();
    const rows = ROWS.filter(r => !q || (r.name || '').toLowerCase().includes(q) ||
      (r.synonyms || []).some(s => String(s).toLowerCase().includes(q)) ||
      DBS.some(db => (r[db.key] || []).some(id => String(id).toLowerCase().includes(q))));
    if (!rows.length) { $('#pick-list').innerHTML = '<p class="muted" style="padding:16px">No matches.</p>'; return; }
    $('#pick-list').innerHTML = rows.map(r => {
      const p = diseaseProgress(r);
      const dot = p.withIds && p.touched >= p.withIds ? '<span class="pr-dot done" title="all id-bearing databases reviewed"></span>'
                : p.touched ? '<span class="pr-dot part" title="partially reviewed"></span>' : '';
      const nIds = DBS.reduce((n, db) => n + (r[db.key] || []).length, 0);
      const meta = nIds ? `${nIds} id${nIds > 1 ? 's' : ''} across ${p.withIds} database${p.withIds > 1 ? 's' : ''}` : 'no cross-references yet';
      return `<div class="pick-row${r.iri === currentIri ? ' active' : ''}" data-iri="${esc(r.iri)}">
        <div class="pr-name">${esc(r.name)}${dot}</div><div class="pr-meta">${esc(meta)}</div></div>`;
    }).join('');
    $('#pick-list').querySelectorAll('.pick-row').forEach(el =>
      el.addEventListener('click', () => selectDisease(el.dataset.iri)));
  }

  // -------------------------------------------------- CENTER: one disease
  function selectDisease(iri) {
    currentIri = iri;
    renderPicker($('#filter').value);
    renderWork();
  }

  function idRowHtml(r, db, id) {
    const k = idKey(r.iri, db.key, id);
    const pre = preJudgmentId(r, db.key, id);
    const cls = (reviewed[k] === 'ok' ? 'ok' : reviewed[k] === 'bad' ? 'bad'
              : pre === 'pos' ? 'prepos' : pre === 'neg' ? 'preneg' : '')
              + (edited[k] ? ' edited' : '');
    const activeCode = active && active.db === db.key && String(active.id) === String(id) ? ' active' : '';
    return `<div class="id-row ${cls}" data-db="${db.key}" data-id="${esc(id)}">
      <span class="id-code${activeCode}" data-preview>${esc(id)}</span>
      <span class="spacer"></span>
      <button class="btn ok mini ${reviewed[k] === 'ok' ? 'on' : ''}" data-verdict="ok">✓ Correct</button>
      <button class="btn bad mini ${reviewed[k] === 'bad' ? 'on' : ''}" data-verdict="bad">✗ Needs change</button>
      <a class="link-out" href="${esc(db.link(id))}" target="_blank" rel="noopener" title="Open in ${esc(db.label)}">↗</a>
    </div>`;
  }

  function predRowHtml(r, db, p) {
    const lc = p.confidence === 'low' ? ' low' : '';
    const tag = p.confidence === 'low' ? 'predicted (synonym)' : 'predicted';
    return `<div class="id-row predicted${lc}" data-db="${db.key}" data-pred-id="${esc(p.id)}">
      <span class="id-code" data-preview-pred>${esc(p.id)}</span>
      ${p.label ? `<span class="id-name">${esc(p.label)}</span>` : ''}
      <span class="id-pred-tag">${tag}</span>
      <span class="spacer"></span>
      <button class="btn ok mini" data-accept>✓ Verify &amp; add</button>
      <a class="link-out" href="${esc(db.link(p.id))}" target="_blank" rel="noopener" title="Open in ${esc(db.label)}">↗</a>
    </div>`;
  }

  function renderWork() {
    const r = ROWS.find(x => x.iri === currentIri);
    if (!r) { $('#work').innerHTML = '<div class="empty">Select a disease on the left to curate its cross-references.</div>'; return; }
    const syns = (r.synonyms || []).filter(Boolean);
    const prog = diseaseProgress(r);
    let h = `<div class="dz-head">
      <h2>${esc(r.name)}</h2>
      <div class="muted">${r.ari_id ? esc(r.ari_id) + ' · ' : ''}${DBS.length} review databases</div>
      ${syns.length ? `<div class="dz-syns">${syns.map(s => `<span class="dz-syn">${esc(s)}</span>`).join('')}</div>` : ''}
      <div class="progress">${prog.touched}/${prog.withIds} id-bearing database${prog.withIds === 1 ? '' : 's'} reviewed</div>
    </div>`;

    for (const db of DBS) {
      const ids = r[db.key] || [];
      const preds = ids.length ? [] : predFor(r, db.key);
      let status = '';
      if (ids.length) {
        const okN = ids.filter(id => reviewed[idKey(r.iri, db.key, id)] === 'ok').length;
        const badN = ids.filter(id => reviewed[idKey(r.iri, db.key, id)] === 'bad').length;
        status = `${ids.length} id${ids.length > 1 ? 's' : ''}${okN ? ` · ${okN}✓` : ''}${badN ? ` · ${badN}✗` : ''}`;
      } else if (preds.length) status = `${preds.length} predicted`;
      else status = 'empty';

      let body;
      if (ids.length) {
        body = ids.map(id => idRowHtml(r, db, id)).join('');
      } else if (preds.length) {
        body = preds.map(p => predRowHtml(r, db, p)).join('');
      } else {
        body = `<div class="db-empty">No ${esc(db.label)} id yet — <a href="${esc(db.search(r.name))}" target="_blank" rel="noopener">search ${esc(db.label)} for "${esc(r.name)}" ↗</a>, then add it below.</div>`;
      }
      h += `<div class="db-card" data-db="${db.key}">
        <div class="db-top"><span class="db-label">${esc(db.label)}</span><span class="db-status">${status}</span></div>
        <div class="db-body">
          ${body}
          <div class="db-add">
            <input placeholder="Add ${esc(db.label)} id(s), comma separated" data-add-input>
            <button class="btn primary mini" data-add-save>Save</button>
          </div>
        </div>
      </div>`;
    }
    $('#work').innerHTML = h;
    wireWork(r);
  }

  function wireWork(r) {
    // Review verdict buttons on existing ids.
    $('#work').querySelectorAll('.id-row[data-id]').forEach(row => {
      const db = row.dataset.db, id = row.dataset.id;
      row.querySelectorAll('[data-verdict]').forEach(b =>
        b.addEventListener('click', () => setReview(r.iri, db, id, b.dataset.verdict)));
      row.querySelector('[data-preview]').addEventListener('click', () => preview(db, id, r));
    });
    // Predicted candidates: preview and accept-into-cell.
    $('#work').querySelectorAll('.id-row[data-pred-id]').forEach(row => {
      const db = row.dataset.db, pid = row.dataset.predId;
      row.querySelector('[data-preview-pred]').addEventListener('click', () => preview(db, pid, r));
      row.querySelector('[data-accept]').addEventListener('click', () => acceptPrediction(r.iri, db, pid));
    });
    // Per-DB add box.
    $('#work').querySelectorAll('.db-card').forEach(card => {
      const db = card.dataset.db;
      const input = card.querySelector('[data-add-input]');
      const saveBtn = card.querySelector('[data-add-save]');
      const doSave = () => saveIds(r.iri, db, input.value);
      saveBtn.addEventListener('click', doSave);
      input.addEventListener('keydown', e => { if (e.key === 'Enter') doSave(); });
    });
  }

  function setReview(iri, db, id, v) {
    const key = idKey(iri, db, id);
    reviewed[key] = reviewed[key] === v ? null : v;
    renderWork(); renderPicker($('#filter').value); counts();
  }

  // Accept a predicted id: pre-fill it as this cell's value and Save (a real
  // edit), then mark it confirmed — the one-click path the prediction enables.
  async function acceptPrediction(iri, db, id) {
    try { await saveIds(iri, db, id, /*silent*/ true); }
    catch (e) { alert('Could not add predicted id: ' + e.message); return; }
    setReview(iri, db, id, 'ok');
    preview(db, id, ROWS.find(x => x.iri === iri));
  }

  async function saveIds(iri, dbkey, value, silent) {
    if (!me || !me.authenticated) { alert('Sign in with GitHub first.'); return; }
    const val = (value || '').trim();
    try {
      const updated = await api('disease/' + enc(iri), { method: 'PUT', body: { changes: { [dbkey]: val } } });
      const r = ROWS.find(x => x.iri === iri);
      const oldIds = r[dbkey] || [], newIds = updated[dbkey] || [];
      r[dbkey] = newIds;
      for (const id of newIds) edited[idKey(iri, dbkey, id)] = true;
      for (const id of oldIds) if (!newIds.includes(id)) { delete edited[idKey(iri, dbkey, id)]; delete reviewed[idKey(iri, dbkey, id)]; }
      renderWork(); renderPicker($('#filter').value); counts();
    } catch (e) { if (!silent) alert('Save failed: ' + e.message); else throw e; }
  }

  // -------------------------------------------------- RIGHT: source preview
  function preview(dbkey, id, r) {
    const db = DBMAP[dbkey];
    active = { db: dbkey, id };
    const src = id ? db.link(id) : db.search(r.name);
    $('#panel').innerHTML = `
      <div class="p-head"><span class="p-title">${esc(db.label)} · ${esc(id || r.name)}</span>
        <span style="flex:1"></span>
        <a class="btn" href="${esc(src)}" target="_blank" rel="noopener">Open ↗</a>
        <button class="btn" id="p-close">✕</button></div>
      ${db.noframe
        ? `<div class="p-note muted">${esc(db.label)} can't be previewed here (it blocks embedding${dbkey === 'umls' ? ' and requires login' : ''}). Use “Open ↗”.</div>`
        : `<iframe id="p-frame" src="${esc(src)}"></iframe>`}`;
    $('#side').classList.add('open'); $('#divider').classList.add('show');
    $('#p-close').addEventListener('click', () => { $('#side').classList.remove('open'); $('#divider').classList.remove('show'); active = null; renderWork(); });
    renderWork();   // refresh active-id highlight in the center
  }

  // -------------------------------------------------- PUBLISH
  async function publish() {
    const comment = window.prompt('Optional comment for the pull request (what you reviewed/changed):', 'Mappings review');
    if (comment === null) return;
    const orcid = (localStorage.getItem('ari_editor_orcid') || '').trim();
    const author = orcid ? ('orcid:' + orcid) : (me && me.login ? ('github:' + me.login) : 'curator');
    $('#publish').disabled = true; $('#publish').textContent = 'Publishing…';
    try {
      const r = await api('publish', { method: 'POST', body: {
        disease: 'mappings review', message: reviewMessage(), comment,
        confirmed: confirmedList(), flagged: flaggedList(), author,
        branch: sessionBranch, labels: ['edit term', 'sssom'] } });
      sessionBranch = r.branch;
      const pl = $('#prlink');
      pl.textContent = 'PR #' + r.pr_number + (r.fork ? ' (from your fork) ↗' : ' ↗'); pl.href = r.pr_url; pl.style.display = '';
      $('#publish').textContent = 'Publish more to PR #' + r.pr_number;
      $('#publish').disabled = true;   // re-enabled by counts() on the next change
    } catch (e) { alert('Publish failed: ' + e.message); $('#publish').textContent = sessionBranch ? 'Publish more to PR' : 'Publish review (PR)'; counts(); }
  }

  // Draggable splitter between the work area and the preview (mouse + touch).
  function initDivider() {
    const div = $('#divider'), body = document.querySelector('.body'), side = $('#side');
    let dragging = false;
    const move = e => {
      if (!dragging) return;
      const x = (e.touches ? e.touches[0].clientX : e.clientX);
      const rect = body.getBoundingClientRect();
      let w = rect.right - x;
      w = Math.max(260, Math.min(rect.width - 200, w));
      side.style.width = w + 'px';
    };
    const start = e => { dragging = true; document.body.classList.add('dragging'); e.preventDefault(); };
    const end = () => { dragging = false; document.body.classList.remove('dragging'); };
    div.addEventListener('mousedown', start); div.addEventListener('touchstart', start, { passive: false });
    window.addEventListener('mousemove', move); window.addEventListener('touchmove', move, { passive: false });
    window.addEventListener('mouseup', end); window.addEventListener('touchend', end);
  }

  async function init() {
    try { me = await api('me'); } catch (e) { me = { github_enabled: false, authenticated: false }; }
    $('#auth').innerHTML = !me.authenticated
      ? (me.github_enabled ? `<a class="btn" href="${new URL('../auth/github?next=' + encodeURIComponent(location.pathname + location.search), location.href).href}">Sign in with GitHub</a>` : '<span class="muted">GitHub off — review only</span>')
      : `<span class="muted">@${esc(me.login)}</span>`;
    try { buildDatabases(await api('xref-databases')); }
    catch (e) { $('#pick-list').innerHTML = '<p class="muted" style="padding:16px">Failed to load the database registry: ' + esc(e.message) + '</p>'; return; }
    try { ROWS = await api('xrefs'); } catch (e) { $('#pick-list').innerHTML = '<p class="muted" style="padding:16px">Failed to load: ' + esc(e.message) + '</p>'; return; }
    try { mappings = {}; for (const m of await api('mappings')) mappings[m.ari_id + '|' + m.prefix + '|' + m.id] = m.judgment; } catch (e) { mappings = {}; }
    try { predicted = {}; for (const p of await api('predictions')) predicted[p.ari_id + '|' + p.prefix + '|' + p.id] = { label: p.object_label, match_field: p.match_field, confidence: p.confidence }; } catch (e) { predicted = {}; }
    renderPicker(''); counts(); initDivider();
    $('#filter').addEventListener('input', e => renderPicker(e.target.value));
    $('#publish').addEventListener('click', publish);
  }
  document.addEventListener('DOMContentLoaded', init);
})();
