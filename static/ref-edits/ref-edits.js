// Reference-review page (/ref-edits) — queue-driven mapping confirmation.
//
// Replaces the 224 x 10 matrix with the workflow curators actually run: your own
// assigned queue on the left, one disease at a time in the centre with every review
// database stacked as a single-decision row, and one reference under review on the
// right. Every decision autosaves through POST /api/v2/decisions the moment it is
// made; publishing stays a separate, explicit step.
//
// Backend contract (app/assignment_service.py + app/main.py):
//   GET    /api/v2/queue                  my diseases + progress + coverage strips
//   GET    /api/v2/queue/{iri}            one disease: entry per review database
//   GET    /api/v2/assignments            all curators' assignment records
//   POST   /api/v2/assignments            {login, iris, note?, replace?}
//   POST   /api/v2/assignments/done       {iri, done?}
//   GET    /api/v2/decisions              my unpublished decisions + summary
//   POST   /api/v2/decisions              {iri, db, id, verdict, ...}  (autosave)
//   DELETE /api/v2/decisions/{id}         undo
//   GET    /api/v2/review-summary         pre-publish summary + publish payload
// Verdicts: confirm | reject | no_value | skip.
// Database status: decided | confirmed | flagged | predicted | unreviewed | missing.
(function () {
  'use strict';

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = id => String(id).replace(/^[A-Za-z]+:/, '');
  const enc = encodeURIComponent;
  const $ = s => document.querySelector(s);
  const apiUrl = p => new URL('../api/v2/' + p, location.href).href;

  async function api(p, opts = {}) {
    if (opts.body) {
      opts.headers = { 'content-type': 'application/json' };
      opts.body = JSON.stringify(opts.body);
    }
    const r = await fetch(apiUrl(p), opts);
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || r.statusText);
    }
    return r.json();
  }

  // ------------------------------------------------------------------- state
  let me = null;
  let DBS = [], DBMAP = {};          // xref registry (review databases only)
  let queue = null;                  // GET /api/v2/queue
  let panel = null;                  // GET /api/v2/queue/{iri}
  let activeIri = null;
  let active = null;                 // {db, id} currently in the review pane
  let defs = {};                     // iri -> definition (lazy)
  let concepts = {};                 // "db|id" -> GET /api/v2/concept result (null while loading, false on error)
  let sessionBranch = null;
  let lastDecision = null;           // for the toast Undo
  let savedAt = null;
  let conceptOpen = false;           // target-detail disclosure: collapsed until asked for
  let filter = '', statusFilter = 'todo', dbFilter = '', sortBy = 'work';

  const TODO = ['unreviewed', 'predicted', 'missing', 'partial'];
  const STATUS_LABEL = {
    decided: 'Decided', partial: 'Partly decided', confirmed: 'Previously confirmed',
    flagged: 'Previously flagged', predicted: 'Predicted', unreviewed: 'Unreviewed',
    missing: 'No id',
  };
  const CHIPS = [
    { key: 'todo', label: 'Needs decision' },
    { key: 'predicted', label: 'Predicted' },
    { key: 'missing', label: 'Missing ids' },
    { key: 'flagged', label: 'Prev. flagged' },
    { key: 'all', label: 'All' },
  ];

  const icon = {
    check: '<svg viewBox="0 0 20 20" width="12" height="12" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>',
    close: '<svg viewBox="0 0 20 20" width="11" height="11" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>',
    external: '<svg viewBox="0 0 20 20" width="12" height="12" fill="currentColor" aria-hidden="true"><path d="M11 3a1 1 0 100 2h2.586l-6.293 6.293a1 1 0 101.414 1.414L15 6.414V9a1 1 0 102 0V4a1 1 0 00-1-1h-5z"/><path d="M5 5a2 2 0 00-2 2v8a2 2 0 002 2h8a2 2 0 002-2v-3a1 1 0 10-2 0v3H5V7h3a1 1 0 000-2H5z"/></svg>',
    upload: '<svg viewBox="0 0 20 20" width="14" height="14" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM6.293 6.707a1 1 0 010-1.414l3-3a1 1 0 011.414 0l3 3a1 1 0 01-1.414 1.414L11 5.414V13a1 1 0 11-2 0V5.414L7.707 6.707a1 1 0 01-1.414 0z" clip-rule="evenodd"/></svg>',
  };

  // ------------------------------------------------------------- db registry
  function buildDatabases(list) {
    const fillId = (t, id) => t.replace('{num}', num(id)).replace('{id}', enc(id));
    const fillName = (t, n) => t.replace('{name}', enc(n));
    DBS = (list || []).filter(d => d.review).map(d => ({
      key: d.key, label: d.label, prefix: d.prefix, noframe: d.noframe,
      link: id => (d.link ? fillId(d.link, id) : null),
      search: n => (d.search ? fillName(d.search, n) : null),
    }));
    DBMAP = Object.fromEntries(DBS.map(d => [d.key, d]));
  }

  const linkFor = (dbkey, id) => (DBMAP[dbkey] ? DBMAP[dbkey].link(id) : null);
  const searchFor = (dbkey, name) => (DBMAP[dbkey] ? DBMAP[dbkey].search(name) : null);

  // Candidates in a database still waiting for a verdict. A database offers several
  // ids more often than not and only one of them is the disease, so these drive both
  // what the row asks next and when the row is finished (server: status 'decided').
  const undecided = entry => (entry.candidates || []).filter(c => !c.decision);
  const decidedIn = entry => (entry.candidates || []).filter(c => c.decision);

  // A database entry's outcome, once every candidate has been judged — null while
  // any is still open, so a half-reviewed row never renders as settled.
  function verdictOf(entry) {
    if (entry.no_value_decision) return entry.no_value_decision.verdict;
    const cands = entry.candidates || [];
    if (!cands.length || undecided(entry).length) return null;
    if (cands.some(c => c.decision.verdict === 'confirm')) return 'confirm';
    if (cands.every(c => c.decision.verdict === 'skip')) return 'skip';
    return 'reject';
  }
  // The decision backing that outcome: the confirmed candidate when there is one,
  // otherwise the most recent, so "Undo" on a settled row lands somewhere sensible.
  function decisionOf(entry) {
    if (entry.no_value_decision) return entry.no_value_decision;
    const decided = decidedIn(entry);
    if (!decided.length) return null;
    const hit = decided.find(c => c.decision.verdict === 'confirm');
    return (hit || decided[decided.length - 1]).decision;
  }
  function matchesStatus(entry) {
    if (statusFilter === 'all') return true;
    if (dbFilter && entry.key !== dbFilter) return false;
    if (statusFilter === 'todo') return TODO.includes(entry.status);
    return entry.status === statusFilter;
  }
  const visibleDbs = () => (panel ? panel.databases.filter(e =>
    (!dbFilter || e.key === dbFilter) && matchesStatus(e)) : []);

  // ------------------------------------------------------------------ header
  function renderHeader() {
    $('#auth').innerHTML = !me.authenticated
      ? (me.github_enabled
        ? `<a class="btn" href="${new URL('../auth/github?next=' + enc(location.pathname + location.search), location.href).href}">Sign in with GitHub</a>`
        : '<span class="muted" style="font-size:11.5px">GitHub off — review only</span>')
      : '';
    if (me.authenticated) {
      const initials = (me.login || '').slice(0, 2).toUpperCase();
      $('#assignment-avatar').textContent = initials;
      $('#assignment-btn').title = `Signed in as @${me.login} — change your queue`;
    }
    $('#status-chips').innerHTML = CHIPS.map(c =>
      `<span class="chip${statusFilter === c.key ? ' on' : ''}" data-chip="${c.key}">${esc(c.label)}<span class="n" data-chip-n="${c.key}"></span></span>`).join('');
    $('#status-chips').querySelectorAll('[data-chip]').forEach(el =>
      el.addEventListener('click', () => { statusFilter = el.dataset.chip; renderHeader(); renderPanel(); }));
    $('#db-filter').innerHTML = '<option value="">All databases</option>' +
      DBS.map(d => `<option value="${d.key}"${dbFilter === d.key ? ' selected' : ''}>${esc(d.label)}</option>`).join('');
    renderProgress();
  }

  function renderProgress() {
    if (!queue) return;
    const c = queue.counts;
    const total = c.references || 1;
    const conf = countVerdict('confirm'), flag = countVerdict('reject') + countVerdict('no_value');
    const prior = Math.max(0, c.reviewed - conf - flag);
    const pct = n => (n / total * 100).toFixed(2) + '%';
    const bar = $('#progress-bar').children;
    bar[0].style.width = pct(prior + conf);
    bar[1].style.width = pct(flag);
    bar[2].style.width = '0';
    $('#progress-text').innerHTML =
      `<b>${c.reviewed}</b>/${c.references} · <span class="left">${c.remaining} left</span>`;
    $('#assignment-count').textContent = `${c.diseases} disease${c.diseases === 1 ? '' : 's'}`;
    $('#q-progress').textContent = `${c.diseases_done} / ${c.diseases} done`;
    const chipCounts = { todo: 0, predicted: 0, missing: 0, flagged: 0, all: c.references };
    if (panel) {
      for (const e of panel.databases) {
        if (TODO.includes(e.status)) chipCounts.todo++;
        if (e.status === 'predicted') chipCounts.predicted++;
        if (e.status === 'missing') chipCounts.missing++;
        if (e.status === 'flagged') chipCounts.flagged++;
      }
      chipCounts.all = panel.databases.length;
    }
    for (const [k, v] of Object.entries(chipCounts)) {
      const el = $(`[data-chip-n="${k}"]`);
      if (el) el.textContent = v || '';
    }
    $('#publish').disabled = !(me.authenticated && queue.unpublished > 0);
    $('#publish').textContent = queue.unpublished
      ? (sessionBranch ? `Publish ${queue.unpublished} more` : `Publish ${queue.unpublished} decision${queue.unpublished === 1 ? '' : 's'}`)
      : 'Publish review';
  }

  // Count decisions of a verdict inside the disease currently open (best-effort
  // colour for the header bar; the authoritative totals come from the queue).
  function countVerdict(v) {
    if (!panel) return 0;
    return panel.databases.filter(e => verdictOf(e) === v).length;
  }

  // ------------------------------------------------------------------- queue
  function renderQueue() {
    const q = (filter || '').trim().toLowerCase();
    let items = (queue.diseases || []).filter(d => !q ||
      (d.name || '').toLowerCase().includes(q) || (d.ari_id || '').toLowerCase().includes(q));
    if (sortBy === 'name') items = items.slice().sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    if (sortBy === 'ari') items = items.slice().sort((a, b) => (a.ari_id || '').localeCompare(b.ari_id || ''));

    if (!items.length) {
      $('#q-list').innerHTML = queue.diseases.length
        ? '<p class="muted" style="padding:14px">Nothing matches that filter.</p>'
        : `<div style="padding:16px;display:flex;flex-direction:column;gap:9px">
             <p class="muted" style="margin:0">No diseases are assigned to you yet.</p>
             <button class="btn sm" id="q-empty-assign">Add diseases to my queue</button></div>`;
      const b = $('#q-empty-assign');
      if (b) b.addEventListener('click', openAssign);
      return;
    }
    $('#q-list').innerHTML = items.map(d => `
      <div class="q-row${d.iri === activeIri ? ' active' : ''}${d.done ? ' done' : ''}" data-iri="${esc(d.iri)}">
        <div class="q-top">
          ${d.done ? `<span class="q-check">${icon.check}</span>` : ''}
          <span class="q-name">${esc(d.name)}</span>
          <span class="q-ari">${esc(d.ari_id || '')}</span>
        </div>
        <div class="q-cov"${d.done ? ' style="padding-left:18px"' : ''}>
          ${(d.coverage || []).map(s => `<i class="s-${esc(s)}" title="${esc(STATUS_LABEL[s] || s)}"></i>`).join('')}
          <span class="q-left ${d.done ? 'done' : d.remaining > 5 ? 'hot' : ''}">${d.done ? 'done' : d.remaining + ' left'}</span>
        </div>
      </div>`).join('');
    $('#q-list').querySelectorAll('.q-row').forEach(el =>
      el.addEventListener('click', () => openDisease(el.dataset.iri)));
  }

  // ----------------------------------------------------------- disease panel
  async function openDisease(iri) {
    activeIri = iri;
    active = null;
    renderQueue();
    $('#panel').innerHTML = '<div class="r-empty">Loading disease…</div>';
    try {
      panel = await api('queue/' + enc(iri));
    } catch (e) {
      $('#panel').innerHTML = `<div class="r-empty">Could not load this disease: ${esc(e.message)}</div>`;
      return;
    }
    if (defs[iri] === undefined) {
      defs[iri] = null;
      api('disease/' + enc(iri))
        .then(d => { defs[iri] = (d && (d.definition || (d.fields && d.fields.definition))) || ''; if (activeIri === iri) renderPanel(); })
        .catch(() => { defs[iri] = ''; });
    }
    renderPanel();
    renderProgress();
    // Land on the first id that still needs a decision.
    const first = visibleDbs().find(e => TODO.includes(e.status)) || visibleDbs()[0];
    if (first) openRef(first.key, (undecided(first)[0] || first.candidates[0] || {}).id || null);
    else renderReview();
  }

  function renderPanel() {
    if (!panel) return;
    const def = defs[panel.iri];
    const rows = visibleDbs();
    $('#panel').innerHTML = `
      <div class="d-head">
        <div class="d-title">
          <span class="d-accent"></span>
          <h2>${esc(panel.name)}</h2>
          <span class="code">${esc(panel.ari_id || '')}</span>
          ${panel.assigned_to && panel.assigned_to !== (me.login || '')
            ? `<span class="tag grey">assigned to @${esc(panel.assigned_to)}</span>` : ''}
          <div class="spacer"></div>
          <span class="muted" style="font-size:11.5px">${panel.remaining} of ${panel.total} still to decide</span>
          <div class="menu-wrap">
            <button class="btn icon" id="d-menu" title="More actions" aria-haspopup="true" aria-expanded="false">⋯</button>
            <div class="menu" id="d-menu-list" role="menu">
              <button type="button" role="menuitem" data-action="curate">${icon.external} Open in the disease curator</button>
              <button type="button" role="menuitem" data-action="main-app">${icon.external} Open in the main app</button>
              <button type="button" role="menuitem" data-action="subtype">＋ New subtype of this disease</button>
              <hr>
              <button type="button" role="menuitem" class="danger" data-action="unassign">Remove from my queue</button>
            </div>
          </div>
        </div>
        ${(panel.synonyms || []).length ? `<div class="syns"><span class="eyebrow" style="margin-right:3px">Synonyms</span>
          ${panel.synonyms.map(s => `<span class="syn">${esc(s)}</span>`).join('')}</div>` : ''}
        ${def ? `<p class="d-def">${esc(def)}</p>` : ''}
      </div>
      <div id="db-list">${rows.length ? rows.map(dbRow).join('')
        : `<div class="r-empty">Nothing in this disease matches the current filter.
             <button class="link" id="clear-f" style="margin-left:6px">Show all</button></div>`}</div>
      <div class="d-foot">
        <span class="muted" style="font-size:12px">Disease ${diseaseIndex() + 1} of ${queue.diseases.length}</span>
        <div class="spacer"></div>
        <button class="btn" id="d-prev">← Prev <span class="hk">k</span></button>
        <button class="btn" id="d-next">Next <span class="hk">j</span> →</button>
        <button class="btn dark" id="d-done">${isDone() ? 'Reopen disease' : 'Mark disease done'} <span class="hk">⏎</span></button>
      </div>`;
    wirePanel();
  }

  function dbRow(e) {
    const v = verdictOf(e);
    const dec = decisionOf(e);
    const cls = ['db', 's-' + e.status];
    if (v) cls.push(v === 'confirm' ? 's-decided-ok' : v === 'skip' ? '' : 's-decided-bad');
    if (active && active.db === e.key) cls.push('current');
    const name = panel.name;

    let body = '';
    if (e.candidates.length) {
      const VERDICT_TITLE = { confirm: 'Confirmed — this is the disease', reject: 'Rejected',
        skip: 'Skipped', no_value: 'No value' };
      body = e.candidates.map(c => {
        const cc = ['id'];
        if (c.decision) cc.push('v-' + c.decision.verdict);
        else if (c.predicted) cc.push('pred');
        else if (c.prior === 'positive') cc.push('prior-pos');
        else if (c.prior === 'negative') cc.push('prior-neg');
        if (active && active.db === e.key && String(active.id) === String(c.id)) cc.push('on');
        const title = c.decision ? VERDICT_TITLE[c.decision.verdict] || c.decision.verdict
          : 'Not yet judged — click to review';
        return `<span class="${cc.join(' ')}" data-ref="${esc(e.key)}" data-id="${esc(c.id)}" title="${esc(title)}">${esc(c.id)}</span>`;
      }).join('');
      const c0 = e.candidates[0];
      if (c0.label) body += `<span class="obj" title="${esc(c0.label)}">${esc(c0.label)}</span>`;
      if (c0.predicted) {
        body += `<span class="tag warn">predicted · ${esc(c0.match_field || 'match')} · ${esc(c0.confidence || '')}</span>`;
      } else if (c0.prior === 'positive') {
        body += '<span class="tag ok">previously confirmed</span>';
      } else if (c0.prior === 'negative') {
        body += '<span class="tag bad">previously flagged</span>';
      }
      if (e.noframe) body += '<span class="tag grey">no preview — blocks embedding</span>';
    } else {
      const su = searchFor(e.key, name);
      body = `<span class="empty-note">No ${esc(e.label)} id yet</span>
        <input class="idin" data-save="${esc(e.key)}" placeholder="paste id…" aria-label="${esc(e.label)} id">
        ${su ? `<a class="link" href="${esc(su)}" target="_blank" rel="noopener">Search ${esc(e.label)} ${icon.external}</a>` : ''}`;
    }

    let acts;
    const open = undecided(e);
    if (v) {
      const label = { confirm: 'Confirmed', reject: 'Rejected', no_value: 'No value in ' + e.label, skip: 'Skipped' }[v];
      const tone = v === 'confirm' ? 'ok' : v === 'skip' ? 'grey' : 'bad';
      const n = decidedIn(e).length;
      acts = `<span class="tag ${tone}">${esc(label)}</span>
        ${dec && dec.author ? `<span class="dim" style="font-size:11px">@${esc(dec.author)}</span>` : ''}
        <button class="link" data-undo-db="${esc(e.key)}">${n > 1 ? 'Undo all' : 'Undo'}</button>`;
    } else if (open.length) {
      // Confirm/Reject act on the id currently selected (open in the review pane on
      // the right) — never on "whichever is first" — so a click here can never
      // judge a different candidate than the one the curator is looking at. With
      // nothing selected in this database there is nothing to target, so the
      // buttons stay disabled until an id chip is clicked.
      const selected = (active && active.db === e.key)
        ? open.find(c => String(c.id) === String(active.id)) : null;
      const done = decidedIn(e).length;
      const dis = selected ? '' : ' disabled';
      const hint = selected ? '' : ' title="Select an id above to judge it"';
      acts = `${done ? `<span class="dim" style="font-size:11px">${done}/${e.candidates.length}</span>` : ''}
        <button class="btn sm ok" data-verdict="confirm" data-db="${esc(e.key)}" data-id="${selected ? esc(selected.id) : ''}"${dis}${hint}>✓ ${selected && selected.predicted ? 'Accept' : 'Confirm'}</button>
        <button class="btn sm bad" data-verdict="reject" data-db="${esc(e.key)}" data-id="${selected ? esc(selected.id) : ''}"${dis}${hint}>✗ ${selected && selected.predicted ? 'Discard' : 'Reject'}</button>`;
      const lu = selected ? linkFor(e.key, selected.id) : null;
      if (lu) acts += `<a class="link" href="${esc(lu)}" target="_blank" rel="noopener" title="Open ${esc(selected.id)} in ${esc(e.label)}">${icon.external}</a>`;
    } else {
      acts = `<button class="btn sm" data-verdict="no_value" data-db="${esc(e.key)}" data-id="">No value exists</button>`;
    }

    return `<div class="${cls.join(' ')}" data-row="${esc(e.key)}">
      <div class="db-key">${esc(e.label)}</div>
      <div class="db-body">${body}</div>
      <div class="db-acts">${acts}</div></div>`;
  }

  function wirePanel() {
    const p = $('#panel');
    p.querySelectorAll('[data-ref]').forEach(el =>
      el.addEventListener('click', () => openRef(el.dataset.ref, el.dataset.id)));
    p.querySelectorAll('[data-verdict]').forEach(el =>
      el.addEventListener('click', () => decide(el.dataset.db, el.dataset.id, el.dataset.verdict)));
    p.querySelectorAll('[data-undo-db]').forEach(el =>
      el.addEventListener('click', () => undoDatabase(el.dataset.undoDb)));
    p.querySelectorAll('[data-save]').forEach(el => {
      el.addEventListener('keydown', ev => { if (ev.key === 'Enter') saveIds(el.dataset.save, el.value); });
      el.addEventListener('focus', () => openRef(el.dataset.save, null));
    });
    const menuBtn = $('#d-menu');
    if (menuBtn) menuBtn.addEventListener('click', ev => { ev.stopPropagation(); toggleMenu(menuBtn, $('#d-menu-list')); });
    const menuList = $('#d-menu-list');
    if (menuList) menuList.querySelectorAll('[data-action]').forEach(el =>
      el.addEventListener('click', () => { closeMenus(); diseaseMenuAction(el.dataset.action); }));
    $('#d-prev').addEventListener('click', () => stepDisease(-1));
    $('#d-next').addEventListener('click', () => stepDisease(1));
    $('#d-done').addEventListener('click', toggleDone);
    const cf = $('#clear-f');
    if (cf) cf.addEventListener('click', () => { statusFilter = 'all'; dbFilter = ''; renderHeader(); renderPanel(); });
  }

  // Anchored popover shared by every "⋯" trigger on the page (today just the
  // disease-panel one). Only one is open at a time; outside click, Escape and the
  // existing scrim-open guard in onKey() all close it, wired once in wireChrome().
  function toggleMenu(btn, list) {
    const willOpen = !list.classList.contains('open');
    closeMenus();
    if (willOpen) {
      list.classList.add('open');
      btn.setAttribute('aria-expanded', 'true');
    }
  }
  function closeMenus() {
    document.querySelectorAll('.menu.open').forEach(m => m.classList.remove('open'));
    document.querySelectorAll('[aria-haspopup="true"][aria-expanded="true"]')
      .forEach(b => b.setAttribute('aria-expanded', 'false'));
  }

  function diseaseMenuAction(action) {
    if (action === 'curate') return window.open('../ref-curate/#' + panel.iri, '_blank');
    if (action === 'main-app') return window.open('../#' + panel.iri, '_blank');
    if (action === 'subtype') return openSubtype(panel.iri);
    if (action === 'unassign') return unassignCurrent();
  }

  const diseaseIndex = () => queue.diseases.findIndex(d => d.iri === activeIri);
  const isDone = () => { const d = queue.diseases[diseaseIndex()]; return !!(d && d.done); };

  function stepDisease(delta) {
    const i = diseaseIndex();
    const next = queue.diseases[i + delta];
    if (next) openDisease(next.iri);
  }

  async function toggleDone() {
    if (!requireAuth()) return;
    try {
      await api('assignments/done', { method: 'POST', body: { iri: activeIri, done: !isDone() } });
      await refreshQueue();
      const wasDone = isDone();
      toast(wasDone ? 'Disease marked done' : 'Disease reopened');
      if (wasDone) {
        const next = queue.diseases.find(d => !d.done);
        if (next) return openDisease(next.iri);
      }
      renderPanel();
    } catch (e) { toast('Could not update: ' + e.message); }
  }

  async function unassignCurrent() {
    if (!requireAuth()) return;
    try {
      await api('assignments', { method: 'DELETE', body: { login: me.login, iris: [activeIri] } });
      await refreshQueue();
      const next = queue.diseases[0];
      if (next) openDisease(next.iri); else { panel = null; activeIri = null; renderPanel(); }
      toast('Removed from your queue');
    } catch (e) { toast('Could not unassign: ' + e.message); }
  }

  // ------------------------------------------------------------ review pane
  function openRef(dbkey, id) {
    const e = panel.databases.find(x => x.key === dbkey);
    if (!e) return;
    const cand = (e.candidates || []).find(c => String(c.id) === String(id)) || e.candidates[0] || null;
    active = { db: dbkey, id: cand ? cand.id : null };
    renderReview();
    renderPanel();
    const row = $(`[data-row="${CSS.escape(dbkey)}"]`);
    if (row) row.scrollIntoViewIfNeeded ? row.scrollIntoViewIfNeeded() : null;
  }

  // Lazy, cached lookup of what the reference indexes know about one target id.
  // Never blocks the verdict buttons: returns null while in flight and re-renders
  // the pane when the answer lands (only if that reference is still active).
  function loadConcept(dbkey, id) {
    const key = dbkey + '|' + id;
    if (key in concepts) return concepts[key];
    concepts[key] = null;
    api('concept/' + enc(dbkey) + '/' + enc(id))
      .then(c => { concepts[key] = c;
        if (active && active.db === dbkey && String(active.id) === String(id)) renderReview(); })
      .catch(() => { concepts[key] = false; });
    return null;
  }

  function renderReview() {
    if (!panel || !active) {
      $('#review').innerHTML = '<div class="r-empty">Select a reference to review it here.</div>';
      return;
    }
    const e = panel.databases.find(x => x.key === active.db);
    const cand = (e.candidates || []).find(c => String(c.id) === String(active.id)) || null;
    const idx = visibleDbs().findIndex(x => x.key === e.key);
    const url = cand ? linkFor(e.key, cand.id) : searchFor(e.key, panel.name);
    // The verdict shown here belongs to *this id*, not to the database: each
    // candidate is judged on its own, and the row is finished only once they all are.
    const settled = cand ? cand.decision : e.no_value_decision;
    const v = settled ? settled.verdict : null;
    const left = undecided(e).length;
    // "No correct value in <db>" contradicts having already confirmed one of its
    // ids, and publishing would emit the cell as both a positive and a negative,
    // so it is offered only while nothing in the database is confirmed.
    const hasConfirm = (e.candidates || []).some(c => c.decision && c.decision.verdict === 'confirm');

    // Evidence comes from GET /api/v2/concept: label, exact synonyms, definition
    // and parents when the database has its own index (direct), or the hub term(s)
    // that cross-reference the id when it does not — never presented as the target
    // database's own term (concept.note carries that caveat verbatim).
    const concept = cand ? loadConcept(e.key, cand.id) : null;
    const norm = s => (s || '').trim().toLowerCase();
    const label = (concept && concept.found && concept.label) ? concept.label
      : (cand && cand.label ? cand.label : null);
    const exact = label && norm(label) === norm(panel.name);
    const synHit = label && (panel.synonyms || []).some(s => norm(s) === norm(label));
    const tSyns = (concept && concept.direct && concept.synonyms) ? concept.synonyms : [];
    const ariStrings = [panel.name].concat(panel.synonyms || []).map(norm);
    const viaSources = (concept && concept.via) ? [...new Set(concept.via.map(v => v.source))] : [];

    // Only the target's side is shown: the ARI disease is already named, with its
    // synonyms and definition, in the panel this pane sits beside, so repeating it
    // as a second column cost height the source page needs. `hit` still marks the
    // strings that agree with the ARI term, which is what the comparison was for.
    const viaHead = !!(concept && concept.found && !concept.direct);
    const heading = viaHead
      ? 'via ' + esc(viaSources.join(', ')) + (cand ? ' · ' + esc(cand.id) : '')
      : esc(e.label) + (cand ? ' · ' + esc(cand.id) : '');
    const headLabel = label
      ? `<span class="t-label ${exact || synHit ? 'hit' : ''}">${esc(label)}</span>`
      : concept === null && cand ? '<span class="t-label dim">Looking up…</span>'
      : `<span class="t-label dim">${cand ? 'Not in our indexes — open the source' : 'No candidate id'}</span>`;
    const detail = [
      tSyns.length ? `<div><span class="dim" style="font-size:11px;font-weight:600">exact synonyms</span>
        <div class="list">${tSyns.map(s => `<span class="${ariStrings.includes(norm(s)) ? 'hit' : ''}">${esc(s)}</span>`).join(' · ')}</div></div>` : '',
      concept && concept.definition ? `<div class="list" style="font-size:11px">${esc(concept.definition)}</div>` : '',
      concept && concept.parents && concept.parents.length
        ? `<div class="list dim" style="font-size:11px">is_a · ${esc(concept.parents.join(' · '))}</div>` : '',
      cand && cand.predicted
        ? `<div class="list">Matched our <b>${esc(cand.match_field || 'label')}</b> — ${esc(cand.confidence || '')} confidence</div>` : '',
      concept && concept.note ? `<div class="list dim" style="font-size:10.5px">${esc(concept.note)}</div>` : '',
    ].filter(Boolean).join('');

    $('#review').innerHTML = `
      <div class="r-head">
        <span class="eyebrow">Reviewing</span>
        <b style="font-size:13px">${esc(e.label)}</b>
        ${cand ? `<span class="mono" style="font-size:11.5px;color:var(--primary)">${esc(cand.id)}</span>` : ''}
        <div class="spacer"></div>
        <span class="mono dim" style="font-size:11.5px">${idx + 1} / ${visibleDbs().length}</span>
        <button class="btn icon sm" id="r-close" title="Close">${icon.close}</button>
      </div>
      <div style="padding:12px 13px 0;flex-shrink:0">
        ${detail
          ? `<details class="tgt" id="r-detail"${conceptOpen ? ' open' : ''}>
               <summary><span class="eyebrow${viaHead ? ' via' : ''}">${heading}</span>${headLabel}</summary>
               <div class="tgt-body">${detail}</div>
             </details>`
          : `<div class="tgt flat"><span class="eyebrow${viaHead ? ' via' : ''}">${heading}</span>${headLabel}</div>`}
        <div style="display:flex;align-items:center;gap:7px;padding:8px 2px 0">
          ${exact ? '<span class="tag ok">Exact label match</span>'
            : synHit ? '<span class="tag ok">Exact synonym match</span>'
            : cand && cand.predicted ? `<span class="tag warn">${esc(cand.confidence || 'low')} confidence prediction</span>`
            : '<span class="tag grey">No automatic match — judge from the source</span>'}
          ${cand && cand.prior ? `<span class="tag ${cand.prior === 'positive' ? 'ok' : 'bad'}">previously ${cand.prior === 'positive' ? 'confirmed' : 'flagged'}</span>` : ''}
          <div class="spacer"></div>
          <span class="dim" style="font-size:11px">${left
            ? `${left} more id${left === 1 ? '' : 's'} to judge in ${esc(e.label)}`
            : `${panel.total - panel.remaining} of ${panel.total} databases settled`}</span>
        </div>
        <div style="border-bottom:1px solid var(--border);margin:11px 0"></div>
      </div>
      ${settled
        ? `<div class="r-verdict"><div class="tag ${v === 'confirm' ? 'ok' : v === 'skip' ? 'grey' : 'bad'}" style="flex:1;height:38px;display:flex;align-items:center;justify-content:center;font-size:13px">
             ${esc({ confirm: '✓ Confirmed', reject: '✗ Rejected', no_value: '✗ No value in ' + e.label, skip: 'Skipped' }[v])}</div>
           <button class="btn big" id="r-undo">Undo</button></div>`
        : `<div class="r-verdict">
             <button class="btn big ok" id="r-ok"${cand ? '' : ' disabled'}>✓ Confirm match <span class="hk">y</span></button>
             <button class="btn big bad" id="r-bad"${cand ? '' : ' disabled'}>✗ Not a match <span class="hk">n</span></button>
           </div>
           <div class="r-alt">
             ${hasConfirm ? '' : `<button class="link quiet" id="r-none">No correct value in ${esc(e.label)} <span class="hk">x</span></button>`}
             ${cand ? '<button class="link quiet" id="r-skip">Skip <span class="hk">s</span></button>' : ''}
             <div class="spacer"></div>
             ${url ? `<a class="link" href="${esc(url)}" target="_blank" rel="noopener">Open ${esc(e.label)} ${icon.external}</a>` : ''}
           </div>`}
      ${(e.candidates || []).length > 1 ? `<div style="padding:0 13px 10px;display:flex;flex-wrap:wrap;gap:5px">
        <span class="eyebrow" style="align-self:center">Other ids</span>
        ${e.candidates.filter(c => String(c.id) !== String(active.id)).map(c =>
          `<span class="id ${c.decision ? 'v-' + c.decision.verdict : c.predicted ? 'pred' : ''}"
             data-ref2="${esc(e.key)}" data-id2="${esc(c.id)}"
             title="${c.decision ? esc(c.decision.verdict) : 'Not yet judged'}">${esc(c.id)}</span>`).join('')}
        ${left ? `<span class="dim" style="align-self:center;font-size:11px">${left} still to judge</span>` : ''}</div>` : ''}
      ${e.noframe || !url
        ? `<div class="frame"><div class="frame-bar"><span class="dot"></span>
             <span class="url">${esc(e.label)} blocks embedding</span></div>
           <div class="noframe">
             <div class="eyebrow">What we hold for this concept</div>
             ${cand ? `<div class="mono" style="font-size:12px">${esc(cand.id)}</div>` : ''}
             ${label ? `<div style="font-family:var(--font-display);font-size:15px;font-weight:600">${esc(label)}</div>` : ''}
             ${cand && cand.predicted ? `<div class="muted" style="font-size:11.5px">Predicted from an exact ${esc(cand.match_field || 'label')} match against the ${esc(e.label)} index.</div>` : ''}
             <p class="muted" style="font-size:11.5px;line-height:1.55;margin:0">${esc(e.label)} refuses to render inside a frame${e.key === 'umls' ? ' and requires a login' : ''}, so judge it from the record above or open it in a new tab.</p>
             ${url ? `<a class="btn sm" href="${esc(url)}" target="_blank" rel="noopener" style="align-self:flex-start">Open in ${esc(e.label)} ${icon.external}</a>` : ''}
           </div></div>`
        : `<div class="frame"><div class="frame-bar"><span class="dot"></span>
             <span class="url">${esc(url.replace(/^https?:\/\//, ''))}</span><div class="spacer"></div>
             <a class="link" href="${esc(url)}" target="_blank" rel="noopener">${icon.external}</a></div>
           <iframe id="r-frame" src="${esc(url)}" title="${esc(e.label)} source page" referrerpolicy="no-referrer"></iframe></div>`}`;
    wireReview(e, cand);
  }

  function wireReview(e, cand) {
    const r = $('#review');
    const bind = (sel, fn) => { const el = r.querySelector(sel); if (el) el.addEventListener('click', fn); };
    bind('#r-close', () => { active = null; renderReview(); renderPanel(); });
    bind('#r-ok', () => decide(e.key, cand && cand.id, 'confirm'));
    bind('#r-bad', () => decide(e.key, cand && cand.id, 'reject'));
    bind('#r-none', () => decide(e.key, '', 'no_value'));
    bind('#r-skip', () => decide(e.key, cand && cand.id, 'skip'));
    bind('#r-undo', () => { const d = cand ? cand.decision : e.no_value_decision; if (d) undo(d.id); });
    // Remember the disclosure across re-renders — the concept lookup lands after
    // the pane is first drawn, and re-collapsing it under the curator would undo
    // the click they just made.
    const det = r.querySelector('#r-detail');
    if (det) det.addEventListener('toggle', () => { conceptOpen = det.open; });
    r.querySelectorAll('[data-ref2]').forEach(el =>
      el.addEventListener('click', () => openRef(el.dataset.ref2, el.dataset.id2)));
  }

  // --------------------------------------------------------------- decisions
  function requireAuth() {
    if (me && me.authenticated) return true;
    toast('Sign in with GitHub to record decisions');
    return false;
  }

  async function decide(dbkey, id, verdict) {
    if (!requireAuth()) return;
    const e = panel.databases.find(x => x.key === dbkey);
    const cand = (e.candidates || []).find(c => String(c.id) === String(id));
    try {
      const d = await api('decisions', { method: 'POST', body: {
        iri: panel.iri, db: dbkey, id: id || '', verdict,
        name: panel.name, ari_id: panel.ari_id,
        label: cand ? cand.label : null, predicted: !!(cand && cand.predicted),
      } });
      lastDecision = d;
      savedAt = Date.now();
      await reloadPanel();
      await refreshQueue();
      // The server downgrades a previously confirmed sibling — say so, because it
      // changed a decision the curator is not looking at.
      const swapped = (d.superseded || []).length
        ? ` — ${e.label} no longer confirms ${d.superseded.join(', ')}` : '';
      toast(({ confirm: 'Confirmed ' + (id || ''), reject: 'Rejected ' + (id || ''),
        no_value: 'Recorded: no value in ' + e.label,
        skip: 'Skipped ' + (id || e.label) }[verdict]) + swapped, true);
      advance();
    } catch (err) { toast('Could not save: ' + err.message); }
  }

  async function undo(decisionId) {
    if (!decisionId) return;
    try {
      await api('decisions/' + enc(decisionId), { method: 'DELETE' });
      savedAt = Date.now();
      await reloadPanel();
      await refreshQueue();
      toast('Decision undone');
    } catch (e) { toast('Could not undo: ' + e.message); }
  }

  // Reopen a whole database: a settled row can hold several verdicts (one confirm,
  // the rest rejected), and undoing only one would leave it half-judged with no
  // obvious way back, so the row-level Undo clears them all.
  async function undoDatabase(dbkey) {
    const e = panel.databases.find(x => x.key === dbkey);
    if (!e) return;
    const ids = decidedIn(e).map(c => c.decision.id);
    if (e.no_value_decision) ids.push(e.no_value_decision.id);
    if (!ids.length) return;
    try {
      for (const id of ids) await api('decisions/' + enc(id), { method: 'DELETE' });
      savedAt = Date.now();
      await reloadPanel();
      await refreshQueue();
      toast(ids.length > 1 ? `${ids.length} ${e.label} decisions undone` : 'Decision undone');
    } catch (err) { toast('Could not undo: ' + err.message); }
  }

  // Move to the next id that still needs a decision — the rest of the database in
  // hand before the next database, so a row with five candidates is worked through
  // rather than abandoned after the first. When the disease is finished, say so
  // rather than jumping away under the curator's cursor.
  function advance() {
    const cur = active && panel.databases.find(x => x.key === active.db);
    if (cur) {
      const more = undecided(cur)[0];
      if (more) return openRef(cur.key, more.id);
    }
    const next = visibleDbs().find(x => TODO.includes(x.status));
    if (next) return openRef(next.key, (undecided(next)[0] || next.candidates[0] || {}).id || null);
    active = null;
    renderReview();
    $('#review').innerHTML = `<div class="r-empty">
      <div><b style="color:var(--ok);font-size:14px">All ${panel.total} databases settled</b><br><br>
      <button class="btn dark" id="r-finish">Mark disease done <span class="hk">⏎</span></button></div></div>`;
    const b = $('#r-finish');
    if (b) b.addEventListener('click', toggleDone);
  }

  async function saveIds(dbkey, value) {
    if (!requireAuth()) return;
    const v = (value || '').trim();
    if (!v) return;
    try {
      await api('disease/' + enc(panel.iri), { method: 'PUT', body: { changes: { [dbkey]: v } } });
      await reloadPanel();
      await refreshQueue();
      toast('Saved ' + DBMAP[dbkey].label + ' id');
      const e = panel.databases.find(x => x.key === dbkey);
      if (e && e.candidates.length) openRef(dbkey, e.candidates[0].id);
    } catch (e) { toast('Save failed: ' + e.message); }
  }

  async function reloadPanel() {
    panel = await api('queue/' + enc(panel.iri));
    renderPanel();
    renderReview();
    renderProgress();
  }

  async function refreshQueue() {
    queue = await api('queue');
    renderQueue();
    renderProgress();
    tickSaved();
  }

  // ---------------------------------------------------------------- toast
  let toastTimer = null;
  function toast(msg, undoable) {
    const t = $('#toast');
    t.querySelector('span').textContent = msg;
    const b = t.querySelector('button');
    b.style.display = undoable && lastDecision ? '' : 'none';
    b.onclick = () => { t.classList.remove('show'); if (lastDecision) undo(lastDecision.id); };
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 3600);
  }

  function tickSaved() {
    if (!savedAt) { $('#saved').textContent = ''; return; }
    const s = Math.round((Date.now() - savedAt) / 1000);
    $('#saved').textContent = s < 3 ? 'saved' : s < 60 ? `saved ${s}s ago` : `saved ${Math.round(s / 60)}m ago`;
  }

  // ------------------------------------------------------- assignment modal
  async function openAssign() {
    if (!requireAuth()) return;
    const scrim = $('#assign-scrim');
    scrim.innerHTML = `<div class="modal wide">
      <div class="m-head"><h3>My assignment</h3><div class="spacer"></div>
        <button class="btn icon" data-close>${icon.close}</button></div>
      <div class="m-body">
        <p class="muted" style="margin:0">Diseases in your queue. Everyone works their own set, so two
          curators never review the same reference twice.</p>
        <div class="field"><label for="as-login">Curator</label>
          <select id="as-login"><option value="${esc(me.login)}">@${esc(me.login)} (me)</option></select></div>
        <div class="field"><label for="as-search">Add diseases</label>
          <input id="as-search" placeholder="Type to filter the catalogue…" autocomplete="off"></div>
        <div class="pickgrid" id="as-pick"><span class="muted">Loading the catalogue…</span></div>
        <div class="field"><label for="as-note">Note (optional)</label>
          <input id="as-note" placeholder="e.g. first pass, endocrine block" value="${esc(queue.note || '')}"></div>
      </div>
      <div class="m-foot"><button class="btn primary" id="as-save">Save assignment</button>
        <button class="btn" data-close>Cancel</button><div class="spacer"></div>
        <span class="muted" id="as-count"></span></div></div>`;
    scrim.classList.add('open');
    scrim.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeModals));

    let all = [];
    try { all = await api('diseases'); }
    catch (e) { $('#as-pick').innerHTML = `<span class="muted">Could not load the catalogue: ${esc(e.message)}</span>`; return; }
    // Other curators' claims are shown but not selectable — assignments are exclusive.
    let owners = {};
    try {
      const recs = await api('assignments');
      for (const [login, rec] of Object.entries(recs))
        for (const iri of (rec.iris || [])) owners[iri] = login;
    } catch (e) { owners = {}; }

    const mine = new Set(queue.diseases.map(d => d.iri));
    const draw = () => {
      const q = ($('#as-search').value || '').trim().toLowerCase();
      const list = all.filter(d => !q || (d.name || '').toLowerCase().includes(q)).slice(0, 300);
      $('#as-pick').innerHTML = list.map(d => {
        const other = owners[d.iri] && owners[d.iri] !== me.login;
        return `<label class="pick" title="${other ? 'Assigned to @' + esc(owners[d.iri]) : ''}"
          style="${other ? 'opacity:.45' : ''}">
          <input type="checkbox" value="${esc(d.iri)}"${mine.has(d.iri) ? ' checked' : ''}${other ? ' disabled' : ''}>
          ${esc(d.name)}${other ? ` <span class="dim">@${esc(owners[d.iri])}</span>` : ''}</label>`;
      }).join('') || '<span class="muted">Nothing matches.</span>';
      $('#as-pick').querySelectorAll('input').forEach(cb => cb.addEventListener('change', () => {
        cb.checked ? mine.add(cb.value) : mine.delete(cb.value);
        $('#as-count').textContent = `${mine.size} disease${mine.size === 1 ? '' : 's'} selected`;
      }));
      $('#as-count').textContent = `${mine.size} disease${mine.size === 1 ? '' : 's'} selected`;
    };
    $('#as-search').addEventListener('input', draw);
    draw();

    $('#as-save').addEventListener('click', async () => {
      const btn = $('#as-save');
      btn.disabled = true; btn.textContent = 'Saving…';
      try {
        await api('assignments', { method: 'POST', body: {
          login: me.login, iris: [...mine], note: $('#as-note').value, replace: true } });
        closeModals();
        await refreshQueue();
        if (!activeIri && queue.diseases.length) openDisease(queue.diseases[0].iri);
        toast('Assignment updated');
      } catch (e) {
        toast('Could not save: ' + e.message);
        btn.disabled = false; btn.textContent = 'Save assignment';
      }
    });
  }

  // ---------------------------------------------------------- summary modal
  async function openSummary() {
    if (!requireAuth()) return;
    const scrim = $('#summary-scrim');
    scrim.innerHTML = '<div class="modal"><div class="m-body"><p class="muted">Loading summary…</p></div></div>';
    scrim.classList.add('open');
    let s;
    try { s = await api('review-summary'); }
    catch (e) {
      scrim.innerHTML = `<div class="modal"><div class="m-body"><p class="muted">Could not load: ${esc(e.message)}</p></div>
        <div class="m-foot"><button class="btn" data-close>Close</button></div></div>`;
      scrim.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeModals));
      return;
    }
    const v = s.summary.by_verdict || {};
    const byDb = Object.entries(s.summary.by_database || {})
      .sort((a, b) => b[1] - a[1]);
    scrim.innerHTML = `<div class="modal wide">
      <div class="m-head"><h3>Session summary</h3><div class="spacer"></div>
        <button class="btn icon" data-close>${icon.close}</button></div>
      <div class="m-body">
        <div class="sum-grid">
          <div class="sum-card"><b style="color:var(--ok)">${v.confirm || 0}</b><span>Confirmed</span></div>
          <div class="sum-card"><b style="color:var(--bad)">${(v.reject || 0) + (v.no_value || 0)}</b><span>Rejected / no value</span></div>
          <div class="sum-card"><b class="dim">${v.skip || 0}</b><span>Skipped</span></div>
          <div class="sum-card"><b>${s.summary.diseases_touched}</b><span>Diseases touched</span></div>
        </div>
        <p class="muted" style="margin:0">${s.summary.predicted_accepted} of the confirmations came from
          automatic predictions you verified. Skipped references are never published.</p>
        ${byDb.length ? `<div><div class="eyebrow" style="margin-bottom:6px">By database</div>
          <div class="sum-list">${byDb.map(([k, n]) =>
            `<div class="sum-row"><span>${esc((DBMAP[k] || {}).label || k)}</span>
             <span class="mono muted">${n}</span><span></span></div>`).join('')}</div></div>` : ''}
        ${s.confirmed.length ? `<div><div class="eyebrow" style="margin-bottom:6px">Will publish as exactMatch (${s.confirmed.length})</div>
          <div class="sum-list">${s.confirmed.slice(0, 12).map(c =>
            `<div class="sum-row"><span>${esc(c.name || c.iri)}</span>
             <span class="tag grey">${esc((DBMAP[c.db] || {}).label || c.db)}</span>
             <span class="mono muted">${esc(c.ids.join(', '))}</span></div>`).join('')}
          ${s.confirmed.length > 12 ? `<div class="sum-row muted">+ ${s.confirmed.length - 12} more</div>` : ''}</div></div>` : ''}
        ${s.flagged.length ? `<div><div class="eyebrow" style="margin-bottom:6px">Will publish as "not" mappings (${s.flagged.length})</div>
          <div class="sum-list">${s.flagged.slice(0, 8).map(c =>
            `<div class="sum-row"><span>${esc(c.name || c.iri)}</span>
             <span class="tag grey">${esc((DBMAP[c.db] || {}).label || c.db)}</span>
             <span class="mono muted">${esc(c.ids.join(', ') || '— no value')}</span></div>`).join('')}</div></div>` : ''}
      </div>
      <div class="m-foot">
        <button class="btn primary" id="sum-publish"${s.summary.total ? '' : ' disabled'}>${icon.upload} Publish these decisions</button>
        <button class="btn" data-close>Keep reviewing</button></div></div>`;
    scrim.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeModals));
    $('#sum-publish').addEventListener('click', () => { closeModals(); publish(); });
  }

  // ---------------------------------------------------------- subtype modal
  let _tissues = null;
  async function openSubtype(parentIri) {
    if (!requireAuth()) return;
    const parent = parentIri ? (queue.diseases.find(d => d.iri === parentIri) || panel) : panel;
    if (!parent) { toast('Open a disease first'); return; }
    const scrim = $('#subtype-scrim');
    scrim.innerHTML = `<div class="modal">
      <div class="m-head"><h3>New subtype</h3><div class="spacer"></div>
        <button class="btn icon" data-close>${icon.close}</button></div>
      <div class="m-body">
        <p class="muted" style="margin:0">Created as a child of <b>${esc(parent.name)}</b>.</p>
        <div class="field" id="so-existing-wrap" style="display:none"><label>Start from an existing clinical subtype</label>
          <select id="so-existing"><option value="">— blank —</option></select></div>
        <div class="field"><label>Label <span class="req">*</span></label>
          <input id="so-label" placeholder="e.g. Juvenile-onset ${esc(parent.name)}"></div>
        <div class="field"><label>Definition <span class="req">*</span></label>
          <textarea id="so-definition" placeholder="A subtype of ${esc(parent.name)} characterized by…"></textarea></div>
        <div class="field"><label>Definition source <span class="req">*</span></label>
          <input id="so-defsrc" placeholder="URL or PMID: 12345678"></div>
        <div class="field"><label>Target tissue <span class="req">*</span></label>
          <div class="pickgrid" id="so-tissues"><span class="muted">Loading…</span></div></div>
        <div class="field"><label>Synonyms (comma separated)</label><input id="so-synonyms"></div>
        <div class="field"><label>Disease category</label><input id="so-category"></div>
        <div class="field"><label>Editor name</label><input id="so-editor" value="${esc(me.login || '')}"></div>
      </div>
      <div class="m-foot"><button class="btn primary" id="so-save">Create subtype</button>
        <button class="btn" data-close>Cancel</button></div></div>`;
    scrim.classList.add('open');
    scrim.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeModals));

    api('disease/' + enc(parent.iri)).then(det => {
      const subs = (det && det.clinical_subtypes) || [];
      if (!subs.length) return;
      const sel = $('#so-existing');
      sel.innerHTML = '<option value="">— blank —</option>' +
        subs.map((s, i) => `<option value="${i}">${esc(String(s).split(' - ')[0])}</option>`).join('');
      sel.addEventListener('change', () => {
        if (sel.value === '') return;
        const raw = String(subs[sel.value]), dash = raw.indexOf(' - ');
        $('#so-label').value = dash >= 0 ? raw.slice(0, dash) : raw;
        if (dash >= 0) $('#so-definition').value = raw.slice(dash + 3);
      });
      $('#so-existing-wrap').style.display = '';
    }).catch(() => {});

    try {
      if (!_tissues) _tissues = await api('tissues');
      $('#so-tissues').innerHTML = _tissues.length
        ? _tissues.map(t => `<label class="pick"><input type="checkbox" value="${esc(t.iri)}"> ${esc(t.name)}</label>`).join('')
        : '<span class="muted">No tissues available</span>';
    } catch (e) { $('#so-tissues').innerHTML = `<span class="muted">Failed to load tissues: ${esc(e.message)}</span>`; }

    $('#so-save').addEventListener('click', async () => {
      const val = id => ($('#' + id) ? $('#' + id).value.trim() : '');
      const tissue_iris = [...document.querySelectorAll('#so-tissues input:checked')].map(c => c.value);
      if (!val('so-label')) return toast('Label is required');
      if (!val('so-definition')) return toast('Definition is required');
      if (!val('so-defsrc')) return toast('Definition source is required');
      if (!tissue_iris.length) return toast('Select at least one target tissue');
      const btn = $('#so-save');
      btn.disabled = true; btn.textContent = 'Creating…';
      try {
        const created = await api('disease', { method: 'POST', body: {
          data: { label: val('so-label'), definition: val('so-definition'),
            def_source: [val('so-defsrc')], tissue_iris, parent_iri: parent.iri,
            synonyms: val('so-synonyms'), disease_category: val('so-category') },
          editor: val('so-editor') || me.login || 'curator' } });
        // A new subtype needs curating, so it joins the creator's queue.
        await api('assignments', { method: 'POST', body: { login: me.login, iris: [created.iri] } });
        closeModals();
        await refreshQueue();
        openDisease(created.iri);
        toast('Created ' + created.name + ' — added to your queue');
      } catch (e) {
        toast('Create failed: ' + e.message);
        btn.disabled = false; btn.textContent = 'Create subtype';
      }
    });
  }

  function closeModals() {
    document.querySelectorAll('.scrim').forEach(s => { s.classList.remove('open'); s.innerHTML = ''; });
  }

  // -------------------------------------------------------------- publish
  async function publish() {
    if (!requireAuth()) return;
    const comment = window.prompt('Optional comment for the pull request:', 'Mappings review');
    if (comment === null) return;
    const orcid = (localStorage.getItem('ari_editor_orcid') || '').trim();
    const author = orcid ? 'orcid:' + orcid : 'github:' + me.login;
    const names = queue.diseases.filter(d => d.remaining < d.total).map(d => d.ari_id).filter(Boolean);
    let lab = names.slice(0, 6).join(', ');
    if (names.length > 6) lab += ', +' + (names.length - 6) + ' more';
    const btn = $('#publish');
    btn.disabled = true; btn.textContent = 'Publishing…';
    try {
      // confirmed/flagged are omitted on purpose: the server falls back to the
      // autosaved decisions, which are the single source of truth.
      const r = await api('publish', { method: 'POST', body: {
        disease: 'mappings review',
        message: '[' + (lab || 'cross-references') + '] - mappings review',
        comment, author, branch: sessionBranch, labels: ['edit term', 'sssom'] } });
      sessionBranch = r.branch;
      const pl = $('#prlink');
      pl.textContent = 'PR #' + r.pr_number + (r.fork ? ' (fork) ↗' : ' ↗');
      pl.href = r.pr_url; pl.style.display = '';
      await refreshQueue();
      if (panel) await reloadPanel();
      toast('Published to PR #' + r.pr_number);
    } catch (e) {
      toast('Publish failed: ' + e.message);
      renderProgress();
    }
  }

  // -------------------------------------------------------------- hotkeys
  function onKey(ev) {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    // Elements that own Enter/Space themselves: a focused <summary> must be able to
    // expand the target details without `Enter` marking the disease done instead.
    const tag = (ev.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || tag === 'summary') {
      if (ev.key === 'Escape') ev.target.blur();
      return;
    }
    if (ev.key === 'Escape') { closeModals(); closeMenus(); return; }
    if (document.querySelector('.scrim.open')) return;
    if (document.querySelector('.menu.open')) return;
    if (!panel) return;
    const e = active ? panel.databases.find(x => x.key === active.db) : null;
    // Gated on *this id's* verdict, not the database's: with several candidates the
    // others still need judging after one of them is settled.
    const c = e ? (e.candidates || []).find(x => String(x.id) === String(active.id)) : null;
    const settled = e ? (c ? c.decision : e.no_value_decision) : null;
    const k = ev.key.toLowerCase();
    if (k === 'j') { ev.preventDefault(); return stepDisease(1); }
    if (k === 'k') { ev.preventDefault(); return stepDisease(-1); }
    if (ev.key === 'Enter') { ev.preventDefault(); return toggleDone(); }
    if (k === '/') { ev.preventDefault(); return $('#filter').focus(); }
    if (!e || settled) return;
    if (k === 'y') { ev.preventDefault(); return decide(e.key, active.id, 'confirm'); }
    if (k === 'n') { ev.preventDefault(); return decide(e.key, active.id, 'reject'); }
    // "No correct value" contradicts an id already confirmed here — see renderReview.
    if (k === 'x') {
      if ((e.candidates || []).some(x => x.decision && x.decision.verdict === 'confirm')) return;
      ev.preventDefault(); return decide(e.key, '', 'no_value');
    }
    // Skip needs an id to hang the decision on (POST /api/v2/decisions rejects
    // every verdict but no_value without one), so it is offered only when the
    // database has a candidate. Use x — "no correct value" — for an empty one.
    if (k === 's' && active.id) { ev.preventDefault(); return decide(e.key, active.id, 'skip'); }
  }

  // -------------------------------------------------------------- splitter
  function initDivider() {
    const div = $('#divider'), body = document.querySelector('.body'), side = $('#review');
    let dragging = false;
    const move = ev => {
      if (!dragging) return;
      const x = ev.touches ? ev.touches[0].clientX : ev.clientX;
      const rect = body.getBoundingClientRect();
      const w = Math.max(300, Math.min(rect.width - 460, rect.right - x));
      side.style.width = w + 'px';
      try { localStorage.setItem('ari_review_pane_w', String(w)); } catch (err) { /* private mode */ }
    };
    const start = ev => { dragging = true; document.body.classList.add('dragging'); ev.preventDefault(); };
    const end = () => { dragging = false; document.body.classList.remove('dragging'); };
    div.addEventListener('mousedown', start);
    div.addEventListener('touchstart', start, { passive: false });
    window.addEventListener('mousemove', move);
    window.addEventListener('touchmove', move, { passive: false });
    window.addEventListener('mouseup', end);
    window.addEventListener('touchend', end);
    try {
      const w = parseInt(localStorage.getItem('ari_review_pane_w'), 10);
      if (w > 0) side.style.width = w + 'px';
    } catch (err) { /* private mode */ }
  }

  // ------------------------------------------------------------------ init
  async function init() {
    try { me = await api('me'); } catch (e) { me = { github_enabled: false, authenticated: false }; }
    try { buildDatabases(await api('xref-databases')); }
    catch (e) {
      $('#panel').innerHTML = `<div class="r-empty">Failed to load the database registry: ${esc(e.message)}</div>`;
      return;
    }
    renderHeader();

    if (!me.authenticated) {
      $('#q-list').innerHTML = `<div style="padding:16px" class="muted">
        Sign in with GitHub to load your assigned queue.</div>`;
      $('#panel').innerHTML = `<div class="r-empty">
        <div>Your review queue is per-curator.<br>Sign in with GitHub to see the diseases assigned to you.</div></div>`;
      wireChrome();
      return;
    }
    try { queue = await api('queue'); }
    catch (e) {
      $('#q-list').innerHTML = `<p class="muted" style="padding:14px">Could not load your queue: ${esc(e.message)}</p>`;
      wireChrome();
      return;
    }
    renderQueue();
    renderProgress();
    wireChrome();
    initDivider();
    setInterval(tickSaved, 5000);
    const hash = decodeURIComponent((location.hash || '').replace(/^#/, ''));
    const start = queue.diseases.find(d => d.iri === hash)
      || queue.diseases.find(d => !d.done) || queue.diseases[0];
    if (start) openDisease(start.iri);
  }

  function wireChrome() {
    $('#filter').addEventListener('input', ev => { filter = ev.target.value; renderQueue(); });
    $('#db-filter').addEventListener('change', ev => { dbFilter = ev.target.value; renderPanel(); renderProgress(); });
    $('#q-sort').addEventListener('change', ev => { sortBy = ev.target.value; renderQueue(); });
    $('#assignment-btn').addEventListener('click', openAssign);
    $('#assign-more').addEventListener('click', openAssign);
    $('#new-subtype').addEventListener('click', () => openSubtype(activeIri));
    $('#summary-btn').addEventListener('click', openSummary);
    $('#publish').addEventListener('click', publish);
    document.querySelectorAll('.scrim').forEach(s =>
      s.addEventListener('click', ev => { if (ev.target === s) closeModals(); }));
    document.addEventListener('click', ev => { if (!ev.target.closest('.menu-wrap')) closeMenus(); });
    document.addEventListener('keydown', onKey);
    window.addEventListener('hashchange', () => {
      const iri = decodeURIComponent((location.hash || '').replace(/^#/, ''));
      if (iri && iri !== activeIri) openDisease(iri);
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
