// Synonym vs subtype review: report 9 from the ARI repo, read-only.
//
// One read of /api/v2/synonym-review (the TSV, parsed) and one of /api/v2/xrefs
// (to link each disease back to its row on the reference-review page). The cards
// pick a view, the filters narrow it, and the table groups rows by disease.
(function () {
  'use strict';

  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const apiUrl = p => new URL('../../api/v2/' + p, location.href).href;

  // Actions that would change ARI itself; everything else keeps it as it is.
  const isChange = a => /^(withdraw|remove|move|restore)/.test(a);

  const VIEWS = [
    { key: 'changes', label: 'Proposed ARI changes', sub: 'withdraw, remove, move or restore', test: r => isChange(r.action) },
    { key: 'keep', label: 'Kept as is', sub: 'ARI names the review confirms', test: r => r.action === 'keep' },
    { key: 'cand-syn', label: 'Candidate synonyms', sub: 'database names ARI lacks', test: r => r.action === 'candidate synonym' },
    { key: 'cand-sub', label: 'Candidate subtypes', sub: 'narrower database terms', test: r => r.action === 'candidate clinical subtype' },
    { key: 'all', label: 'All names', sub: '', test: () => true },
  ];
  const PAGE = 400;   // rows rendered before "Show all"; the full report is ~7,000

  let ROWS = [];
  let IRI = {};       // ARI id -> disease IRI, for the link back to reference review
  let view = 'changes';
  let limit = PAGE;

  function fill(sel, label, values) {
    sel.innerHTML = `<option value="">${esc(label)}</option>` +
      values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  }

  function renderCards() {
    $('#cards').innerHTML = VIEWS.map(v => {
      const n = ROWS.filter(v.test).length;
      return `<button class="card" data-view="${v.key}" aria-pressed="${v.key === view}">` +
        `<div class="k">${esc(v.label)}</div><div class="n">${n.toLocaleString()}</div>` +
        (v.sub ? `<div class="sub">${esc(v.sub)}</div>` : '') + `</button>`;
    }).join('');
  }

  function matches() {
    const test = VIEWS.find(v => v.key === view).test;
    const q = $('#q').value.trim().toLowerCase();
    const verdict = $('#verdict').value;
    const decided = $('#decided').value;
    return ROWS.filter(r => test(r)
      && (!verdict || r.verdict === verdict)
      && (!decided || r.decided_by === decided)
      && (!q || [r.ari_id, r.disease, r.term, r.other_names, r.note]
        .some(s => s.toLowerCase().includes(q))));
  }

  function rowHtml(r) {
    return `<tr>
      <td class="term">${esc(r.term)}${r.other_names ? `<span class="alt">${esc(r.other_names)}</span>` : ''}</td>
      <td class="field">${esc(r.ari_field || '—')}</td>
      <td><span class="pill v-${esc(r.verdict)}">${esc(r.verdict)}</span></td>
      <td class="action${isChange(r.action) ? ' change' : ''}">${esc(r.action)}</td>
      <td class="small">${esc(r.decided_by)}</td>
      <td class="small">${esc(r.evidence)}</td>
      <td class="small">${esc(r.note)}</td>
    </tr>`;
  }

  function renderTable() {
    const rows = matches();
    const shown = rows.slice(0, limit);
    $('#count').textContent = `${rows.length.toLocaleString()} names in ` +
      `${new Set(rows.map(r => r.ari_id)).size.toLocaleString()} diseases`;
    if (!rows.length) { $('#table').innerHTML = '<p class="empty">No names match.</p>'; return; }
    let body = '';
    let last = null;
    for (const r of shown) {
      if (r.ari_id !== last) {
        last = r.ari_id;
        const iri = IRI[r.ari_id];
        body += `<tr class="disease"><td colspan="7"><span class="id">${esc(r.ari_id)}</span>${esc(r.disease)}` +
          (iri ? `<a href="../#${encodeURIComponent(iri)}">Open in review →</a>` : '') + `</td></tr>`;
      }
      body += rowHtml(r);
    }
    $('#table').innerHTML = `<table>
      <thead><tr><th>Name</th><th>ARI field</th><th>Verdict</th><th>Action</th><th>Decided by</th><th>Evidence</th><th>Note</th></tr></thead>
      <tbody>${body}</tbody></table>` +
      (rows.length > shown.length
        ? `<button class="more" id="more">Show all ${rows.length.toLocaleString()}</button>` : '');
  }

  function refresh() { limit = PAGE; renderTable(); }

  async function getJson(p) {
    const r = await fetch(apiUrl(p), { credentials: 'same-origin' });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `${p}: HTTP ${r.status}`);
    }
    return r.json();
  }

  async function load() {
    const [report, xrefs] = await Promise.all([getJson('synonym-review'), getJson('xrefs')]);
    ROWS = report.rows;
    for (const x of xrefs) if (x.ari_id) IRI[x.ari_id] = x.iri;
    const dates = [...new Set(ROWS.map(r => r.review_date))].sort();
    $('#stamp').textContent = `${report.source} · reviewed ${dates.join(', ')}`;
    fill($('#verdict'), 'Any verdict', [...new Set(ROWS.map(r => r.verdict))].sort());
    fill($('#decided'), 'Decided by anyone', [...new Set(ROWS.map(r => r.decided_by))].sort());
    renderCards();
    renderTable();
  }

  $('#cards').addEventListener('click', e => {
    const b = e.target.closest('[data-view]');
    if (!b) return;
    view = b.dataset.view;
    renderCards();
    refresh();
  });
  $('#q').addEventListener('input', refresh);
  $('#verdict').addEventListener('change', refresh);
  $('#decided').addEventListener('change', refresh);
  $('#table').addEventListener('click', e => {
    if (e.target.id !== 'more') return;
    limit = Infinity;
    renderTable();
  });

  load().catch(err => {
    $('#table').innerHTML = `<p class="error">Could not load report 9: ${esc(err.message)}</p>`;
  });
})();
