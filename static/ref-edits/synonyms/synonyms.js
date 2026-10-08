// Synonym vs subtype review: report 9 from the ARI repo, with curators' marks.
//
// One read of /api/v2/synonym-review (the TSV, parsed, plus the shared curation
// marks) and one of /api/v2/xrefs (to link each disease back to its row on the
// reference-review page). The cards pick a view, the filters narrow it, and the
// table groups rows by disease. A signed-in curator marks each row correct,
// incorrect or needs-review and can leave a note; both save to the server as
// soon as they change (PUT /api/v2/synonym-review/curation).
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

  const MARKS = [
    { status: 'correct', glyph: '✓', title: 'The verdict and action are correct' },
    { status: 'incorrect', glyph: '✗', title: 'The verdict or action is wrong (say why in the note)' },
    { status: 'needs-review', glyph: '?', title: 'Unsure: needs another curator to look' },
  ];

  let ROWS = [];
  let CURATION = {};  // "<ari_id>|<term>" -> {status, note, by, at, ...}
  let LOGIN = null;
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
    const status = $('#status').value;
    return ROWS.filter(r => test(r)
      && (!verdict || r.verdict === verdict)
      && (!decided || r.decided_by === decided)
      && statusMatches(reviewOf(r).status || '', status)
      && (!q || [r.ari_id, r.disease, r.term, r.other_names, r.note]
        .some(s => s.toLowerCase().includes(q))));
  }

  const keyOf = r => r.ari_id + '|' + r.term;
  const reviewOf = r => CURATION[keyOf(r)] || {};

  function statusMatches(s, want) {
    if (!want) return true;
    if (want === 'none') return !s;
    if (want === 'open') return !s || s === 'needs-review';
    return s === want;
  }

  function stamp(c) {
    return c.by ? `@${c.by} · ${String(c.at).slice(0, 10)}` : '';
  }

  function reviewHtml(r) {
    const c = reviewOf(r);
    const off = LOGIN ? '' : ' disabled';
    return `<td class="review" data-key="${esc(keyOf(r))}"><div class="marks">` +
      MARKS.map(m => `<button data-status="${m.status}" aria-pressed="${c.status === m.status}"` +
        ` title="${esc(m.title)}"${off}>${m.glyph}</button>`).join('') + `</div>` +
      `<textarea rows="2" placeholder="${LOGIN ? 'Note' : ''}"${off}>${esc(c.note || '')}</textarea>` +
      `<span class="by">${esc(stamp(c))}</span></td>`;
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
      ${reviewHtml(r)}
    </tr>`;
  }

  function renderTable() {
    const rows = matches();
    const shown = rows.slice(0, limit);
    const reviewed = rows.filter(r => reviewOf(r).status).length;
    $('#count').textContent = `${rows.length.toLocaleString()} names in ` +
      `${new Set(rows.map(r => r.ari_id)).size.toLocaleString()} diseases · ` +
      `${reviewed.toLocaleString()} reviewed`;
    if (!rows.length) { $('#table').innerHTML = '<p class="empty">No names match.</p>'; return; }
    let body = '';
    let last = null;
    for (const r of shown) {
      if (r.ari_id !== last) {
        last = r.ari_id;
        const iri = IRI[r.ari_id];
        body += `<tr class="disease"><td colspan="8"><span class="id">${esc(r.ari_id)}</span>${esc(r.disease)}` +
          (iri ? `<a href="../#${encodeURIComponent(iri)}">Open in review →</a>` : '') + `</td></tr>`;
      }
      body += rowHtml(r);
    }
    $('#table').innerHTML = `<table>
      <thead><tr><th>Name</th><th>ARI field</th><th>Verdict</th><th>Action</th><th>Decided by</th><th>Evidence</th><th>Note</th><th>Curation</th></tr></thead>
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

  // Save one row's status and note. The table is not re-rendered, so a row that
  // stops matching the status filter stays put until the filter changes.
  async function save(td, status, note) {
    const key = td.dataset.key;
    const row = ROWS.find(r => keyOf(r) === key);
    const by = td.querySelector('.by');
    const res = await fetch(apiUrl('synonym-review/curation'), {
      method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ row, status, note }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) {
      by.textContent = `Not saved: ${d.detail || 'HTTP ' + res.status}`;
      by.classList.add('err');
      return;
    }
    if (d.entry) CURATION[key] = d.entry; else delete CURATION[key];
    const c = reviewOf(row);
    td.querySelectorAll('.marks button').forEach(b =>
      b.setAttribute('aria-pressed', String(b.dataset.status === c.status)));
    by.textContent = stamp(c);
    by.classList.remove('err');
    const reviewed = matches().filter(r => reviewOf(r).status).length;
    $('#count').textContent = $('#count').textContent.replace(/[\d,]+ reviewed$/, `${reviewed.toLocaleString()} reviewed`);
  }

  async function load() {
    const [report, xrefs] = await Promise.all([getJson('synonym-review'), getJson('xrefs')]);
    ROWS = report.rows;
    CURATION = report.curation;
    LOGIN = report.login;
    $('#signin').hidden = !!LOGIN;
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
  $('#status').addEventListener('change', refresh);
  // Clicking the pressed mark clears it.
  $('#table').addEventListener('click', e => {
    const b = e.target.closest('.marks button');
    if (!b) return;
    const td = b.closest('td.review');
    const status = b.getAttribute('aria-pressed') === 'true' ? '' : b.dataset.status;
    save(td, status, td.querySelector('textarea').value);
  });
  // A note saves when the box loses focus with a changed value.
  $('#table').addEventListener('change', e => {
    if (e.target.tagName !== 'TEXTAREA') return;
    const td = e.target.closest('td.review');
    save(td, reviewOf(ROWS.find(r => keyOf(r) === td.dataset.key)).status || '', e.target.value);
  });
  $('#table').addEventListener('click', e => {
    if (e.target.id !== 'more') return;
    limit = Infinity;
    renderTable();
  });

  load().catch(err => {
    $('#table').innerHTML = `<p class="error">Could not load report 9: ${esc(err.message)}</p>`;
  });
})();
