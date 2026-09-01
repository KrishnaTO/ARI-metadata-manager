// Reference-review page — curation matrix (design 1c). Every review database is a
// column and every disease a row of state glyphs; clicking a disease opens its review
// strip in place, and clicking a cell opens that mapping in the side panel. Verdicts
// are per-id; confirmed cross-references are written to SSSOM + equivalency files in
// the published pull request.
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

  // ------------------------------------------------------- WINDOW PREFERENCES
  // Layout is per-window; identity-ish preferences (theme, text size) are not.
  //
  // Density and the disease-column width lived in localStorage, which is shared
  // by every window of the browser. Open a narrow comparison window beside a wide
  // one, size it to fit, and the wide window inherited the change on its next
  // reload — in a workflow whose whole point is two differently-shaped windows
  // (issue #114).
  //
  // A window seeds itself from the last value set anywhere and then keeps its
  // own: sessionStorage is read first and seeded from localStorage on first use,
  // so a new window still opens with the density the curator prefers, and an
  // existing one is never reshaped by a change made somewhere else.
  function winPref(key) {
    try {
      const own = sessionStorage.getItem(key);
      if (own !== null) return own;
      const shared = localStorage.getItem(key);
      if (shared !== null) sessionStorage.setItem(key, shared);
      return shared;
    } catch (e) { return null; }
  }

  function setWinPref(key, value) {
    try {
      sessionStorage.setItem(key, value);       // this window, from now on
      localStorage.setItem(key, value);         // and the default for the next one
    } catch (e) { /* storage may be unavailable */ }
  }

  // The database columns (labels, link/search URL builders, object-curie prefixes)
  // are all built from the shared registry served by /api/v2/xref-databases
  // (app/xref_registry.py) — the single source of truth, so the PREFIX map here
  // can no longer drift from the server's SSSOM prefixes. Populated by init().
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

  let ROWS = [], me = null, reviewed = {}, edited = {}, active = null,
      sessionBranch = null, sessionPr = null, _tissues = null, openRow = null,
      pendingPublishId = null;   // survives a failed attempt so a retry is recognised
  // Review queue: which curator holds each disease (iri -> {login, done}), the
  // logins known to have a queue, and this page's tick-box selection.
  let owners = {}, curators = [], selected = new Set(), lastSel = null, queueFilter = 'all';
  // Column view state. `colSort` orders the matrix by one column — 'missing' puts the
  // diseases still needing a mapping in that database on top, 'mapped' reverses it;
  // `missingOnly` narrows the matrix to the diseases missing a mapping in one column.
  // Both are view-only: nothing here touches verdicts or what gets published.
  let colSort = null, missingOnly = null;
  // reviewed/edited keys: `${iri}|${db}|${id}` (per-ID, not per-cell)
  // The review policy lives in review-model.js as pure functions; this page owns
  // the mutable state and hands a snapshot of it in. One implementation, and the
  // one the tests exercise.
  const RM = globalThis.ReviewModel;
  const model = () => ({
    reviewed, edited, published, mappings, predicted, idAuthors,
    PREFIX, DBS, me, sessionPr,
  });

  const idKey = RM.idKey;
  // What a key already sits in a pull request as: `key -> {pr, state}`. Keeps
  // published work out of the next publish, so a fresh PR is titled and filled
  // with new verdicts only instead of everything reviewed this session.
  let published = {};
  // A key's publishable state — the verdict, plus a marker when its id was edited.
  // Empty means there is nothing to publish for it.
  const keyState = k => RM.keyState(model(), k);
  const isPending = k => RM.isPending(model(), k);
  // Pre-existing curated judgments keyed `${ari_id}|${prefix}|${id}` ->
  // 'positive'|'negative', plus 'absent' under the NO_TERM id for a whole cell.
  let mappings = {};
  // Who added each id on file, keyed `${iri}|${db}|${id}` -> github login
  // (/api/v2/id-authors). A curator may not confirm an id they added themselves.
  let idAuthors = {};
  // SSSOM's "no term found in this database" sentinel — the id a cell-level
  // "not in database" verdict is stored and published under.
  const NO_TERM = RM.NO_TERM;
  // Predicted matches (issue #42) keyed `${ari_id}|${prefix}|${id}` ->
  // {label, match_field, confidence}. From /api/v2/predictions: exact name/synonym
  // hits for blank cells. confidence 'high' = the disease label matched a concept;
  // 'low' = only a synonym matched (label matched nothing) — worth a closer look.
  let predicted = {};

  // Predicted candidate ids for a currently-blank (disease, db) cell. Returns
  // [{id, label, match_field, confidence}], skipping any id already flagged negative.
  const predFor = (r, dbkey) => RM.predFor(model(), r, dbkey);

  // Per-ID pre-judgment from the curated mappings: 'pos' | 'neg' | null.
  const preJudgmentId = (r, dbkey, id) => RM.preJudgmentId(model(), r, dbkey, id);

  const $ = s => document.querySelector(s);

  // ------------------------------------------------- SEPARATION OF DUTIES
  // Whoever adds a mapping id may not be the one who confirms it, so a second
  // curator always vouches for the match. Flagging or declaring the database
  // empty stays open to everyone — only the ✓ is withheld.
  const addedBy = (iri, db, id) => RM.addedBy(model(), iri, db, id);
  const isOwnAddition = (iri, db, id) => RM.isOwnAddition(model(), iri, db, id);

  // The inline banner under the header. It already existed but was used only
  // for refusals, while errors and confirmations went to blocking alert()s
  // (issue #118). Everything routes through it now.
  //
  //   note(msg)            a refusal or a neutral notice — auto-dismisses
  //   note(msg, 'ok')      a confirmation — auto-dismisses
  //   note(msg, 'error')   a failure — stays until dismissed or superseded
  //
  // Errors persist because a message that describes a failed save must not
  // vanish while the curator is still reading it.
  let _noteTimer = null;
  function note(msg, kind) {
    const el = $('#note');
    el.textContent = msg;
    el.classList.remove('is-ok', 'is-error');
    if (kind) el.classList.add('is-' + kind);
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.classList.add('open');
    clearTimeout(_noteTimer);
    if (kind !== 'error') _noteTimer = setTimeout(() => el.classList.remove('open'), 6000);
    // Announce it too: the banner is visual, and there was no live region
    // anywhere in the app.
    UIDialog.announce(msg);
  }

  // ------------------------------------------------------------- STATE MODEL
  // Per-id state, the single thing the glyphs, tints and counts are derived from:
  //   ok    confirmed this session, or positive in the curated mappings
  //   bad   flagged this session, or negative in the curated mappings
  //   pred  lexical prediction for a blank cell (the disease label matched a concept)
  //   low   lexical prediction from a synonym only
  //   have  an id on file that nobody has judged yet
  // A cell (not an id) can also be `none` — the curator judged that the database
  // has no term for this disease at all.
  const idState = (r, dbkey, id, pred) => RM.idState(model(), r, dbkey, id, pred);

  // Everything in one cell: the ids on file, or — for a blank cell — its predictions.
  const cellEntries = (r, dbkey) => RM.cellEntries(model(), r, dbkey);

  // The cell-level "this database has no term for the disease" verdict: this
  // session's, or one already published to the mapping files.
  const absentKey = RM.absentKey;
  const isAbsent = (r, dbkey) => RM.isAbsent(model(), r, dbkey);

  // A cell only reads ✓ once every id in it has a verdict, so any unjudged entry wins.
  const cellState = (r, dbkey) => RM.cellState(model(), r, dbkey);

  const isOpenState = RM.isOpenState;
  // A database is missing a mapping for a disease until it is either confirmed or
  // declared to have no term for it — a blank, predicted, unjudged or flagged cell
  // all still need a mapping.
  const isMissing = (r, dbkey) => RM.isMissing(model(), r, dbkey);
  // How much work a cell still needs, lowest first: blank cells lead, resolved
  // cells trail. Drives the "missing first" column sort.
  const cellNeed = (r, dbkey) => RM.cellNeed(model(), r, dbkey);
  // A disease is complete when nothing in its row is still awaiting a verdict.
  const isComplete = r => RM.isComplete(model(), r);

  // Confirmed autoimmune (diseaseCategory "Autoimmune") — the same teal dot the
  // editor's disease tree carries, so a disease is marked identically on both pages.
  const aiDot = r => r.autoimmune
    ? '<span class="ai-dot" title="Confirmed autoimmune"></span>' : '';

  // One shape per state. `pred`, `low` and `have` used to be a large, a hollow
  // and a small circle -- three states differing mainly in SIZE at 11px, which
  // asks a non-specialist to make a discrimination the eye is poor at, and
  // leaves a red-green colour-blind curator with no reliable channel at all.
  const GLYPH = { ok: '✓', bad: '✕', pred: '!', low: '?', have: '•', none: '–' };
  const SUP = { 2: '²', 3: '³', 4: '⁴', 5: '⁵' };
  const TAG = { ok: 'confirmed', bad: 'flagged', pred: 'predicted', low: 'synonym',
                have: 'on file', none: 'not in database' };

  // Concept labels for ids on file (predictions already carry theirs). Filled lazily
  // for the open row only, then painted in place so a re-render never blocks on them.
  const idLabels = {};
  function labelKey(db, id) { return db + '|' + id; }
  function paintLabel(k) {
    const v = idLabels[k];
    if (!v) return;
    document.querySelectorAll(`.clabel[data-lk="${CSS.escape(k)}"]`).forEach(el => { el.textContent = v; });
  }
  function ensureLabel(db, id) {
    const k = labelKey(db, id);
    if (k in idLabels) { paintLabel(k); return; }
    idLabels[k] = '';                           // in flight — don't look it up twice
    conceptFor(db, id).then(c => {
      idLabels[k] = (c && c.found && c.label) ? c.label : '';
      paintLabel(k);
    });
  }
  function fillCardLabels(r) {
    for (const db of DBS) for (const id of (r[db.key] || [])) ensureLabel(db.key, id);
  }

  // ---------------------------------------------------------- COMPARE PANE
  // Judging a cross-reference means comparing two definitions. The endpoint
  // already returns the candidate's definition, synonyms and parents — the panel
  // used to fetch all of it and keep only `label`, so for the four databases that
  // block embedding (OMOP, Orphanet, UMLS, MeSH — 44% of every disease's review)
  // it showed nothing about the candidate at all, and the curator opened a tab
  // and held the ARI definition in working memory (issue #96).

  const MAX_SYNONYMS_SHOWN = 8;

  function synonymChips(list) {
    const all = (list || []).filter(Boolean);
    if (!all.length) return '<span class="cmp-none">none recorded</span>';
    const shown = all.slice(0, MAX_SYNONYMS_SHOWN);
    const rest = all.length - shown.length;
    return shown.map(x => `<span class="cmp-chip">${esc(x)}</span>`).join('') +
      (rest > 0 ? `<span class="cmp-more" title="${esc(all.slice(MAX_SYNONYMS_SHOWN).join(', '))}">+${rest} more</span>` : '');
  }

  function comparePaneHTML(r, db, ent) {
    // Left: the ARI record. Right: a placeholder the async lookup fills in, so
    // nothing blocks on the network.
    return `<div class="cmp">
      <div class="cmp-col">
        <div class="cmp-head">ARI &middot; ${esc(r.ari_id || '')}</div>
        <div class="cmp-name">${esc(r.name)}</div>
        <div class="cmp-label">Definition</div>
        <div class="cmp-def">${r.definition ? esc(r.definition) : '<span class="cmp-none">none recorded</span>'}</div>
        <div class="cmp-label">Synonyms</div>
        <div class="cmp-chips">${synonymChips(r.synonyms)}</div>
      </div>
      <div class="cmp-col" id="cmp-candidate">
        <div class="cmp-head">${esc(db.label)}${ent ? ' &middot; ' + esc(ent.id) : ''}</div>
        <div class="cmp-loading">Looking up the candidate concept…</div>
      </div>
    </div>`;
  }

  function renderCandidate(c, db, ent) {
    const col = $('#cmp-candidate');
    if (!col) return;
    const head = `<div class="cmp-head">${esc(db.label)} &middot; ${esc(ent.id)}</div>`;
    if (!c || !c.found) {
      col.innerHTML = head + `<div class="cmp-none" style="margin-top:8px">
        This id is not in the downloaded ${esc(db.label)} index, so there is nothing to
        compare against here. Open it at the source to check it.</div>`;
      return;
    }
    // A hub cross-reference is a weaker claim than the database's own term, and
    // the curator should be able to see which they are looking at.
    const via = (!c.direct && (c.via || []).length)
      ? `<div class="cmp-via">Not ${esc(db.label)}'s own record — found via
           ${c.via.map(v => esc(v)).join(', ')}, which cross-references this id.</div>`
      : '';
    const defn = c.definition
      ? esc(c.definition)
      : `<span class="cmp-none">${esc(c.note || 'no definition in the downloaded index')}</span>`;
    const parents = (c.parents || []).length
      ? `<div class="cmp-label">Broader terms</div>
         <div class="cmp-chips">${(c.parents || []).map(x => `<span class="cmp-chip">${esc(x)}</span>`).join('')}</div>`
      : '';
    col.innerHTML = head + via +
      `<div class="cmp-name">${esc(c.label || '(unnamed)')}</div>
       <div class="cmp-label">Definition</div>
       <div class="cmp-def">${defn}</div>
       <div class="cmp-label">Synonyms</div>
       <div class="cmp-chips">${synonymChips(c.synonyms)}</div>
       ${parents}`;
  }

  // Session cache of concept lookups, keyed `${db}|${id}`. Labels are decoration —
  // nothing waits on them, and a failed lookup just leaves the id unlabelled.
  const conceptCache = {};

  async function conceptFor(db, id) {
    const key = db + '|' + id;
    if (!(key in conceptCache)) {
      conceptCache[key] = api('concept/' + enc(db) + '/' + enc(id)).catch(() => null);
    }
    return conceptCache[key];
  }

  // Keys a publish covers. A new PR carries this session's pending work only;
  // appending to the tracked PR also re-sends the keys already on it, because the
  // mapping files are rebuilt from the base branch on every publish.
  const publishKeys = newPr => RM.publishKeys(model(), newPr);

  // ------------------------------------------------------------- ENRICHMENT
  // Confirming a cross-reference asserts the external term *is* the disease, so
  // that term's own synonyms and its direct children can be folded into the ARI
  // record. Preview only until "Apply on publish" is ticked; the server recomputes
  // the additions from the confirmed list at publish time, so this can't go stale.
  let applyEnrichment = false;

  // What the curator has ticked, as {iri: {synonyms: Set, subtypes: Set}}.
  // Everything starts ticked, so the default matches the old all-or-nothing
  // behaviour and a curator only has to act to *decline* something.
  let enrichPick = {};
  let enrichData = {};

  function enrichSelection() {
    // The publish payload: plain arrays of the values still ticked.
    const out = {};
    for (const iri of Object.keys(enrichPick)) {
      out[iri] = {
        synonyms: [...enrichPick[iri].synonyms],
        subtypes: [...enrichPick[iri].subtypes],
      };
    }
    return out;
  }

  function enrichCount() {
    return Object.values(enrichPick)
      .reduce((n, p) => n + p.synonyms.size + p.subtypes.size, 0);
  }

  function enrichItemHTML(iri, kind, entry, i) {
    // `entry` is {value, source} — the source is what makes this a curated
    // record rather than an aggregated one (#117), so it is always shown.
    const id = `en-${kind}-${i}`;
    const on = enrichPick[iri] && enrichPick[iri][kind].has(entry.value);
    // A subtype value reads "<name> - subtype of <disease> (<CURIE>)"; show the
    // name plainly, since the source is displayed separately.
    const m = kind === 'subtypes' && entry.value.match(/^(.*) - subtype of .* \(([^)]+)\)$/);
    const shown = m ? m[1] : entry.value;
    return `<li class="en-item">
      <label>
        <input type="checkbox" id="${id}" ${on ? 'checked' : ''}
               data-iri="${esc(iri)}" data-kind="${kind}" data-value="${esc(entry.value)}">
        <span class="en-val">${esc(shown)}</span>
        <span class="en-src" title="Proposed from this term">${esc(entry.source || '')}</span>
      </label></li>`;
  }

  function renderEnrich(data) {
    enrichData = data || {};
    const iris = Object.keys(enrichData);
    // Rebuild the selection, keeping any box the curator already unticked.
    const prev = enrichPick;
    enrichPick = {};
    for (const iri of iris) {
      const had = prev[iri];
      enrichPick[iri] = {
        synonyms: new Set((enrichData[iri].synonyms || [])
          .map(e => e.value).filter(v => !had || had.synonyms.has(v))),
        subtypes: new Set((enrichData[iri].subtypes || [])
          .map(e => e.value).filter(v => !had || had.subtypes.has(v))),
      };
    }
    const total = iris.reduce(
      (n, i) => n + enrichData[i].synonyms.length + enrichData[i].subtypes.length, 0);

    let i = 0;
    $('#enrich-list').innerHTML = !iris.length
      ? '<div class="pending-row"><span class="muted">Nothing new to add — the confirmed'
        + ' cross-references resolve to terms whose names and subtypes these diseases already'
        + ' carry, or to ids too coarse to identify a single concept.</span></div>'
      : iris.map(iri => {
          const d = enrichData[iri], r = ROWS.find(x => x.iri === iri);
          const group = (kind, label) => !d[kind].length ? '' :
            `<div class="en-group">
               <div class="en-grouphead">
                 <span class="en-label">${d[kind].length} ${label}</span>
                 <button class="en-all" data-iri="${esc(iri)}" data-kind="${kind}" data-on="0">None</button>
                 <button class="en-all" data-iri="${esc(iri)}" data-kind="${kind}" data-on="1">All</button>
               </div>
               <ul class="en-items">${d[kind].map(e => enrichItemHTML(iri, kind, e, i++)).join('')}</ul>
             </div>`;
          return `<div class="en-disease"><h4>${esc((r && r.name) || iri)}</h4>` +
                 group('synonyms', 'synonym(s)') + group('subtypes', 'clinical subtype(s)') + '</div>';
        }).join('');

    // One handler for the list rather than one per checkbox.
    $('#enrich-list').querySelectorAll('input[type=checkbox]').forEach(cb =>
      cb.addEventListener('change', () => {
        const set = enrichPick[cb.dataset.iri][cb.dataset.kind];
        if (cb.checked) set.add(cb.dataset.value); else set.delete(cb.dataset.value);
        reflectEnrichCount(total);
      }));
    $('#enrich-list').querySelectorAll('.en-all').forEach(b =>
      b.addEventListener('click', () => {
        const on = b.dataset.on === '1';
        const iri = b.dataset.iri, kind = b.dataset.kind;
        enrichPick[iri][kind] = new Set(on ? enrichData[iri][kind].map(e => e.value) : []);
        $('#enrich-list').querySelectorAll(
          `input[data-iri="${CSS.escape(iri)}"][data-kind="${kind}"]`).forEach(cb => { cb.checked = on; });
        reflectEnrichCount(total);
      }));

    $('#en-apply').disabled = !iris.length;
    if (!iris.length && applyEnrichment) { applyEnrichment = false; $('#en-apply').checked = false; }
    reflectEnrichCount(total);
  }

  function reflectEnrichCount(total) {
    const picked = enrichCount();
    const n = Object.keys(enrichData).length;
    $('#enrich-chip').title = n
      ? `${n} disease(s) · ${picked} of ${total} proposed addition(s) selected`
      : 'No additions proposed';
    $('#enrich-dot').classList.toggle('live', applyEnrichment && picked > 0);
  }

  // Open or close a drawer from its toolbar toggle, keeping the toggle's pressed
  // look and aria-expanded in step. Returns the resulting open state.
  function reflectDrawer(btn, panel, force) {
    const open = force === undefined ? !$(panel).classList.contains('open') : force;
    $(panel).classList.toggle('open', open);
    $(btn).classList.toggle('on', open);
    $(btn).setAttribute('aria-expanded', String(open));
    return open;
  }

  async function openEnrich() {
    if (!reflectDrawer('#enrich-chip', '#enrich-panel')) return;
    const confirmed = confirmedList(publishKeys(false));
    if (!confirmed.length) { reflectDrawer('#enrich-chip', '#enrich-panel', false); return; }
    $('#enrich-list').innerHTML = '<div class="pending-row"><span class="muted">Computing…</span></div>';
    try {
      renderEnrich(await api('enrichment-preview', { method: 'POST', body: { confirmed } }));
    } catch (e) {
      $('#enrich-list').innerHTML = '<div class="pending-row"><span class="muted">Preview failed: '
        + esc(e.message) + '</span></div>';
    }
  }

  function reviewMessage(keys) {
    const iris = new Set();
    for (const k of keys) if (edited[k] || reviewed[k] === 'ok' || reviewed[k] === 'none') iris.add(k.split('|')[0]);
    const ari = [...iris].map(i => (ROWS.find(x => x.iri === i) || {}).ari_id).filter(Boolean).sort();
    let lab = ari.slice(0, 6).join(', ');
    if (ari.length > 6) lab += ', +' + (ari.length - 6) + ' more';
    return '[' + (lab || 'cross-references') + '] - mappings review';
  }

  // Collect the given keys of a verdict ('ok' positives / 'bad' negatives) into
  // the {ari_id, iri, name, db, ids} shape publish wants.
  function reviewedCells(verdict, keys) {
    const cellMap = {};
    for (const k of keys) {
      if (reviewed[k] !== verdict) continue;
      const parts = k.split('|');
      const iri = parts[0], db = parts[1];
      const ck = iri + '|' + db;
      if (!cellMap[ck]) {
        const r = ROWS.find(x => x.iri === iri);
        cellMap[ck] = { ari_id: r ? r.ari_id : null, iri, name: r ? r.name : null, db, ids: [] };
      }
      cellMap[ck].ids.push(parts[2]);
    }
    return Object.values(cellMap).filter(c => c.ids.length > 0);
  }
  const confirmedList = keys => reviewedCells('ok', keys);
  const flaggedList = keys => reviewedCells('bad', keys);
  // "No term in this database" is a per-cell judgment, so it publishes without ids.
  const absentList = keys => reviewedCells('none', keys)
    .map(({ ari_id, iri, name, db }) => ({ ari_id, iri, name, db }));

  // The given (unpublished) keys grouped by disease, for the pending drawer.
  function pendingByDisease(keys) {
    const by = {};
    const bump = (iri, f) => { (by[iri] = by[iri] || { ok: 0, bad: 0, none: 0, ed: 0 })[f]++; };
    for (const k of keys) {
      if (reviewed[k] === 'ok' || reviewed[k] === 'bad' || reviewed[k] === 'none') bump(k.split('|')[0], reviewed[k]);
      if (edited[k]) bump(k.split('|')[0], 'ed');
    }
    return Object.entries(by).map(([iri, c]) => {
      const r = ROWS.find(x => x.iri === iri);
      return {
        name: r ? r.name : iri,
        summary: [c.ok ? c.ok + ' confirmed' : null, c.bad ? c.bad + ' flagged' : null,
                  c.none ? c.none + ' not in database' : null,
                  c.ed ? c.ed + ' edited' : null].filter(Boolean).join(' · '),
      };
    });
  }

  function counts() {
    const pending = [...publishKeys(true)];        // work not yet in any pull request
    const ok = pending.filter(k => reviewed[k] === 'ok').length;
    const bad = pending.filter(k => reviewed[k] === 'bad').length;
    const none = pending.filter(k => reviewed[k] === 'none').length;
    const ed = pending.filter(k => edited[k]).length;
    const done = ROWS.filter(isComplete).length;
    $('#done-n').textContent = done;
    $('#total-n').textContent = ROWS.length;
    $('#progress-bar').style.width = ROWS.length ? Math.round(done / ROWS.length * 100) + '%' : '0%';
    const pend = pendingByDisease(pending);
    $('#pending-count').textContent = pend.length;
    $('#pending-dot').classList.toggle('live', pend.length > 0);
    $('#pending-chip').title = `confirmed ${ok} · flagged ${bad} · not in database ${none} · edited ${ed}`;
    $('#pending-list').innerHTML = pend.length
      ? pend.map(p => `<div class="pending-row"><span>${esc(p.name)}</span><span>${esc(p.summary)}</span></div>`).join('')
      : '<div class="pending-row"><span class="muted">Nothing waiting to publish. Open a disease and judge a mapping.</span></div>';
    const canPublish = !!(me && me.authenticated && pending.length);
    $('#publish').disabled = !canPublish;
    $('#publish-new').disabled = !canPublish;
    // Enrichment derives from confirmed cross-references, so it needs at least one.
    const anyConfirmed = confirmedList(publishKeys(false)).length > 0;
    $('#enrich-chip').disabled = !anyConfirmed;
    if (!anyConfirmed) {
      applyEnrichment = false;
      $('#en-apply').checked = false;
      reflectDrawer('#enrich-chip', '#enrich-panel', false);
    }
    $('#enrich-dot').classList.toggle('live', applyEnrichment);
  }

  // Persist this signed-in user's review session (verdicts, edited-id markers and
  // the PR pointer) to the server so a page reload resumes their work.
  //
  // Only what *this window* changed is sent. The whole state blob used to go up
  // on every save and the server wrote it wholesale, so comparing two records
  // side by side — which is the product's core loop, and takes two windows —
  // meant the last window to save replaced the whole document and the other
  // one's verdicts were gone on the next reload (issue #114). Every key here is
  // one cell or one id, so the merge is unambiguous; `null` is the one thing a
  // plain merge cannot express, and means the curator cleared that verdict.
  const emptyPatch = () => ({ reviewed: {}, edited: {}, published: {} });
  let patch = emptyPatch();

  function setSessionKey(name, key, value) {
    const map = { reviewed, edited, published }[name];
    if (value === null) delete map[key]; else map[key] = value;
    patch[name][key] = value;
  }

  const patchIsEmpty = () => Object.values(patch).every(m => !Object.keys(m).length);

  // A failed save puts its keys back, without overwriting anything changed since.
  function requeue(sent) {
    for (const name of Object.keys(sent))
      for (const [k, v] of Object.entries(sent[name]))
        if (!(k in patch[name])) patch[name][k] = v;
  }

  function sessionBody() {
    const sent = patch;
    patch = emptyPatch();
    return { sent, body: { patch: sent, branch: sessionBranch, pr: sessionPr } };
  }

  let _saveTimer = null;
  function saveSession(immediate) {
    if (!(me && me.authenticated)) return;
    clearTimeout(_saveTimer);
    const put = () => {
      if (patchIsEmpty() && !immediate) return;
      const { sent, body } = sessionBody();
      api('ref-session', { method: 'PUT', body })
        .catch(e => { requeue(sent); console.warn('Could not save review session:', e.message); });
    };
    if (immediate) put(); else _saveTimer = setTimeout(put, 500);
  }

  // The debounce is a 500ms window in which closing the tab loses a verdict, and
  // this workflow closes tabs constantly. `keepalive` lets the request outlive
  // the page. `visibilitychange` rather than `beforeunload`: it is the one signal
  // that reliably fires when a tab is closed or backgrounded.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'hidden') return;
    if (!(me && me.authenticated) || patchIsEmpty()) return;
    clearTimeout(_saveTimer);
    const { body } = sessionBody();
    fetch(apiUrl('ref-session'), { method: 'PUT', keepalive: true,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .catch(() => { /* the page is going away; nothing useful to report */ });
  });

  // Reflect the tracked PR in the header. With a PR on file the primary button
  // appends to it and the split caret opens the alternatives (visit the PR, or
  // start a fresh one); with none, the primary button opens the first PR alone.
  function reflectPr() {
    if (sessionPr) {
      // The GitHub link moves behind "Advanced": it lands on a diff of RDF/XML,
      // which is not where a clinician should be sent by the primary control.
      const pl = $('#prlink');
      pl.textContent = 'View ' + Words.submissionName(sessionPr.number).toLowerCase() + ' on GitHub ↗';
      pl.href = sessionPr.url;
      $('#publish').textContent = Words.submitLabel(sessionPr);
      $('#publish-split').classList.add('has-pr');
    } else {
      $('#publish').textContent = Words.publish;
      $('#publish-split').classList.remove('has-pr');
      closeMenus();
    }
  }

  // ------------------------------------------------------------ REVIEW QUEUE
  // A disease belongs to exactly one curator's queue. The whole assignment table
  // is small (one record per curator), so it is fetched once and flattened into
  // the per-disease owner map the rows and the queue filter read.
  const canQueue = () => !!(me && me.authenticated);
  const mine = iri => { const o = owners[iri]; return !!(o && me && o.login === me.login); };

  async function loadOwners() {
    owners = {}; curators = [];
    if (!canQueue()) return;
    try {
      const all = await api('assignments');
      curators = Object.keys(all || {}).sort();
      for (const [login, rec] of Object.entries(all || {})) {
        const done = new Set(rec.done || []);
        for (const iri of rec.iris || []) owners[iri] = { login, done: done.has(iri) };
      }
    } catch (e) { console.warn('Could not load the review queues:', e.message); }
    $('#curator-logins').innerHTML = curators.map(l => `<option value="${esc(l)}">`).join('');
  }

  // Tick one disease; with Shift, everything between it and the last tick — over
  // the rows currently visible, so a range never reaches past the filter.
  function toggleSelect(iri, range) {
    const rows = visibleRows().map(r => r.iri);
    if (range && lastSel && rows.includes(lastSel) && rows.includes(iri)) {
      const a = rows.indexOf(lastSel), b = rows.indexOf(iri);
      const on = !selected.has(iri);
      for (let i = Math.min(a, b); i <= Math.max(a, b); i++) {
        if (on) selected.add(rows[i]); else selected.delete(rows[i]);
      }
    } else if (selected.has(iri)) selected.delete(iri);
    else selected.add(iri);
    lastSel = iri;
    reflectSelection(); renderMatrix();
  }

  function clearSelection() {
    selected.clear(); lastSel = null;
    reflectSelection(); renderMatrix();
  }

  function reflectSelection() {
    const n = selected.size;
    $('#selbar').classList.toggle('open', n > 0);
    $('#sel-count').textContent = n + (n === 1 ? ' disease selected' : ' diseases selected');
    const held = [...selected].filter(i => owners[i] && !mine(i)).length;
    $('#sel-owned').textContent = held ? held + " already on another curator's queue" : '';
    // "Remove" only acts on the diseases I actually hold, so it says how many that
    // is and goes flat when the selection is all somebody else's, or nobody's.
    const own = [...selected].filter(i => mine(i)).length;
    const rm = $('#sel-remove');
    rm.disabled = !own;
    rm.title = own
      ? `Take ${own} disease${own === 1 ? '' : 's'} off your review queue`
      : 'Nothing in this selection is on your review queue';
  }

  // Queue the selection for `login`. Diseases another curator holds are only moved
  // after the curator says so; declining queues the rest and leaves those alone.
  async function addToQueue(login, queueNote) {
    const iris = [...selected];
    if (!iris.length || !login) return;
    const clash = {};
    for (const i of iris) {
      const o = owners[i];
      if (o && o.login !== login) (clash[o.login] = clash[o.login] || []).push(i);
    }
    const clashN = Object.values(clash).reduce((n, v) => n + v.length, 0);
    let send = iris, reassign = false;
    if (clashN) {
      const who = Object.entries(clash).map(([l, v]) => `${v.length} on @${l}`).join(', ');
      reassign = await UIDialog.confirm({
        title: `Move ${clashN} disease(s) to @${login}'s queue?`,
        detail: `${who} already. Declining queues only the ` +
                `${iris.length - clashN} nobody else holds.`,
        confirmLabel: 'Move them',
        cancelLabel: 'Leave them',
      });
      if (!reassign) send = iris.filter(i => !(owners[i] && owners[i].login !== login));
    }
    if (!send.length) return;
    const btns = [$('#sel-mine'), $('#sel-assign')];
    btns.forEach(b => { b.disabled = true; });
    try {
      await api('assignments', { method: 'POST', body: { login, iris: send, note: queueNote || '', reassign } });
      selected.clear(); lastSel = null;
      $('#sel-note').value = '';
      await loadOwners();
      reflectSelection(); renderMatrix();
    } catch (e) {
      note('Could not update the review queue: ' + e.message, 'error');
    } finally { btns.forEach(b => { b.disabled = false; }); }
  }

  // Take the selection off my own queue. A selection can span several curators'
  // queues and a delete is scoped to one, so this drops only what I hold — the
  // rest is left where it is. Nothing about a disease's verdicts changes; it just
  // stops being mine to work through, and reappears under "Unassigned".
  async function removeFromQueue() {
    const iris = [...selected].filter(i => mine(i));
    if (!iris.length) return;
    $('#sel-remove').disabled = true;
    try {
      await api('assignments', { method: 'DELETE', body: { iris } });
      selected.clear(); lastSel = null;
      await loadOwners();
      renderMatrix();
    } catch (e) {
      note('Could not update the review queue: ' + e.message, 'error');
    }
    reflectSelection();
  }

  function initQueue() {
    if (!canQueue()) return;
    $('#queue-filter').style.display = '';
    $('#sel-others').classList.toggle('on', !!me.can_assign_others);
    $('#sel-clear').addEventListener('click', clearSelection);
    $('#sel-mine').addEventListener('click', () => addToQueue(me.login, ''));
    $('#sel-remove').addEventListener('click', removeFromQueue);
    $('#sel-assign').addEventListener('click', () => {
      const login = $('#sel-login').value.trim();
      if (!login) { note('Enter the curator’s GitHub login.', 'error'); $('#sel-login').focus(); return; }
      addToQueue(login, $('#sel-note').value.trim());
    });
    $('#queue-filter').addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      queueFilter = b.dataset.queue;
      syncSegs(); renderMatrix();
    });
  }

  // ----------------------------------------------------------------- MATRIX
  // Disease column width, in px. null means the density default; a dragged value
  // (see initColGrip) is clamped to half the matrix area and remembered per browser.
  let diseaseW = null;
  const DISEASE_MIN = 150;
  // What a database column needs before its header label starts truncating —
  // ORPHANET is the longest at ~62px.
  const DB_READABLE = 64;
  const diseaseMax = () => document.querySelector('.body').getBoundingClientRect().width / 2;

  // Column tracks: the disease column plus one equal track per database. The 44px
  // floor is what makes the matrix scroll instead of collapse when the panel opens
  // or the disease column is dragged wide.
  //
  // The disease column is the one that gives way. Opening the review panel used to
  // leave the 413px name column untouched and crush the nine database columns to
  // 55px each — the column you no longer need (you know which disease you opened)
  // keeping its width while the columns you are comparing collided. The dragged
  // width is an upper bound now, not a floor (issue #97).
  function applyGrid() {
    const compact = document.documentElement.dataset.density === 'compact';
    if (diseaseW !== null) diseaseW = Math.max(DISEASE_MIN, Math.min(diseaseMax(), diseaseW));
    let w = diseaseW === null ? (compact ? 250 : 330) : diseaseW;
    const avail = $('#matrix-wrap')?.getBoundingClientRect().width || 0;
    if (avail) w = Math.max(DISEASE_MIN, Math.min(w, avail - DBS.length * DB_READABLE));
    // The splitter reads --disease-col to place itself on the boundary.
    document.documentElement.style.setProperty('--disease-col', Math.round(w) + 'px');
    document.documentElement.style.setProperty('--grid-cols',
      'var(--disease-col) repeat(' + DBS.length + ', minmax(44px,1fr))');
  }

  // Header cells carry both column controls: the label sorts, the ○ button
  // narrows to what is still missing. That is a filter, not a verdict, so it is
  // deliberately not one of the state glyphs.
  function headCell(key, label, extra) {
    const dir = colSort && colSort.db === key ? colSort.dir : null;
    const only = missingOnly === key;
    const arrow = dir === 'missing' ? '▲' : dir === 'mapped' ? '▼' : dir === 'az' ? '▲' : dir === 'za' ? '▼' : '';
    const sortTitle = key === NAME_COL ? 'Sort diseases by name'
      : `Sort by ${label}: diseases missing a mapping first`;
    return `<div class="hcol${dir || only ? ' active' : ''}">
      <button class="hsort" data-sort="${esc(key)}" title="${esc(sortTitle)}"><span class="hlabel">${esc(label)}</span><span class="harrow">${arrow}</span></button>
      ${extra}</div>`;
  }

  function renderHead() {
    $('#mhead').innerHTML = headCell(NAME_COL, 'Disease', '') + DBS.map(d =>
      headCell(d.key, d.label,
        `<button class="hmiss${missingOnly === d.key ? ' on' : ''}" data-miss="${esc(d.key)}"
           title="Show only the diseases missing a ${esc(d.label)} mapping">○</button>`)).join('');
    measureHead();
  }

  // The open strip's disease name sticks below the column header, so it needs the
  // header's height — which changes with the density and the text size.
  function measureHead() {
    const h = $('#mhead').offsetHeight;
    if (h) document.documentElement.style.setProperty('--mhead-h', h + 'px');
  }

  // The disease column sorts by name; the database columns sort by how much of the
  // mapping work is left. One column at a time, and a third click clears it.
  const NAME_COL = '__name';
  function cycleSort(key) {
    const order = key === NAME_COL ? ['az', 'za'] : ['missing', 'mapped'];
    const cur = colSort && colSort.db === key ? colSort.dir : null;
    const next = cur === null ? order[0] : cur === order[0] ? order[1] : null;
    colSort = next ? { db: key, dir: next } : null;
    renderHead(); renderMatrix();
  }

  function toggleMissingOnly(key) {
    missingOnly = missingOnly === key ? null : key;
    renderHead(); renderMatrix();
  }

  function sortRows(rows) {
    if (!colSort) return rows;
    const { db, dir } = colSort;
    const key = db === NAME_COL ? r => (r.name || '').toLowerCase() : r => cellNeed(r, db);
    const sign = dir === 'mapped' || dir === 'za' ? -1 : 1;
    return rows.slice().sort((a, b) => {
      const x = key(a), y = key(b);
      return x < y ? -sign : x > y ? sign : 0;
    });
  }

  const awaitsSecondReviewer = r => RM.awaitsSecondReviewer(model(), r);

  const inQueueFilter = r => queueFilter === 'all' ? true
    : queueFilter === 'mine' ? mine(r.iri)
    : queueFilter === 'second' ? awaitsSecondReviewer(r)
    : !owners[r.iri];

  function visibleRows() {
    const q = ($('#filter').value || '').trim().toLowerCase();
    let rows = queueFilter === 'all' ? ROWS : ROWS.filter(inQueueFilter);
    if (missingOnly) rows = rows.filter(r => isMissing(r, missingOnly));
    if (q) rows = rows.filter(r => (r.name || '').toLowerCase().includes(q) ||
      (r.synonyms || []).some(s => String(s).toLowerCase().includes(q)) ||
      DBS.some(db => cellEntries(r, db.key).some(e => String(e.id).toLowerCase().includes(q))));
    return sortRows(rows);
  }

  // How much a candidate deserves trust, 0-100 (issue #91). The grid only ever
  // said "predicted" or "predicted from a synonym", so everything in between
  // looked alike and there was no way to work the easy ones first. The tooltip
  // says what produced the number rather than leaving it a bare score.
  const ROUTE_SAYS = {
    xref: 'carried by an id already on file for this disease',
    label: 'this disease’s label is exactly this term’s name',
    synonym: 'only one of this disease’s synonyms matched',
  };

  function scoreHtml(pred) {
    if (!pred || typeof pred.score !== 'number') return '';
    const why = ROUTE_SAYS[pred.match_field] || 'lexical match';
    return `<span class="cscore ${esc(pred.band || 'weak')}"
      title="Match score ${pred.score} of 100 · ${esc(why)}">${pred.score}</span>`;
  }

  // One database card in the open row's review strip: the state tag, each id with its
  // ✓/✕ verdict and a link out, an "add another id" affordance, and — while the cell
  // holds no id — the "no term in this database" verdict.
  function cardHtml(r, db) {
    const entries = cellEntries(r, db.key);
    const st = cellState(r, db.key);
    const tag = st ? TAG[st] : 'no id';
    const ids = entries.map(e => {
      const ist = idState(r, db.key, e.id, e.pred);
      const k = idKey(r.iri, db.key, e.id);
      const sel = active && active.iri === r.iri && active.dbkey === db.key && String(active.id) === String(e.id);
      const label = e.pred ? (e.pred.label || '') : (idLabels[labelKey(db.key, e.id)] || '');
      const at = ` data-iri="${esc(r.iri)}" data-db="${db.key}" data-id="${esc(e.id)}"`;
      const own = isOwnAddition(r.iri, db.key, e.id);
      const author = addedBy(r.iri, db.key, e.id);
      return `<div class="card-id${sel ? ' sel' : ''}${edited[k] ? ' edited' : ''}"${at}${e.pred ? ' data-pred="1"' : ''}>
        <div class="cid"><span class="cidtext">${esc(e.id)}</span>${scoreHtml(e.pred)}</div>
        <div class="clabel" data-lk="${esc(labelKey(db.key, e.id))}">${esc(label)}</div>
        ${author ? `<div class="cwho${own ? ' own' : ''}">added by @${esc(author)}</div>` : ''}
        <div class="cacts">
          <button class="cbtn ok${ist === 'ok' ? ' on' : ''}${own ? ' locked' : ''}" data-v="ok"${at}
            title="${own ? 'You added this id — another curator has to confirm it' : 'Confirm this mapping'}">✓</button>
          <button class="cbtn bad${ist === 'bad' ? ' on' : ''}" data-v="bad"${at} title="Flag this mapping as wrong">✕</button>
          <a class="copen" href="${esc(db.link(e.id))}" target="_blank" rel="noopener" title="Open in ${esc(db.label)}">↗</a>
        </div></div>`;
    }).join('');
    // Declaring the database empty only makes sense while nothing is on file for it.
    const absent = st === 'none';
    const noneBtn = (r[db.key] || []).length ? '' :
      `<button class="cnone${absent ? ' on' : ''}" data-none="1" data-iri="${esc(r.iri)}" data-db="${db.key}"
         title="Record that ${esc(db.label)} has no term for this disease">– Not in ${esc(db.label)}</button>`;
    return `<div class="card${st ? ' ' + st : ''}">
      <div class="card-h"><span class="card-db">${esc(db.label)}</span><span class="card-tag${st ? ' ' + st : ''}">${esc(tag)}</span></div>
      ${ids}${entries.length ? '' : '<div class="card-empty">no id yet</div>'}
      ${noneBtn}
      <div class="card-add" data-iri="${esc(r.iri)}" data-db="${db.key}">+ add another id</div>
    </div>`;
  }

  // A new curator signs in to 214 rows with nothing to say where to begin. When
  // they have judged nothing and hold no queue, offer one scope to start from —
  // the emptiest column, which is where the work actually is (issue #119).
  function reflectFirstRun() {
    const bar = $('#firstrun');
    const fresh = !Object.keys(reviewed).length && !mineCount() &&
                  !missingOnly && queueFilter === 'all' && !$('#filter').value.trim();
    bar.classList.toggle('open', fresh && !!ROWS.length);
    if (!fresh || !ROWS.length) return;
    const db = emptiestDb();
    if (!db) { bar.classList.remove('open'); return; }
    bar.innerHTML = `<span>New here? The gaps are the work. ` +
      `<strong>${esc(db.label)}</strong> is missing a mapping for ` +
      `${ROWS.filter(r => isMissing(r, db.key)).length} of ${ROWS.length} diseases.</span>` +
      `<button class="btn" data-firstrun="${esc(db.key)}">Start with ${esc(db.label)}</button>` +
      `<button class="btn" data-firstrun-help="1">How this works</button>`;
  }

  const mineCount = () => ROWS.filter(r => mine(r.iri)).length;

  // The review column with the most diseases still missing a mapping.
  function emptiestDb() {
    let best = null, most = 0;
    for (const db of DBS) {
      const n = ROWS.filter(r => isMissing(r, db.key)).length;
      if (n > most) { most = n; best = db; }
    }
    return best;
  }

  function emptyNote() {
    if (missingOnly && !$('#filter').value.trim())
      return 'Every disease in view already has a ' + ((DBMAP[missingOnly] || {}).label || missingOnly) + ' mapping.';
    if (queueFilter === 'mine' && !$('#filter').value.trim())
      return 'Nothing in your review queue yet — tick some diseases under “All” and add them.';
    if (queueFilter === 'unassigned' && !$('#filter').value.trim())
      return 'Every disease is on a curator’s queue.';
    if (queueFilter === 'second' && !$('#filter').value.trim())
      return 'Nothing is waiting on you. This scope lists ids another curator added ' +
        'and nobody has judged — the ones only someone else can confirm.';
    return 'No disease or id matches that filter.';
  }

  function renderMatrix() {
    const rows = visibleRows();
    let h = '';
    for (const r of rows) {
      const open = openRow === r.iri;
      const states = DBS.map(db => cellState(r, db.key));
      const okN = states.filter(s => s === 'ok').length;
      const cells = DBS.map((db, j) => {
        const st = states[j];
        const entries = cellEntries(r, db.key);
        const sel = active && active.iri === r.iri && active.dbkey === db.key;
        const anyEdited = entries.some(e => edited[idKey(r.iri, db.key, e.id)]);
        const sup = entries.length > 1 ? (SUP[entries.length] || '·' + entries.length) : '';
        const title = entries.length ? entries.map(e => e.id).join(', ') : 'No ' + db.label + ' id';
        // role + tabindex: every cell was a plain div with a click handler, so
        // roughly 1,900 of them were unreachable without a mouse and screen
        // readers saw unlabelled containers. The roving tabindex is set by
        // primeMatrixTabStop() after each render.
        const label = `${r.name}, ${db.label}: ${st ? TAG[st] : 'no id'}`;
        return `<div class="mcell${st ? ' ' + st : ''}${sel ? ' sel' : ''}${anyEdited ? ' edited' : ''}"
          data-iri="${esc(r.iri)}" data-db="${db.key}" title="${esc(title)}"
          role="gridcell" tabindex="-1" aria-label="${esc(label)}"
          ><span>${st ? GLYPH[st] : ''}</span><span class="sup">${sup}</span></div>`;
      }).join('');
      let strip = '';
      if (open) {
        const openN = DBS.reduce((n, db) => n + (isAbsent(r, db.key) ? 0 : cellEntries(r, db.key)
          .filter(e => isOpenState(idState(r, db.key, e.id, e.pred))).length), 0);
        const none = states.filter(s => !s).length;
        const multi = DBS.filter(db => cellEntries(r, db.key).length > 1).length;
        const summary = `${okN} confirmed · ${openN} ids awaiting review · ${none} databases with no id` +
          (multi ? ` · ${multi} with several ids` : '');
        strip = `<div class="strip">
          <div class="strip-head">
            <span class="strip-title">${esc(r.name)}</span>${aiDot(r)}
            <button class="strip-copy" data-copy="${esc(r.iri)}"
              title="Copy a link to this disease — paste it in a second window to compare side by side"
              aria-label="Copy a link to ${esc(r.name)}">⧉ Copy link</button>
            <span class="strip-id">${esc(r.ari_id || '')}</span>
            <span class="strip-syn">${esc((r.synonyms || []).join(' · '))}</span>
            <span style="flex:1"></span>
            <button class="btn" data-subtype="${esc(r.iri)}">＋ New subtype</button>
            <span class="strip-sum">${esc(summary)}</span>
          </div>
          <div class="cards">${DBS.map(db => cardHtml(r, db)).join('')}</div>
        </div>`;
      }
      const own = owners[r.iri];
      const badge = own
        ? `<span class="mowner${mine(r.iri) ? ' mine' : ''}${own.done ? ' done' : ''}" title="${
            own.done ? 'Marked done by' : 'In the review queue of'} @${esc(own.login)}">@${esc(own.login)}${own.done ? ' ✓' : ''}</span>`
        : '';
      const box = canQueue()
        ? `<input type="checkbox" class="mselect" data-sel="${esc(r.iri)}"${selected.has(r.iri) ? ' checked' : ''}
             title="Select for a review queue (Shift-click for a range)">`
        : '';
      h += `<div class="mgroup${open ? ' open' : ''}">
        <div class="mrow" data-iri="${esc(r.iri)}">
          <div class="mname">${box}<span class="mcaret">${open ? '▾' : '▸'}</span>
            <span class="mname-text">${esc(r.name)}</span>${aiDot(r)}
            ${badge}<span class="mcount">${okN}/${DBS.length}</span></div>
          ${cells}
        </div>${strip}</div>`;
    }
    $('#matrix').innerHTML = h || `<div class="empty-note">${esc(emptyNote())}</div>`;
    reflectFirstRun();
    primeMatrixTabStop();      // exactly one cell stays in the tab order
    if (openRow) {
      const r = ROWS.find(x => x.iri === openRow);
      if (r) fillCardLabels(r);
    }
  }

  function toggleRow(iri) { openRow = openRow === iri ? null : iri; renderMatrix(); reflectHash(); }

  // Open one mapping in the side panel, expanding its disease so the strip agrees
  // with what the panel is showing.
  function openEntry(iri, dbkey, id) {
    openRow = iri;
    openPanel(iri, dbkey, id);
    renderMatrix();
    reflectHash();
  }

  // ------------------------------------------------------------- DEEP LINKS
  // What is open lives in the fragment, so a disease row — or one disease x
  // database cell — can be addressed in a URL. Comparing two records side by side
  // is what this page is for, and there was no way to set that second window up:
  // the editor has `#/disease/<iri>` and a Copy link button, this page had
  // nothing (issue #114).
  //
  // `#<iri>` opens the row; `#<iri>|<db>|<id>` also opens that cell's panel.
  // replaceState, not a push: clicking through twenty cells should not bury the
  // page the curator came from under twenty history entries.
  function hashFor(iri, dbkey, id) {
    return '#' + [enc(iri), dbkey, id].filter(x => x != null && x !== '').map(String).join('|');
  }

  function linkTo(iri, dbkey, id) {
    return new URL(hashFor(iri, dbkey, id), location.href.split('#')[0]).href;
  }

  let _hashOurs = '';
  function reflectHash() {
    const h = active ? hashFor(active.iri, active.dbkey, active.id)
            : openRow ? hashFor(openRow)
            : '';
    if (h === location.hash) return;
    _hashOurs = h;
    history.replaceState(null, '', h || location.pathname + location.search);
  }

  // Read the fragment and open what it names. Silent when it names a disease that
  // is not in the matrix — a stale link should not break the page.
  function applyHash() {
    const raw = location.hash.replace(/^#/, '');
    if (!raw) return;
    const [rawIri, dbkey, id] = raw.split('|');
    let iri;
    try { iri = decodeURIComponent(rawIri); } catch (e) { return; }
    if (!ROWS.some(r => r.iri === iri)) return;
    if (dbkey && DBMAP[dbkey]) openEntry(iri, dbkey, id || null);
    else { openRow = iri; renderMatrix(); }
    document.querySelector(`.mrow[data-iri="${CSS.escape(iri)}"]`)
      ?.scrollIntoView({ block: 'center' });
  }

  window.addEventListener('hashchange', () => {
    if (location.hash === _hashOurs) return;      // our own replaceState
    applyHash();
  });

  // Step to the next mapping anywhere in the matrix that still needs a verdict.
  function nextOpen() {
    const flat = [];
    for (const r of ROWS) for (const db of DBS) {
      if (isAbsent(r, db.key)) continue;          // settled — nothing left to judge here
      for (const e of cellEntries(r, db.key)) flat.push({ r, db: db.key, id: e.id, pred: e.pred });
    }
    if (!flat.length) return;
    const at = active ? flat.findIndex(x => x.r.iri === active.iri && x.db === active.dbkey &&
                                             String(x.id) === String(active.id)) : -1;
    for (let n = 1; n <= flat.length; n++) {
      const c = flat[(at + n) % flat.length];
      if (isOpenState(idState(c.r, c.db, c.id, c.pred))) { openEntry(c.r.iri, c.db, c.id); return; }
    }
  }

  function setReview(iri, db, id, v) {
    if (v === 'ok' && isOwnAddition(iri, db, id)) {
      note(`You added ${DBMAP[db] ? DBMAP[db].label : db} id ${id}, so you cannot confirm it — ` +
           'another curator has to. You can still flag it as wrong.');
      return;
    }
    const key = idKey(iri, db, id);
    setSessionKey('reviewed', key, reviewed[key] === v ? null : v);
    counts(); saveSession(); renderMatrix();
    // Keep the panel's own verdict buttons in step without rebuilding it (which
    // would reload the preview iframe underneath the curator).
    const ok = $('#p-ok'), bad = $('#p-bad');
    if (ok && bad && active && active.iri === iri && active.dbkey === db && String(active.id) === String(id)) {
      ok.classList.toggle('on', reviewed[key] === 'ok');
      bad.classList.toggle('on', reviewed[key] === 'bad');
    }
  }

  // Toggle the cell-level "this database has no term for the disease" verdict.
  // It publishes as an SSSOM NoTermFound row rather than a per-id judgment.
  function setAbsent(iri, dbkey) {
    const key = absentKey(iri, dbkey);
    setSessionKey('reviewed', key, reviewed[key] === 'none' ? null : 'none');
    counts(); saveSession(); renderMatrix();
    if (active && active.iri === iri && active.dbkey === dbkey) openPanel(iri, dbkey, active.id);
  }

  // ------------------------------------------------------------- SIDE PANEL
  function openPanel(iri, dbkey, id) {
    const r = ROWS.find(x => x.iri === iri);
    const db = DBMAP[dbkey];
    if (!r || !db) return;
    const entries = cellEntries(r, dbkey);
    const ent = entries.find(e => String(e.id) === String(id)) || entries[0] || null;
    const target = ent ? ent.id : null;
    active = { iri, dbkey, id: target };
    const st = ent ? idState(r, dbkey, ent.id, ent.pred) : null;

    const absent = isAbsent(r, dbkey);
    const noIdsOnFile = !(r[dbkey] || []).length;
    const own = ent ? isOwnAddition(iri, dbkey, ent.id) : false;
    const author = ent ? addedBy(iri, dbkey, ent.id) : null;

    const eyebrow = absent ? 'No term in ' + db.label
      : !ent ? 'No id yet'
      : st === 'ok' ? 'Confirmed mapping' : st === 'bad' ? 'Flagged mapping'
      : st === 'low' ? 'Predicted · synonym match only'
      : st === 'pred' ? 'Predicted · exact ' + (ent.pred.match_field || 'label') + ' match'
      : 'On file · not yet reviewed';
    const pos = ent ? entries.findIndex(e => String(e.id) === String(ent.id)) + 1 : 0;
    const sub = (r.ari_id || '') + ' → ' + db.label + (entries.length > 1 ? ` · id ${pos} of ${entries.length}` : '');

    const sibs = entries.length > 1 ? `<div class="p-switch">
      <div class="p-switch-h">${entries.length} ids mapped to this database — judge each one</div>
      <div class="p-sibs">${entries.map(e => {
        const s = idState(r, dbkey, e.id, e.pred);
        return `<span class="p-sib ${s}${String(e.id) === String(target) ? ' active' : ''}" data-sib="${esc(e.id)}">${esc(e.id)}</span>`;
      }).join('')}</div></div>` : '';

    // The target id and its editor are one control: ✎ swaps the id for an input over
    // the same box, so there is a single place the id is read and written.
    const lk = ent && !ent.pred ? labelKey(dbkey, ent.id) : '';
    const entLabel = ent ? (ent.pred ? (ent.pred.label || '') : (idLabels[lk] || '')) : '';
    const searchLink = `<a href="${esc(db.search(r.name))}" target="_blank" rel="noopener">search ${esc(db.label)} for "${esc(r.name)}" ↗</a>`;
    const idBlock = `<div class="p-idblock" id="p-idblock">
      <div class="p-idview">
        ${ent ? `<div class="p-idrow">
            <span class="p-id">${esc(ent.id)}</span>
            <button class="p-icon" id="p-edit" title="Edit this ${esc(db.label)} id">✎</button>
          </div>
          <div class="p-label clabel"${lk ? ` data-lk="${esc(lk)}"` : ''}>${esc(entLabel)}</div>
          <div class="p-idacts">
            <a class="p-open" href="${esc(db.link(ent.id))}" target="_blank" rel="noopener">Open ${esc(db.label)} in a new tab ↗</a>
            <button class="btn" id="p-copy">Copy id</button>
          </div>
          <div class="p-note">${db.noframe
            ? esc(db.label) + ' blocks embedding' + (dbkey === 'umls' ? ' and requires login' : '') + ', so nothing is previewed here. '
            : 'Source page embeds below. '}Not the right concept? ${searchLink}</div>`
        : `<div class="p-label">No ${esc(db.label)} id yet — ${searchLink}, then add it here.</div>
           <div class="p-idacts"><button class="p-icon" id="p-edit">＋ Add a ${esc(db.label)} id</button></div>`}
      </div>
      <div class="p-idform">
        <label>${ent ? esc(db.label) + ' id' : 'New ' + esc(db.label) + ' id'}</label>
        <input id="p-ids" value="${esc(ent ? ent.id : '')}" placeholder="e.g. 12345">
        <div class="p-formacts">
          <button class="btn primary" id="p-save">Save</button>
          <button class="btn" id="p-cancel">Cancel</button>
        </div>
        <div class="p-hint">${ent
          ? 'Saving rewrites this one id and leaves the rest of the cell alone; clear it to remove the id.'
          : 'Adds this id to the cell.'} Separate several ids with commas.</div>
      </div>
    </div>`;

    const foot = entries.length > 1
      ? 'Each id publishes as its own SSSOM row; confirming one does not judge the others.'
      : 'Verdicts save to your review session as you work.';

    // Whoever adds an id may not confirm it, so the ✓ is withheld from its author
    // and the panel says why rather than leaving a dead button.
    // The rule is correct and entirely non-obvious, and it was communicated by a
    // withheld button and 11.5px of grey text at the foot of the panel (issue #119).
    // It is stated where the ✓ would have been, at body size, and names the person
    // it is waiting on — the ledger already knows who that is.
    const ownNote = own
      ? `<div class="p-lock"><strong>Waiting for a second curator.</strong>
           You added this id, so someone else has to confirm it — a mapping other
           people rely on is always vouched for by two. You can still reject it, or
           record that ${esc(db.label)} has no term for this disease.</div>`
      : author ? `<div class="p-note">Added by @${esc(author)} — confirming it is yours to do.</div>` : '';
    // "No term in this database" is a verdict about the cell, offered only while
    // no id is on file for it.
    const noneBtn = noIdsOnFile
      ? `<div class="p-actions">
           <button class="btn bad ${absent ? 'on' : ''}" id="p-none">– Not in ${esc(db.label)}</button>
           <span class="muted">${absent
             ? 'Publishes as an SSSOM “no term found” record for ' + esc(db.label) + '.'
             : 'Record that ' + esc(db.label) + ' has no term for this disease.'}</span>
         </div>`
      : '';

    $('#panel').innerHTML = `
      <div class="p-head">
        <button class="p-close" id="p-close">✕</button>
        <div class="p-eyebrow ${st === 'ok' ? 'ok' : (st === 'bad' || absent) ? 'bad' : ''}">${esc(eyebrow)}${
          ent && ent.pred ? scoreHtml(ent.pred) : ''}</div>
        <div class="p-title">${esc(r.name)}</div>
        <div class="p-sub">${esc(sub)}</div>
      </div>
      ${sibs}
      ${idBlock}
      ${ent ? `${ownNote}<div class="p-actions">
        <button class="btn ok ${st === 'ok' ? 'on' : ''}${own ? ' locked' : ''}" id="p-ok">✓ Confirm</button>
        <button class="btn bad ${st === 'bad' ? 'on' : ''}" id="p-bad">✕ Not the same</button>
        <button class="btn" id="p-next">Next open mapping</button>
      </div>` : ''}
      ${noneBtn}
      ${ent ? comparePaneHTML(r, db, ent) : ''}
      ${db.noframe || !ent
        ? ''
        : `<details class="p-source">
             <summary>Full ${esc(db.label)} page</summary>
             <iframe id="p-frame" loading="lazy" src="${esc(db.link(ent.id))}"></iframe>
           </details>`}
      <div class="p-foot">${esc(foot)}</div>`;

    $('#side').classList.add('open');
    $('#divider').classList.add('show');
    $('#p-close').addEventListener('click', closePanel);
    // ✎ / ＋ swaps the id box into its edit form; Save writes just this id.
    const blk = $('#p-idblock');
    const commit = () => saveId(iri, dbkey, ent ? ent.id : null);
    $('#p-edit').addEventListener('click', () => {
      blk.classList.add('editing');
      const inp = $('#p-ids'); inp.focus(); inp.select();
    });
    $('#p-cancel').addEventListener('click', () => {
      blk.classList.remove('editing');
      $('#p-ids').value = ent ? ent.id : '';
    });
    $('#p-save').addEventListener('click', commit);
    $('#p-ids').addEventListener('keydown', e => {
      if (e.key === 'Enter') commit();
      else if (e.key === 'Escape') $('#p-cancel').click();
    });
    if (noIdsOnFile) $('#p-none').addEventListener('click', () => setAbsent(iri, dbkey));
    if (ent) {
      $('#p-ok').addEventListener('click', () => setReview(iri, dbkey, target, 'ok'));
      $('#p-bad').addEventListener('click', () => setReview(iri, dbkey, target, 'bad'));
      $('#p-next').addEventListener('click', nextOpen);
      $('#p-copy').addEventListener('click', e => {
        navigator.clipboard.writeText(String(target)).then(() => { e.target.textContent = 'Copied'; })
          .catch(() => { e.target.textContent = 'Copy failed'; });
      });
      $('#panel').querySelectorAll('.p-sib').forEach(s =>
        s.addEventListener('click', () => openEntry(iri, dbkey, s.dataset.sib)));
      // Look the concept up after wiring, so nothing waits on it. One request
      // fills both the small caption and the compare pane.
      if (!ent.pred) ensureLabel(dbkey, ent.id);
      conceptFor(dbkey, ent.id).then(c => {
        // Guard against a slow response landing after the curator moved on.
        if (active && active.iri === iri && active.dbkey === dbkey &&
            String(active.id) === String(ent.id)) renderCandidate(c, db, ent);
      });
    }
  }

  function closePanel() {
    closeSubtypeOverlay();
    active = null;
    $('#side').classList.remove('open');
    $('#divider').classList.remove('show');
    renderMatrix();
    reflectHash();
  }

  // Copy a deep link to the disease. The button says what happened rather than
  // relying on a toast the curator may have looked away from.
  function copyLink(btn) {
    const was = btn.textContent;
    navigator.clipboard.writeText(linkTo(btn.dataset.copy))
      .then(() => { btn.textContent = '✓ Copied'; })
      .catch(() => { btn.textContent = 'Copy failed'; })
      .finally(() => setTimeout(() => { btn.textContent = was; }, 1600));
  }

  // -------------------------------------------------- NEW-SUBTYPE OVERLAY
  // Covers only the matrix (left) area so the reference info in the right panel
  // stays visible while a curator fills in a new child disease.
  async function loadTissues() {
    if (!_tissues) _tissues = await api('tissues');
    return _tissues;
  }

  function closeSubtypeOverlay() { $('#subtype-overlay').classList.remove('open'); }

  async function openSubtypeOverlay(parentIri) {
    const r = ROWS.find(x => x.iri === parentIri);
    if (!r) return;
    const ov = $('#subtype-overlay');
    ov.style.width = $('#matrix-wrap').getBoundingClientRect().width + 'px';
    ov.innerHTML = `
      <div class="so-head"><strong>＋ New subtype</strong><span style="flex:1"></span>
        <button class="btn" id="so-close">✕</button></div>
      <div class="so-body" id="so-body">
        <div class="so-parent-info">Parent disease: <strong>${esc(r.name)}</strong><br>
          Created as a child (subtype) of this disease. Use the reference info on the right to fill the cross-reference ids below.</div>
        <div class="so-field" id="so-existing-wrap" style="display:none"><label>Start from an existing clinical subtype</label>
          <select id="so-existing"><option value="">— blank —</option></select></div>
        <div class="so-field"><label>Label <span class="so-req">*</span></label>
          <input id="so-label" placeholder="e.g. Juvenile-onset ${esc(r.name)}"></div>
        <div class="so-field"><label>Definition <span class="so-req">*</span></label>
          <textarea id="so-definition" placeholder="A subtype of ${esc(r.name)} characterized by…"></textarea></div>
        <div class="so-field"><label>Definition source <span class="so-req">*</span></label>
          <input id="so-defsrc" placeholder="URL or PMID: 12345678"></div>
        <div class="so-field"><label>Target tissue <span class="so-req">*</span></label>
          <div class="so-tissue-grid" id="so-tissues"><span class="muted">Loading…</span></div></div>
        <div class="so-field"><label>Synonyms (comma separated)</label>
          <input id="so-synonyms" placeholder="Synonym 1, Synonym 2"></div>
        <div class="so-field"><label>Disease category</label><input id="so-category"></div>
        <div class="so-field"><label>Clinical subtypes (comma separated)</label>
          <input id="so-clinical" placeholder="Name - description, …"></div>
        <div class="so-field"><label>Editor name</label>
          <input id="so-editor" value="${esc((me && me.login) || '')}"></div>
      </div>
      <div class="so-actions">
        <button class="btn primary" id="so-save">＋ Create subtype</button>
        <button class="btn" id="so-cancel">Cancel</button></div>`;
    ov.classList.add('open');
    $('#so-close').addEventListener('click', closeSubtypeOverlay);
    $('#so-cancel').addEventListener('click', closeSubtypeOverlay);
    $('#so-save').addEventListener('click', () => submitSubtype(parentIri));
    // Offer the parent's existing clinical subtypes (not in the xref row) as a
    // starting point: picking one seeds the new child's label + definition.
    api('disease/' + enc(parentIri)).then(det => {
      const subs = (det && det.clinical_subtypes) || [];
      if (!subs.length) return;
      const sel = $('#so-existing');
      if (!sel) return;
      sel.innerHTML = '<option value="">— blank —</option>' +
        subs.map((s, i) => `<option value="${i}">${esc(String(s).split(' - ')[0])}</option>`).join('');
      sel.addEventListener('change', () => {
        if (sel.value === '') return;
        const raw = String(subs[sel.value]), dash = raw.indexOf(' - ');
        $('#so-label').value = dash >= 0 ? raw.slice(0, dash) : raw;
        if (dash >= 0) $('#so-definition').value = raw.slice(dash + 3);
      });
      $('#so-existing-wrap').style.display = '';
    }).catch(() => {});   // existing-subtype picker is optional; ignore failures
    try {
      const tissues = await loadTissues();
      $('#so-tissues').innerHTML = tissues.length
        ? tissues.map(t => `<label class="so-tissue-check"><input type="checkbox" value="${esc(t.iri)}"> ${esc(t.name)}</label>`).join('')
        : '<span class="muted">No tissues available</span>';
    } catch (e) { $('#so-tissues').innerHTML = '<span class="muted">Failed to load tissues: ' + esc(e.message) + '</span>'; }
  }

  async function submitSubtype(parentIri) {
    if (!me || !me.authenticated) { note('Sign in with GitHub first.', 'error'); return; }
    const val = id => ($('#' + id)?.value || '').trim();
    const label = val('so-label'), definition = val('so-definition'), defsrc = val('so-defsrc');
    const tissue_iris = [...document.querySelectorAll('#so-tissues input:checked')].map(c => c.value);
    // Every failure at once, next to its own field. This used to return on the
    // first one, so a curator missing four required fields got four separate
    // 2.6-second messages across four submit attempts (issue #99).
    const soBody = $('#so-body');
    const problems = [];
    if (!label)              problems.push({ id: 'so-label', message: 'Give the subtype a label.' });
    if (!definition)         problems.push({ id: 'so-definition', message: 'Write a definition.' });
    if (!defsrc)             problems.push({ id: 'so-defsrc', message: 'Cite where the definition came from — a URL or a PMID.' });
    if (!tissue_iris.length) problems.push({ id: 'so-tissues', message: 'Choose at least one target tissue.' });
    if (!UIDialog.showFieldErrors(soBody, problems)) return;
    const editor = val('so-editor') || (me && me.login) || 'curator';
    const data = {
      label, definition, def_source: [defsrc], tissue_iris, parent_iri: parentIri,
      synonyms: val('so-synonyms'), disease_category: val('so-category'),
      clinical_subtypes: val('so-clinical'),
    };
    const btn = $('#so-save');
    btn.disabled = true; btn.textContent = 'Creating…';
    try {
      const created = await api('disease', { method: 'POST', body: { data, editor } });
      ROWS = await api('xrefs');           // refresh so the new subtype appears in the matrix
      closeSubtypeOverlay();
      renderMatrix(); counts();
      // Creating a subtype here writes it to this curator's working copy — it does
      // not open a pull request, and nothing said so: the old message was
      // "Created subtype: X" and left the curator to guess whether it had been
      // submitted (issue #24). Say what happened, and where to submit it.
      const parent = ROWS.find(x => x.iri === parentIri);
      subtypeNote(created, parent);
    } catch (e) {
      note('Could not create the subtype: ' + e.message, 'error');
      btn.disabled = false; btn.textContent = '＋ Create subtype';
    }
  }

  // Save an edit to the panel's target id. The rest of the cell is left alone: the new
  // value replaces just this id, an empty value removes it, and an id that is not on
  // file yet (a prediction, or a first id for a blank cell) is added.
  //
  // The single id and what to do with it go to the server, which rebuilds the cell
  // from what is on file at that moment. This used to send the cell's whole new
  // contents, computed from the ids this window happened to be holding, so an id a
  // second window had added since page load was erased with no conflict and no
  // message (issue #114). A `replace` whose target has gone now fails loudly.
  async function saveId(iri, dbkey, targetId) {
    if (!me || !me.authenticated) { note('Sign in with GitHub first.', 'error'); return; }
    const r = ROWS.find(x => x.iri === iri);
    if (!r) return;
    const val = $('#p-ids').value.trim();
    const onFile = (r[dbkey] || []).map(String);
    const replacing = targetId != null && onFile.includes(String(targetId));
    const op = replacing ? (val ? 'replace' : 'remove') : 'add';
    if (op === 'add' && !val) { $('#p-cancel').click(); return; }   // nothing to do
    $('#p-save').disabled = true; $('#p-save').textContent = 'Saving…';
    try {
      const updated = await api('disease/' + enc(iri) + '/xref', { method: 'POST', body: {
        db: dbkey, op, value: op === 'remove' ? String(targetId) : val,
        replaces: replacing ? String(targetId) : '' } });
      const oldIds = r[dbkey] || [];
      const newIds = updated[dbkey] || [];
      r[dbkey] = newIds;
      // Only ids that actually changed are touched, so a sibling id in the same cell
      // keeps the verdict the curator already gave it. Clearing a new id's review
      // stops a just-saved id also publishing as a negative mapping.
      for (const id of newIds) {
        if (oldIds.includes(id)) continue;
        setSessionKey('edited', idKey(iri, dbkey, id), true);
        setSessionKey('reviewed', idKey(iri, dbkey, id), null);
      }
      for (const id of oldIds) {
        if (newIds.includes(id)) continue;
        setSessionKey('edited', idKey(iri, dbkey, id), null);
        setSessionKey('reviewed', idKey(iri, dbkey, id), null);
      }
      // The save just credited an id to someone in the authorship ledger, so pull
      // it again rather than guessing — the ledger keeps an id's *first* author.
      await loadIdAuthors();
      counts(); saveSession();
      // Stay on the id just saved when it survived, so editing the third id in a
      // cell doesn't bounce the curator back to the first.
      openPanel(iri, dbkey, newIds.includes(val) ? val : (newIds[0] || null));
      renderMatrix();
    } catch (e) { note('Could not save the id: ' + e.message, 'error'); $('#p-save').disabled = false; $('#p-save').textContent = 'Save'; }
  }

  // Publish the review as a pull request. `newPr` true opens a fresh PR carrying
  // only the verdicts published nowhere yet; otherwise the changes are committed
  // to the tracked PR (or a first PR is opened when none exists yet), whose title
  // then names every disease on that PR.
  async function publish(newPr) {
    const keys = publishKeys(newPr);
    if (!keys.size) return;
    const reuse = newPr ? null : sessionBranch;
    if (newPr && sessionPr && !await UIDialog.confirm({
        title: 'Start a new submission?',
        detail: `Your work is currently going into ${Words.submissionName(sessionPr.number)}. ` +
                'A new one carries only the verdicts not already published there.',
        confirmLabel: 'Start a new one',
        cancelLabel: 'Keep adding to ' + Words.submissionName(sessionPr.number),
      })) return;
    // A real textarea. This was window.prompt(): single-line, unstyled, easy to
    // dismiss by accident, and the only place a curator could say what they had
    // reviewed (issue #118).
    const comment = await UIDialog.text({
      title: 'Describe this submission',
      detail: `This becomes the ${Words.submission}'s description. Optional, but it is what `
            + 'a reviewer reads first.',
      label: 'What did you review or change?',
      value: 'Mappings review',
      placeholder: 'e.g. Checked every MONDO id for the skin diseases; flagged three that name a broader concept.',
      confirmLabel: Words.publish,
      multiline: true,
    });
    if (comment === null) return;
    // The author lands in the published SSSOM `author_id` column. The server
    // validates an ORCID and refuses a malformed one rather than writing a typo
    // into a permanent, citable record.
    const orcid = (localStorage.getItem('ari_editor_orcid') || '').trim();
    const author = orcid ? ('orcid:' + orcid) : (me && me.login ? ('github:' + me.login) : 'curator');
    const message = reviewMessage(keys);
    // One id per publish *attempt*. If the commit and PR succeed but the response
    // is lost — a proxy timeout, a closed laptop — retrying with the same id
    // returns the original result instead of committing the judgments twice.
    if (!pendingPublishId) pendingPublishId = 'pub_' + Date.now().toString(36) +
      '_' + Math.random().toString(36).slice(2, 10);
    $('#publish').disabled = true; $('#publish-new').disabled = true; $('#publish').textContent = 'Publishing…';
    try {
      const r = await api('publish', { method: 'POST', body: {
        disease: 'mappings review', message, comment,
        confirmed: confirmedList(keys), flagged: flaggedList(keys),
        absent: absentList(keys), author,
        apply_enrichment: applyEnrichment,
        // Only what the curator left ticked. Sent whenever enrichment is on, so
        // declining three of eleven proposals no longer means declining all.
        enrichment_selection: applyEnrichment ? enrichSelection() : null,
        request_id: pendingPublishId,
        branch: reuse, labels: ['edit term', 'sssom'] } });
      pendingPublishId = null;                        // this attempt is settled
      sessionBranch = r.branch;                       // subsequent publishes append to the same PR
      sessionPr = { number: r.pr_number, url: r.pr_url, fork: r.fork };
      for (const k of keys) setSessionKey('published', k, { pr: r.pr_number, state: keyState(k) });
      // A PR this branch used to feed has been merged or closed, so this work
      // went into a new one. Say which, rather than leaving the header pointing
      // at a number the curator's work is no longer in.
      if (r.superseded && r.superseded.length){
        const old = r.superseded[0];
        note(`PR #${old.number} was ${old.merged ? 'merged' : 'closed'}, so this went into ` +
             `a new submission, PR #${r.pr_number}.`);
      }
      reflectPr();
      counts();                                       // published work leaves the pending set
      saveSession(true);                              // persist the PR pointer immediately
    } catch (e) {
      // pendingPublishId is deliberately kept: the next click retries the SAME
      // attempt, which the server can recognise if the first one actually landed.
      note('Publish failed: ' + e.message, 'error'); reflectPr(); counts();
    }
  }

  // What creating a subtype actually did, and what is left to do. The record is
  // in this session's working copy along with everything else, so it goes out
  // with the next submission from the editor — no redirect needed, which is the
  // point: a curator can carry on reviewing and submit later.
  function subtypeNote(created, parent) {
    const el = $('#note');
    const editorUrl = new URL('../#/disease/' + enc(created.iri), location.href).href;
    el.innerHTML =
      `<strong>${esc(created.name)}</strong> created` +
      (parent ? ` as a clinical subtype of <strong>${esc(parent.name)}</strong>` : '') +
      ` (${esc(created.ari_id || '')}). It is in your working copy, and both records ` +
      `now carry a changelog entry saying so. It goes out with your next submission — ` +
      `<a href="${esc(editorUrl)}" target="_blank" rel="noopener">open it in the editor</a> ` +
      `to fill in the rest or submit.`;
    // Same banner as note(), but the message carries a link, so it sets innerHTML
    // rather than text. It still has to leave the element in the state note()
    // would: the success class, a status role, and the message announced.
    el.classList.remove('is-error');
    el.classList.add('open', 'is-ok');
    el.setAttribute('role', 'status');
    UIDialog.announce(el.textContent);
    clearTimeout(_noteTimer);
    _noteTimer = setTimeout(() => el.classList.remove('open', 'is-ok'), 14000);
  }

  // Draggable splitter — adjust side-panel width on all screens (mouse + touch).
  function initDivider() {
    const div = $('#divider'), body = document.querySelector('.body'), side = $('#side');
    let dragging = false;
    const move = e => {
      if (!dragging) return;
      const x = (e.touches ? e.touches[0].clientX : e.clientX);
      const rect = body.getBoundingClientRect();
      let w = rect.right - x;
      w = Math.max(300, Math.min(rect.width - 200, w));
      side.style.width = w + 'px';
    };
    const start = e => { dragging = true; document.body.classList.add('dragging'); e.preventDefault(); };
    const end = () => { dragging = false; document.body.classList.remove('dragging'); };
    div.addEventListener('mousedown', start); div.addEventListener('touchstart', start, { passive: false });
    window.addEventListener('mousemove', move); window.addEventListener('touchmove', move, { passive: false });
    window.addEventListener('mouseup', end); window.addEventListener('touchend', end);
  }

  // Draggable boundary between the disease column and the first database column —
  // small screens clip long disease names, so the column widens to half the matrix.
  function initColGrip() {
    let dragging = false;
    const move = e => {
      if (!dragging) return;
      const x = (e.touches ? e.touches[0].clientX : e.clientX);
      diseaseW = x - $('#matrix-inner').getBoundingClientRect().left;
      applyGrid();
      e.preventDefault();
    };
    const end = () => {
      if (!dragging) return;
      dragging = false; document.body.classList.remove('dragging');
      setWinPref('refDiseaseW', String(Math.round(diseaseW)));
    };
    const start = e => { dragging = true; document.body.classList.add('dragging'); e.preventDefault(); };
    const grip = $('#colgrip');
    grip.addEventListener('mousedown', start);
    grip.addEventListener('touchstart', start, { passive: false });
    grip.addEventListener('dblclick', () => {
      diseaseW = null; applyGrid();
      try { sessionStorage.removeItem('refDiseaseW'); localStorage.removeItem('refDiseaseW'); }
      catch (err) { /* storage may be unavailable */ }
    });
    window.addEventListener('mousemove', move); window.addEventListener('touchmove', move, { passive: false });
    window.addEventListener('mouseup', end); window.addEventListener('touchend', end);
    // Anything that changes the matrix's width re-runs the column budget: the
    // review panel opening or closing, the divider being dragged, the window
    // resizing. One observer covers all three, and the width it reports is the
    // settled one — #side animates its width, so reading it on the click is early.
    new ResizeObserver(() => { applyGrid(); measureHead(); }).observe($('#matrix-wrap'));
  }

  // ------------------------------------------------------- HEADER CONTROLS
  // Toolbar popovers (preferences, publish options). One is open at a time, and a
  // click outside or Escape closes it — the button keeps aria-expanded in step.
  const MENUS = [['#prefs-btn', '#prefs-menu'], ['#publish-more', '#publish-menu']];

  function closeMenus(except) {
    for (const [btn, menu] of MENUS) {
      if (menu === except) continue;
      $(menu).classList.remove('open');
      $(btn).setAttribute('aria-expanded', 'false');
    }
  }

  function initMenus() {
    for (const [btn, menu] of MENUS) {
      $(btn).addEventListener('click', e => {
        e.stopPropagation();
        const open = !$(menu).classList.contains('open');
        closeMenus(open ? menu : null);
        $(menu).classList.toggle('open', open);
        $(btn).setAttribute('aria-expanded', String(open));
      });
      // A click inside the panel adjusts a preference; only the publish menu's
      // items are one-shot actions, and those close it through their own handlers.
      $(menu).addEventListener('click', e => e.stopPropagation());
    }
    document.addEventListener('click', () => closeMenus());
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenus(); });
  }

  function syncSegs() {
    const th = document.documentElement.dataset.theme || 'light';
    const de = document.documentElement.dataset.density || 'comfortable';
    const ts = document.documentElement.dataset.textsize || 'standard';
    document.querySelectorAll('#theme button').forEach(b => b.classList.toggle('on', b.dataset.theme === th));
    document.querySelectorAll('#density button').forEach(b => b.classList.toggle('on', b.dataset.density === de));
    document.querySelectorAll('#textsize button').forEach(b => b.classList.toggle('on', b.dataset.textsize === ts));
    document.querySelectorAll('#queue-filter button').forEach(b => b.classList.toggle('on', b.dataset.queue === queueFilter));
    $('#pref-legend').checked = document.documentElement.dataset.legend !== 'off';
  }

  function initControls() {
    initMenus();
    // ORCID: what lands in the published SSSOM author_id column. Validated on
    // the way out too, by the server — a typo here is permanent and
    // unattributable, so a malformed one is refused rather than published.
    const orcid = $('#pref-orcid');
    orcid.value = localStorage.getItem('ari_editor_orcid') || '';
    const saveOrcid = () => {
      const v = orcid.value.trim();
      if (v && !/^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$/.test(v)) {
        orcid.setAttribute('aria-invalid', 'true');
        note('An ORCID looks like 0000-0000-0000-0000 (the last character may be X).', 'error');
        return;
      }
      orcid.removeAttribute('aria-invalid');
      if (v) localStorage.setItem('ari_editor_orcid', v);
      else localStorage.removeItem('ari_editor_orcid');
      note(v ? 'Mappings will be attributed to ORCID ' + v : 'Cleared — using your GitHub login.', 'ok');
    };
    orcid.addEventListener('change', saveOrcid);
    orcid.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); saveOrcid(); } });

    $('#pref-legend').addEventListener('change', e => {
      const v = e.target.checked ? 'on' : 'off';
      document.documentElement.dataset.legend = v;
      try { localStorage.setItem('refLegend', v); } catch (err) {}
    });
    $('#theme').addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      document.documentElement.dataset.theme = b.dataset.theme;
      try { localStorage.setItem('theme', b.dataset.theme); } catch (err) {}
      syncSegs();
    });
    $('#density').addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      document.documentElement.dataset.density = b.dataset.density;
      setWinPref('refDensity', b.dataset.density);
      applyGrid(); syncSegs();
    });
    $('#help-btn').addEventListener('click', () => Help.open());
    $('#firstrun').addEventListener('click', e => {
      const b = e.target.closest('[data-firstrun]');
      if (b) { toggleMissingOnly(b.dataset.firstrun); return; }
      if (e.target.closest('[data-firstrun-help]')) Help.open();
    });
    // Text size shares one key with the editor, so the choice carries across both
    // pages rather than being made twice (issue #94).
    $('#textsize').addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      document.documentElement.dataset.textsize = b.dataset.textsize;
      try { localStorage.setItem('ari-textsize', b.dataset.textsize); } catch (err) {}
      applyGrid(); measureHead(); syncSegs();
    });
    $('#pending-chip').addEventListener('click', () => reflectDrawer('#pending-chip', '#pending-panel'));
    $('#enrich-chip').addEventListener('click', openEnrich);
    $('#en-apply').addEventListener('change', e => {
      applyEnrichment = e.target.checked;
      $('#enrich-dot').classList.toggle('live', applyEnrichment);
    });
    // One delegated handler for the whole matrix — 200+ rows x 10 cells is far too
    // many nodes to bind individually, and the matrix re-renders on every verdict.
    $('#matrix').addEventListener('click', e => {
      if (e.target.closest('.copen')) return;                 // let the link open normally
      const sel = e.target.closest('.mselect');
      if (sel) { toggleSelect(sel.dataset.sel, e.shiftKey); return; }   // never expands the row
      const btn = e.target.closest('.cbtn');
      if (btn) { setReview(btn.dataset.iri, btn.dataset.db, btn.dataset.id, btn.dataset.v); return; }
      const nb = e.target.closest('.cnone');
      if (nb) { setAbsent(nb.dataset.iri, nb.dataset.db); return; }
      const cp = e.target.closest('[data-copy]');
      if (cp) { copyLink(cp); return; }
      const sub = e.target.closest('[data-subtype]');
      if (sub) { openSubtypeOverlay(sub.dataset.subtype); return; }
      const add = e.target.closest('.card-add');
      if (add) { openEntry(add.dataset.iri, add.dataset.db, null); return; }
      const cid = e.target.closest('.card-id');
      if (cid) { openEntry(cid.dataset.iri, cid.dataset.db, cid.dataset.id); return; }
      const cell = e.target.closest('.mcell');
      if (cell) { openEntry(cell.dataset.iri, cell.dataset.db, null); return; }
      const row = e.target.closest('.mrow');
      if (row) toggleRow(row.dataset.iri);
    });
  }

  // Who added each id on file. Failure is non-fatal — the frontend lock relaxes,
  // but /api/v2/publish re-checks authorship server-side, so nothing slips through.
  //
  // The endpoint requires a session (it is the evidence base for separation of
  // duties). A signed-out viewer cannot confirm anything, so the ledger would
  // change nothing for them — don't ask for it and log a 401 on every load.
  async function loadIdAuthors() {
    if (!(me && me.authenticated)) { idAuthors = {}; return; }
    try { idAuthors = await api('id-authors'); }
    catch (e) { console.warn('Could not load id authorship:', e.message); idAuthors = {}; }
  }

  async function init() {
    try { me = await api('me'); } catch (e) { me = { github_enabled: false, authenticated: false }; }
    $('#auth').innerHTML = !me.authenticated
      ? (me.github_enabled ? `<a class="btn" href="${new URL('../auth/github?next=' + encodeURIComponent(location.pathname + location.search), location.href).href}">Sign in with GitHub</a>` : '<span class="muted">GitHub off — review only</span>')
      : `<span class="muted">@${esc(me.login)}</span>`;
    try { buildDatabases(await api('xref-databases')); }
    catch (e) { $('#matrix').innerHTML = '<p class="muted" style="padding:16px">Failed to load the database registry: ' + esc(e.message) + '</p>'; return; }
    try { ROWS = await api('xrefs'); } catch (e) { $('#matrix').innerHTML = '<p class="muted" style="padding:16px">Failed to load: ' + esc(e.message) + '</p>'; return; }
    // Pre-existing curated judgments pre-highlight cells; failure is non-fatal.
    try {
      mappings = {};
      for (const m of await api('mappings')) mappings[m.ari_id + '|' + m.prefix + '|' + m.id] = m.judgment;
    } catch (e) { mappings = {}; }
    // Predicted exact-match candidates for blank cells (issue #42); non-fatal.
    try {
      predicted = {};
      for (const p of await api('predictions'))
        predicted[p.ari_id + '|' + p.prefix + '|' + p.id] = {
          label: p.object_label, match_field: p.match_field, confidence: p.confidence,
          score: p.score, band: p.band };
    } catch (e) { predicted = {}; }
    await loadIdAuthors();
    // Restore this user's saved review session (verdicts + PR pointer) so a reload
    // resumes their work. Anonymous users get {} — review state stays in-memory.
    if (me && me.authenticated) {
      try {
        const s = await api('ref-session');
        reviewed = s.reviewed || {};
        edited = s.edited || {};
        published = s.published || {};
        sessionBranch = s.branch || null;
        sessionPr = s.pr || null;
      } catch (e) { console.warn('Could not load review session:', e.message); }
    }
    // Who holds which disease — drives the owner badges and the queue filter.
    await loadOwners();
    reflectPr();
    // This window's layout. The pre-paint script in index.html sets data-density
    // too, from the shared default, so the first frame is close — but the value
    // that stands is this one, read session-first (see winPref).
    document.documentElement.dataset.density = winPref('refDensity') || 'comfortable';
    diseaseW = Number(winPref('refDiseaseW')) || null;
    applyGrid(); syncSegs(); renderHead(); renderMatrix(); counts();
    applyHash();                       // open whatever the URL names
    initDivider(); initColGrip(); initControls(); initQueue();
    $('#filter').addEventListener('input', renderMatrix);
    $('#mhead').addEventListener('click', e => {
      const miss = e.target.closest('[data-miss]');
      if (miss) { toggleMissingOnly(miss.dataset.miss); return; }
      const sort = e.target.closest('[data-sort]');
      if (sort) cycleSort(sort.dataset.sort);
    });
    $('#publish').addEventListener('click', () => publish(false));
    $('#publish-new').addEventListener('click', () => { closeMenus(); publish(true); });
    $('#prlink').addEventListener('click', () => closeMenus());
  }
  // -------------------------------------------------------------- KEYBOARD
  // A roving tabindex over the matrix: one cell in the tab order, arrow keys to
  // move, Enter to open the review panel, and single-key verdicts. Arrow-key
  // movement is also the fastest way for a mouse user to work through ~1,900
  // cells (issue #100).
  function matrixCells(){ return [...$('#matrix').querySelectorAll('.mcell')]; }

  function primeMatrixTabStop(){
    const cells = matrixCells();
    if (!cells.length) return;
    const current = cells.find(c => c.tabIndex === 0) || cells[0];
    cells.forEach(c => c.tabIndex = c === current ? 0 : -1);
  }

  function focusCell(cell){
    if (!cell) return;
    matrixCells().forEach(c => c.tabIndex = -1);
    cell.tabIndex = 0;
    cell.focus();
    cell.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function moveFocus(from, dCol, dRow){
    const cells = matrixCells();
    const i = cells.indexOf(from);
    if (i < 0) return;
    const cols = DBS.length;
    const col = i % cols, row = Math.floor(i / cols);
    const nc = Math.min(cols - 1, Math.max(0, col + dCol));
    const nr = Math.min(Math.floor((cells.length - 1) / cols), Math.max(0, row + dRow));
    focusCell(cells[nr * cols + nc]);
  }

  $('#matrix').addEventListener('keydown', e => {
    const cell = e.target.closest('.mcell');
    if (!cell) return;
    switch (e.key) {
      case 'ArrowRight': e.preventDefault(); moveFocus(cell, 1, 0); return;
      case 'ArrowLeft':  e.preventDefault(); moveFocus(cell, -1, 0); return;
      case 'ArrowDown':  e.preventDefault(); moveFocus(cell, 0, 1); return;
      case 'ArrowUp':    e.preventDefault(); moveFocus(cell, 0, -1); return;
      case 'Enter':
      case ' ':
        e.preventDefault(); cell.click(); return;
    }
    // Single-key verdicts, only once a cell's panel is the active one — the
    // verdict buttons are the authority, so this just clicks them and inherits
    // every rule they enforce, including the separation-of-duties gate.
    const keyed = { y: '#p-ok', n: '#p-bad', d: '#p-none' }[e.key.toLowerCase()];
    if (keyed && active && active.iri === cell.dataset.iri && active.dbkey === cell.dataset.db){
      const btn = $(keyed);
      if (btn && !btn.disabled){ e.preventDefault(); btn.click(); }
    }
  });

  $('#matrix').addEventListener('focusin', e => {
    const cell = e.target.closest('.mcell');
    if (cell) matrixCells().forEach(c => c.tabIndex = c === cell ? 0 : -1);
  });

  document.addEventListener('DOMContentLoaded', init);
})();
