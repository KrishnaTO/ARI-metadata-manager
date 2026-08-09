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
      sessionBranch = null, sessionPr = null, _tissues = null, openRow = null;
  // reviewed/edited keys: `${iri}|${db}|${id}` (per-ID, not per-cell)
  const idKey = (iri, db, id) => iri + '|' + db + '|' + id;
  // Pre-existing curated judgments keyed `${ari_id}|${prefix}|${id}` -> 'positive'|'negative'.
  let mappings = {};
  // Predicted matches (issue #42) keyed `${ari_id}|${prefix}|${id}` ->
  // {label, match_field, confidence}. From /api/v2/predictions: exact name/synonym
  // hits for blank cells. confidence 'high' = the disease label matched a concept;
  // 'low' = only a synonym matched (label matched nothing) — worth a closer look.
  let predicted = {};

  // Predicted candidate ids for a currently-blank (disease, db) cell. Returns
  // [{id, label, match_field, confidence}], skipping any id already flagged negative.
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

  // Per-ID pre-judgment from the curated mappings: 'pos' | 'neg' | null.
  function preJudgmentId(r, dbkey, id) {
    const ari = r.ari_id, prefix = PREFIX[dbkey];
    if (!ari || !prefix) return null;
    const j = mappings[ari + '|' + prefix + '|' + id];
    if (j === 'positive') return 'pos';
    if (j === 'negative') return 'neg';
    return null;
  }

  const $ = s => document.querySelector(s);

  // ------------------------------------------------------------- STATE MODEL
  // Per-id state, the single thing the glyphs, tints and counts are derived from:
  //   ok    confirmed this session, or positive in the curated mappings
  //   bad   flagged this session, or negative in the curated mappings
  //   pred  lexical prediction for a blank cell (the disease label matched a concept)
  //   low   lexical prediction from a synonym only
  //   have  an id on file that nobody has judged yet
  function idState(r, dbkey, id, pred) {
    const k = idKey(r.iri, dbkey, id);
    if (reviewed[k] === 'ok') return 'ok';
    if (reviewed[k] === 'bad') return 'bad';
    const pre = preJudgmentId(r, dbkey, id);
    if (pre === 'pos') return 'ok';
    if (pre === 'neg') return 'bad';
    return pred ? (pred.confidence === 'low' ? 'low' : 'pred') : 'have';
  }

  // Everything in one cell: the ids on file, or — for a blank cell — its predictions.
  function cellEntries(r, dbkey) {
    const ids = r[dbkey] || [];
    if (ids.length) return ids.map(id => ({ id, pred: null }));
    return predFor(r, dbkey).map(p => ({ id: p.id, pred: p }));
  }

  // A cell only reads ✓ once every id in it has a verdict, so any unjudged entry wins.
  function cellState(r, dbkey) {
    const sts = cellEntries(r, dbkey).map(e => idState(r, dbkey, e.id, e.pred));
    if (!sts.length) return null;
    if (sts.includes('pred')) return 'pred';
    if (sts.includes('low')) return 'low';
    if (sts.includes('have')) return 'have';
    if (sts.includes('ok')) return 'ok';
    return 'bad';
  }

  const isOpenState = st => st === 'pred' || st === 'low' || st === 'have';
  // A disease is complete when nothing in its row is still awaiting a verdict.
  const isComplete = r => DBS.every(db => !isOpenState(cellState(r, db.key)));

  const GLYPH = { ok: '✓', bad: '✕', pred: '●', low: '○', have: '•' };
  const SUP = { 2: '²', 3: '³', 4: '⁴', 5: '⁵' };
  const TAG = { ok: 'confirmed', bad: 'flagged', pred: 'predicted', low: 'synonym', have: 'on file' };

  // Concept labels for ids on file (predictions already carry theirs). Filled lazily
  // for the open row only, then painted in place so a re-render never blocks on them.
  const idLabels = {};
  function labelKey(db, id) { return db + '|' + id; }
  function paintLabel(k) {
    const v = idLabels[k];
    if (!v) return;
    document.querySelectorAll(`.card-id[data-lk="${CSS.escape(k)}"] .clabel`).forEach(el => { el.textContent = v; });
  }
  function fillCardLabels(r) {
    for (const db of DBS) {
      for (const id of (r[db.key] || [])) {
        const k = labelKey(db.key, id);
        if (k in idLabels) { paintLabel(k); continue; }
        idLabels[k] = '';                       // in flight — don't look it up twice
        conceptFor(db.key, id).then(c => {
          idLabels[k] = (c && c.found && c.label) ? c.label : '';
          paintLabel(k);
        });
      }
    }
  }

  // Fold a string the way app/predict_service.normalize() does (NFKD, drop combining
  // marks, casefold, collapse non-alphanumerics), so the compare pane's string-match
  // highlighting agrees with what the matcher actually treats as the same name.
  const normTxt = s => (s == null ? '' : String(s)).normalize('NFKD')
    .replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^0-9a-z]+/g, ' ').trim();

  // Session caches for the compare pane, keyed `${db}|${id}` (concept lookups) and by
  // iri (ARI disease detail). The pane never blocks the verdict buttons on these.
  const conceptCache = {}, diseaseCache = {};

  async function conceptFor(db, id) {
    const key = db + '|' + id;
    if (!(key in conceptCache)) {
      conceptCache[key] = api('concept/' + enc(db) + '/' + enc(id)).catch(() => null);
    }
    return conceptCache[key];
  }
  async function diseaseFor(iri) {
    if (!(iri in diseaseCache)) diseaseCache[iri] = api('disease/' + enc(iri)).catch(() => null);
    return diseaseCache[iri];
  }

  // Render a value list, wrapping any entry whose normalized form is in `hits`
  // (the other column's strings) in `.hit` so the shared strings line up visually.
  function cmpList(values, hits) {
    if (!values || !values.length) return '<span class="cmp-empty">—</span>';
    return values.map(v => {
      const cls = hits.has(normTxt(v)) ? ' class="hit"' : '';
      return `<span${cls}>${esc(v)}</span>`;
    }).join('');
  }

  function cmpColumn(title, sub, label, synonyms, definition, parents, hits, note) {
    const labelCls = label && hits.has(normTxt(label)) ? ' hit' : '';
    return `<div class="cmp-col">
      <div class="cmp-h">${esc(title)}${sub ? ` <span class="cmp-sub">${esc(sub)}</span>` : ''}</div>
      <div class="cmp-label"><span class="${labelCls.trim()}">${label ? esc(label) : '<span class="cmp-empty">—</span>'}</span></div>
      ${note ? `<div class="cmp-note">${esc(note)}</div>` : ''}
      <div class="cmp-field"><span class="cmp-k">Synonyms</span>${cmpList(synonyms, hits)}</div>
      <div class="cmp-field"><span class="cmp-k">Definition</span>${definition ? esc(definition) : '<span class="cmp-empty">—</span>'}</div>
      <div class="cmp-field"><span class="cmp-k">Parents</span>${cmpList(parents, hits)}</div>
    </div>`;
  }

  // Fill the panel's compare pane: ARI disease (left) vs the target concept (right),
  // mirrored so the two read as a side-by-side "same disease?" judgement. Runs after
  // the panel is wired, so the verdict buttons stay live while it loads.
  async function fillCompare(dbkey, targetId, r) {
    const host = $('#p-compare');
    if (!host) return;
    const db = DBMAP[dbkey];
    const [concept, detail] = await Promise.all([conceptFor(dbkey, targetId), diseaseFor(r.iri)]);
    if (!$('#p-compare') || !active || active.iri !== r.iri || active.dbkey !== dbkey) return;

    const ariSyn = (r.synonyms || []).slice();
    const ariDef = detail ? (detail.definition || '') : '';
    const ariParents = detail ? (detail.parent_disease || []).map(p => p.name || p.label || '').filter(Boolean) : [];
    const ariStrings = new Set([r.name, ...ariSyn, ...ariParents].map(normTxt).filter(Boolean));

    let right;
    if (!concept) {
      right = `<div class="cmp-col"><div class="cmp-h">${esc(db.label)}</div>
        <div class="cmp-note">Couldn't load this concept — <a href="${esc(db.search(r.name))}" target="_blank" rel="noopener">search ${esc(db.label)} ↗</a>.</div></div>`;
    } else if (!concept.found) {
      right = `<div class="cmp-col"><div class="cmp-h">${esc(db.label)}</div>
        <div class="cmp-note">${esc(concept.note || 'Not in our indexes.')} <a href="${esc(db.search(r.name))}" target="_blank" rel="noopener">Open the source ↗</a></div></div>`;
    } else {
      // Honesty: when the id isn't the database's own term, label the column for the
      // hub it actually came from ("via MONDO") and show the caveat, never as if the
      // target database supplied it.
      const via = concept.via && concept.via.length
        ? 'via ' + concept.via.map(v => v.source.toUpperCase()).join(' / ') : '';
      const title = concept.direct ? db.label : (via || db.label);
      const cSyn = concept.synonyms || [], cParents = concept.parents || [];
      const conStrings = new Set([concept.label, ...cSyn, ...cParents].map(normTxt).filter(Boolean));
      const right2 = cmpColumn(title, concept.id, concept.label, cSyn, concept.definition,
        cParents, ariStrings, concept.direct ? '' : (concept.note || ''));
      host.innerHTML = `<div class="cmp">
        ${cmpColumn('ARI', r.ari_id || '', r.name, ariSyn, ariDef, ariParents, conStrings, '')}
        ${right2}</div>`;
      return;
    }
    host.innerHTML = `<div class="cmp">
      ${cmpColumn('ARI', r.ari_id || '', r.name, ariSyn, ariDef, ariParents, new Set(), '')}
      ${right}</div>`;
  }

  function reviewMessage() {
    const iris = new Set();
    Object.keys(edited).forEach(k => iris.add(k.split('|')[0]));
    for (const [k, v] of Object.entries(reviewed)) if (v === 'ok') iris.add(k.split('|')[0]);
    const ari = [...iris].map(i => (ROWS.find(x => x.iri === i) || {}).ari_id).filter(Boolean).sort();
    let lab = ari.slice(0, 6).join(', ');
    if (ari.length > 6) lab += ', +' + (ari.length - 6) + ' more';
    return '[' + (lab || 'cross-references') + '] - mappings review';
  }

  // Collect this session's reviewed cells of a given verdict ('ok' positives /
  // 'bad' negatives) into the {ari_id, iri, name, db, ids} shape publish wants.
  function reviewedCells(verdict) {
    const cellMap = {};
    for (const [k, v] of Object.entries(reviewed)) {
      if (v !== verdict) continue;
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
  const confirmedList = () => reviewedCells('ok');
  const flaggedList = () => reviewedCells('bad');

  // This session's unpublished work, grouped by disease, for the pending drawer.
  function pendingByDisease() {
    const by = {};
    const bump = (iri, f) => { (by[iri] = by[iri] || { ok: 0, bad: 0, ed: 0 })[f]++; };
    for (const [k, v] of Object.entries(reviewed)) if (v === 'ok' || v === 'bad') bump(k.split('|')[0], v);
    for (const k of Object.keys(edited)) bump(k.split('|')[0], 'ed');
    return Object.entries(by).map(([iri, c]) => {
      const r = ROWS.find(x => x.iri === iri);
      return {
        name: r ? r.name : iri,
        summary: [c.ok ? c.ok + ' confirmed' : null, c.bad ? c.bad + ' flagged' : null,
                  c.ed ? c.ed + ' edited' : null].filter(Boolean).join(' · '),
      };
    });
  }

  function counts() {
    const ok = Object.values(reviewed).filter(v => v === 'ok').length;
    const bad = Object.values(reviewed).filter(v => v === 'bad').length;
    const ed = Object.keys(edited).length;
    const conf = confirmedList().length, flag = flaggedList().length;
    const done = ROWS.filter(isComplete).length;
    $('#done-n').textContent = done;
    $('#total-n').textContent = ROWS.length;
    $('#progress-bar').style.width = ROWS.length ? Math.round(done / ROWS.length * 100) + '%' : '0%';
    const pend = pendingByDisease();
    $('#pending-count').textContent = pend.length;
    $('#pending-dot').classList.toggle('live', pend.length > 0);
    $('#pending-chip').title = `confirmed ${ok} · flagged ${bad} · edited ${ed}`;
    $('#pending-list').innerHTML = pend.length
      ? pend.map(p => `<div class="pending-row"><span>${esc(p.name)}</span><span>${esc(p.summary)}</span></div>`).join('')
      : '<div class="pending-row"><span class="muted">No verdicts yet. Open a disease and judge a mapping.</span></div>';
    const canPublish = !!(me && me.authenticated && (ed > 0 || conf > 0 || flag > 0));
    $('#publish').disabled = !canPublish;
    $('#publish-new').disabled = !canPublish;
  }

  // Persist this signed-in user's review session (verdicts, edited-id markers and
  // the PR pointer) to the server so a page reload resumes where they left off.
  // Debounced; anonymous users keep review state in-memory only (no persistence).
  let _saveTimer = null;
  function saveSession(immediate) {
    if (!(me && me.authenticated)) return;
    clearTimeout(_saveTimer);
    const put = () => {
      const reviewedClean = {};
      for (const [k, v] of Object.entries(reviewed)) if (v) reviewedClean[k] = v;
      api('ref-session', { method: 'PUT', body: { reviewed: reviewedClean, edited, branch: sessionBranch, pr: sessionPr } })
        .catch(e => console.warn('Could not save review session:', e.message));
    };
    if (immediate) put(); else _saveTimer = setTimeout(put, 500);
  }

  // Reflect the tracked PR in the header: link + button labels. With a PR on file
  // the primary button appends to it and a secondary "New PR" button is offered;
  // with none, the primary button opens the first PR.
  function reflectPr() {
    const pl = $('#prlink');
    if (sessionPr) {
      pl.textContent = 'PR #' + sessionPr.number + (sessionPr.fork ? ' (from your fork) ↗' : ' ↗');
      pl.href = sessionPr.url; pl.style.display = '';
      $('#publish').textContent = 'Publish to PR #' + sessionPr.number;
      $('#publish-new').style.display = '';
    } else {
      pl.style.display = 'none';
      $('#publish').textContent = 'Publish review (PR)';
      $('#publish-new').style.display = 'none';
    }
  }

  // ----------------------------------------------------------------- MATRIX
  // Column tracks: a fixed disease column plus one equal track per database. The 44px
  // floor is what makes the matrix scroll instead of collapse when the panel opens.
  function applyGrid() {
    const compact = document.documentElement.dataset.density === 'compact';
    document.documentElement.style.setProperty('--grid-cols',
      (compact ? '250px' : '330px') + ' repeat(' + DBS.length + ', minmax(44px,1fr))');
  }

  function renderHead() {
    $('#mhead').innerHTML = '<div>Disease</div>' + DBS.map(d => `<div>${esc(d.label)}</div>`).join('');
  }

  function visibleRows() {
    const q = ($('#filter').value || '').trim().toLowerCase();
    if (!q) return ROWS;
    return ROWS.filter(r => (r.name || '').toLowerCase().includes(q) ||
      (r.synonyms || []).some(s => String(s).toLowerCase().includes(q)) ||
      DBS.some(db => cellEntries(r, db.key).some(e => String(e.id).toLowerCase().includes(q))));
  }

  // One database card in the open row's review strip: the state tag, each id with its
  // ✓/✕ verdict and a link out, and an "add another id" affordance.
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
      return `<div class="card-id${sel ? ' sel' : ''}${edited[k] ? ' edited' : ''}" data-lk="${esc(labelKey(db.key, e.id))}"${at}${e.pred ? ' data-pred="1"' : ''}>
        <div class="cid">${esc(e.id)}</div>
        <div class="clabel">${esc(label)}</div>
        <div class="cacts">
          <button class="cbtn ok${ist === 'ok' ? ' on' : ''}" data-v="ok"${at} title="Confirm this mapping">✓</button>
          <button class="cbtn bad${ist === 'bad' ? ' on' : ''}" data-v="bad"${at} title="Flag this mapping as wrong">✕</button>
          <a class="copen" href="${esc(db.link(e.id))}" target="_blank" rel="noopener" title="Open in ${esc(db.label)}">↗</a>
        </div></div>`;
    }).join('');
    return `<div class="card${st ? ' ' + st : ''}">
      <div class="card-h"><span class="card-db">${esc(db.label)}</span><span class="card-tag${st ? ' ' + st : ''}">${esc(tag)}</span></div>
      ${ids}${entries.length ? '' : '<div class="card-empty">no id yet</div>'}
      <div class="card-add" data-iri="${esc(r.iri)}" data-db="${db.key}">+ add another id</div>
    </div>`;
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
        return `<div class="mcell${st ? ' ' + st : ''}${sel ? ' sel' : ''}${anyEdited ? ' edited' : ''}"
          data-iri="${esc(r.iri)}" data-db="${db.key}" title="${esc(title)}"><span>${st ? GLYPH[st] : ''}</span><span class="sup">${sup}</span></div>`;
      }).join('');
      let strip = '';
      if (open) {
        const openN = DBS.reduce((n, db) => n + cellEntries(r, db.key)
          .filter(e => isOpenState(idState(r, db.key, e.id, e.pred))).length, 0);
        const none = states.filter(s => !s).length;
        const multi = DBS.filter(db => cellEntries(r, db.key).length > 1).length;
        const summary = `${okN} confirmed · ${openN} ids awaiting review · ${none} databases with no id` +
          (multi ? ` · ${multi} with several ids` : '');
        strip = `<div class="strip">
          <div class="strip-head">
            <span class="strip-title">${esc(r.name)}</span>
            <span class="strip-id">${esc(r.ari_id || '')}</span>
            <span class="strip-syn">${esc((r.synonyms || []).join(' · '))}</span>
            <span style="flex:1"></span>
            <button class="btn" data-subtype="${esc(r.iri)}">＋ New subtype</button>
            <span class="strip-sum">${esc(summary)}</span>
          </div>
          <div class="cards">${DBS.map(db => cardHtml(r, db)).join('')}</div>
        </div>`;
      }
      h += `<div class="mgroup${open ? ' open' : ''}">
        <div class="mrow" data-iri="${esc(r.iri)}">
          <div class="mname"><span class="mcaret">${open ? '▾' : '▸'}</span>
            <span class="mname-text">${esc(r.name)}</span>
            <span class="mcount">${okN}/${DBS.length}</span></div>
          ${cells}
        </div>${strip}</div>`;
    }
    $('#matrix').innerHTML = h || '<div class="empty-note">No disease or id matches that filter.</div>';
    if (openRow) {
      const r = ROWS.find(x => x.iri === openRow);
      if (r) fillCardLabels(r);
    }
  }

  function toggleRow(iri) { openRow = openRow === iri ? null : iri; renderMatrix(); }

  // Open one mapping in the side panel, expanding its disease so the strip agrees
  // with what the panel is showing.
  function openEntry(iri, dbkey, id) {
    openRow = iri;
    openPanel(iri, dbkey, id);
    renderMatrix();
  }

  // Step to the next mapping anywhere in the matrix that still needs a verdict.
  function nextOpen() {
    const flat = [];
    for (const r of ROWS) for (const db of DBS)
      for (const e of cellEntries(r, db.key)) flat.push({ r, db: db.key, id: e.id, pred: e.pred });
    if (!flat.length) return;
    const at = active ? flat.findIndex(x => x.r.iri === active.iri && x.db === active.dbkey &&
                                             String(x.id) === String(active.id)) : -1;
    for (let n = 1; n <= flat.length; n++) {
      const c = flat[(at + n) % flat.length];
      if (isOpenState(idState(c.r, c.db, c.id, c.pred))) { openEntry(c.r.iri, c.db, c.id); return; }
    }
  }

  function setReview(iri, db, id, v) {
    const key = idKey(iri, db, id);
    if (reviewed[key] === v) delete reviewed[key]; else reviewed[key] = v;
    counts(); saveSession(); renderMatrix();
    // Keep the panel's own verdict buttons in step without rebuilding it (which
    // would reload the preview iframe underneath the curator).
    const ok = $('#p-ok'), bad = $('#p-bad');
    if (ok && bad && active && active.iri === iri && active.dbkey === db && String(active.id) === String(id)) {
      ok.classList.toggle('on', reviewed[key] === 'ok');
      bad.classList.toggle('on', reviewed[key] === 'bad');
    }
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
    const onFile = (r[dbkey] || []).length > 0;
    const key = target != null ? idKey(iri, dbkey, target) : null;

    const eyebrow = !ent ? 'No id yet'
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

    const idBlock = ent ? `<div class="p-idblock">
      <div class="p-id">${esc(ent.id)}</div>
      <div class="p-label">${esc(ent.pred ? (ent.pred.label || '') : (idLabels[labelKey(dbkey, ent.id)] || ''))}</div>
      <div class="p-idacts">
        <a class="p-open" href="${esc(db.link(ent.id))}" target="_blank" rel="noopener">Open ${esc(db.label)} in a new tab ↗</a>
        <button class="btn" id="p-copy">Copy id</button>
      </div>
      <div class="p-note">${db.noframe
        ? esc(db.label) + ' blocks embedding' + (dbkey === 'umls' ? ' and requires login' : '') + ', so nothing is previewed here.'
        : 'Source page embeds below.'}</div>
    </div>` : `<div class="p-idblock">
      <div class="p-label">No ${esc(db.label)} id yet — <a href="${esc(db.search(r.name))}" target="_blank" rel="noopener">search ${esc(db.label)} for "${esc(r.name)}" ↗</a>, then paste the id below.</div>
    </div>`;

    const foot = entries.length > 1
      ? 'Each id publishes as its own SSSOM row; confirming one does not judge the others.'
      : 'Verdicts save to your review session as you work.';

    $('#panel').innerHTML = `
      <div class="p-head">
        <button class="p-close" id="p-close">✕</button>
        <div class="p-eyebrow ${st === 'ok' ? 'ok' : st === 'bad' ? 'bad' : ''}">${esc(eyebrow)}</div>
        <div class="p-title">${esc(r.name)}</div>
        <div class="p-sub">${esc(sub)}</div>
      </div>
      ${sibs}
      ${idBlock}
      ${ent ? `<div class="p-actions">
        <button class="btn ok ${st === 'ok' ? 'on' : ''}" id="p-ok">✓ Confirm</button>
        <button class="btn bad ${st === 'bad' ? 'on' : ''}" id="p-bad">✕ Not the same</button>
        <button class="btn" id="p-next">Next open mapping</button>
      </div>` : ''}
      <div class="p-edit">
        <label>${esc(db.label)} id(s) — comma separated${!onFile && ent ? ' (predicted id pre-filled — verify, then Save)' : ''}</label>
        <input id="p-ids" value="${esc(onFile ? (r[dbkey] || []).join(', ') : (ent ? ent.id : ''))}" placeholder="e.g. 12345, 67890">
        <button class="btn primary" id="p-save">Save</button>
      </div>
      ${ent ? '<div class="p-cmp" id="p-compare"><div class="muted" style="padding:9px 12px">Loading concept…</div></div>' : ''}
      ${db.noframe || !ent
        ? ''
        : `<iframe id="p-frame" src="${esc(db.link(ent.id))}"></iframe>`}
      <div class="p-foot">${esc(foot)}</div>`;

    $('#side').classList.add('open');
    $('#divider').classList.add('show');
    $('#p-close').addEventListener('click', closePanel);
    $('#p-save').addEventListener('click', () => save(iri, dbkey));
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
      // Fill the compare pane after wiring, so the verdict buttons stay live while
      // the concept lookup is in flight.
      fillCompare(dbkey, target, r);
    }
  }

  function closePanel() {
    closeSubtypeOverlay();
    active = null;
    $('#side').classList.remove('open');
    $('#divider').classList.remove('show');
    renderMatrix();
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
      <div class="so-body">
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
    if (!me || !me.authenticated) { alert('Sign in with GitHub first.'); return; }
    const val = id => ($('#' + id)?.value || '').trim();
    const label = val('so-label'), definition = val('so-definition'), defsrc = val('so-defsrc');
    const tissue_iris = [...document.querySelectorAll('#so-tissues input:checked')].map(c => c.value);
    if (!label)             { alert('Label is required'); return; }
    if (!definition)        { alert('Definition is required'); return; }
    if (!defsrc)            { alert('Definition source is required'); return; }
    if (!tissue_iris.length){ alert('Select at least one target tissue'); return; }
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
      alert('Created subtype: ' + created.name);
    } catch (e) {
      alert('Create failed: ' + e.message);
      btn.disabled = false; btn.textContent = '＋ Create subtype';
    }
  }

  async function save(iri, dbkey) {
    if (!me || !me.authenticated) { alert('Sign in with GitHub first.'); return; }
    const val = $('#p-ids').value.trim();
    $('#p-save').disabled = true; $('#p-save').textContent = 'Saving…';
    try {
      const updated = await api('disease/' + encodeURIComponent(iri), { method: 'PUT', body: { changes: { [dbkey]: val } } });
      const r = ROWS.find(x => x.iri === iri);
      const oldIds = r[dbkey] || [];
      const newIds = updated[dbkey] || [];
      r[dbkey] = newIds;
      // Mark all new ids as edited (per-ID). Clear any prior 'bad' review on the
      // same id (e.g. a rejected prediction the curator then decided to keep) so
      // a saved id is never also published as a negative mapping.
      for (const id of newIds) {
        edited[idKey(iri, dbkey, id)] = true;
        delete reviewed[idKey(iri, dbkey, id)];
      }
      // Clear edited for ids that were removed
      for (const id of oldIds) {
        if (!newIds.includes(id)) {
          delete edited[idKey(iri, dbkey, id)];
          delete reviewed[idKey(iri, dbkey, id)];
        }
      }
      counts(); saveSession();
      openPanel(iri, dbkey, newIds.length ? newIds[0] : null);
      renderMatrix();
    } catch (e) { alert('Save failed: ' + e.message); $('#p-save').disabled = false; $('#p-save').textContent = 'Save'; }
  }

  // Publish the review as a pull request. `newPr` true opens a fresh PR even when
  // one is already tracked; otherwise changes are committed to the existing PR (or
  // a first PR is opened when none exists yet).
  async function publish(newPr) {
    const reuse = newPr ? null : sessionBranch;
    if (newPr && sessionPr &&
        !confirm('Open a new pull request instead of adding to PR #' + sessionPr.number + '?')) return;
    const comment = window.prompt('Optional comment for the pull request (what you reviewed/changed):', 'Mappings review');
    if (comment === null) return;
    const orcid = (localStorage.getItem('ari_editor_orcid') || '').trim();
    const author = orcid ? ('orcid:' + orcid) : (me && me.login ? ('github:' + me.login) : 'curator');
    const message = reviewMessage();
    $('#publish').disabled = true; $('#publish-new').disabled = true; $('#publish').textContent = 'Publishing…';
    try {
      const r = await api('publish', { method: 'POST', body: {
        disease: 'mappings review', message, comment,
        confirmed: confirmedList(), flagged: flaggedList(), author,
        branch: reuse, labels: ['edit term', 'sssom'] } });
      sessionBranch = r.branch;                       // subsequent publishes append to the same PR
      sessionPr = { number: r.pr_number, url: r.pr_url, fork: r.fork };
      reflectPr();
      saveSession(true);                              // persist the PR pointer immediately
      $('#publish').disabled = true; $('#publish-new').disabled = true;  // re-enabled by counts() on new changes
    } catch (e) { alert('Publish failed: ' + e.message); reflectPr(); counts(); }
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

  // ------------------------------------------------------- HEADER CONTROLS
  function syncSegs() {
    const th = document.documentElement.dataset.theme || 'light';
    const de = document.documentElement.dataset.density || 'comfortable';
    document.querySelectorAll('#theme button').forEach(b => b.classList.toggle('on', b.dataset.theme === th));
    document.querySelectorAll('#density button').forEach(b => b.classList.toggle('on', b.dataset.density === de));
  }

  function initControls() {
    $('#theme').addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      document.documentElement.dataset.theme = b.dataset.theme;
      try { localStorage.setItem('theme', b.dataset.theme); } catch (err) {}
      syncSegs();
    });
    $('#density').addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      document.documentElement.dataset.density = b.dataset.density;
      try { localStorage.setItem('refDensity', b.dataset.density); } catch (err) {}
      applyGrid(); syncSegs();
    });
    $('#pending-chip').addEventListener('click', () => $('#pending-panel').classList.toggle('open'));
    // One delegated handler for the whole matrix — 200+ rows x 10 cells is far too
    // many nodes to bind individually, and the matrix re-renders on every verdict.
    $('#matrix').addEventListener('click', e => {
      if (e.target.closest('.copen')) return;                 // let the link open normally
      const btn = e.target.closest('.cbtn');
      if (btn) { setReview(btn.dataset.iri, btn.dataset.db, btn.dataset.id, btn.dataset.v); return; }
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
        predicted[p.ari_id + '|' + p.prefix + '|' + p.id] = { label: p.object_label, match_field: p.match_field, confidence: p.confidence };
    } catch (e) { predicted = {}; }
    // Restore this user's saved review session (verdicts + PR pointer) so a reload
    // resumes their work. Anonymous users get {} — review state stays in-memory.
    if (me && me.authenticated) {
      try {
        const s = await api('ref-session');
        reviewed = s.reviewed || {};
        edited = s.edited || {};
        sessionBranch = s.branch || null;
        sessionPr = s.pr || null;
      } catch (e) { console.warn('Could not load review session:', e.message); }
    }
    reflectPr();
    applyGrid(); syncSegs(); renderHead(); renderMatrix(); counts();
    initDivider(); initControls();
    $('#filter').addEventListener('input', renderMatrix);
    $('#publish').addEventListener('click', () => publish(false));
    $('#publish-new').addEventListener('click', () => publish(true));
  }
  document.addEventListener('DOMContentLoaded', init);
})();
