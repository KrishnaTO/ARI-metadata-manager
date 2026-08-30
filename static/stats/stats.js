// The curation dashboard: one read of /api/v2/stats, rendered.
//
// Nothing measured the activity the product exists to produce — the only trace
// was a gitignored log on one host — so no prioritisation decision about the
// tool had any input (issue #124). Each section below answers one of the
// questions that could not be answered at all.
(function () {
  'use strict';

  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const apiUrl = p => new URL('../api/v2/' + p, location.href).href;

  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const pct = (n, d) => (d ? Math.round(n / d * 100) : 0);

  // A curator id is `github:login` or `orcid:0000-…`; show the readable half.
  const who = c => String(c || '').replace(/^github:/, '@').replace(/^orcid:/, 'ORCID ');

  function cards(d) {
    const w = d.waiting || {};
    const confirmed = (d.curators || []).reduce((n, c) => n + c.confirmed, 0);
    const flagged = (d.curators || []).reduce((n, c) => n + c.flagged, 0);
    return `<div class="cards">
      ${card('Diseases', d.diseases, '')}
      ${card('Mappings confirmed', confirmed, flagged + ' flagged')}
      ${card('Ids awaiting a verdict', w.ids_unjudged, 'on file, never judged', 'warn')}
      ${card('Waiting on a second reviewer', w.needs_second_reviewer,
             'the adder cannot confirm their own', 'bad')}
      ${card('In review queues', (d.queues || {}).assigned,
             ((d.queues || {}).done || 0) + ' marked done')}
    </div>`;
  }

  function card(k, n, sub, kind) {
    return `<div class="card${kind ? ' ' + kind : ''}"><div class="k">${esc(k)}</div>` +
      `<div class="n">${Number(n || 0).toLocaleString()}</div>` +
      (sub ? `<div class="sub">${esc(sub)}</div>` : '') + `</div>`;
  }

  function coverageTable(rows) {
    if (!rows.length) return '<p class="empty">No databases configured.</p>';
    return `<div class="scroll"><table>
      <thead><tr><th>Database</th><th>Has an id</th><th>Confirmed</th><th>Flagged</th>
        <th>Awaiting a verdict</th><th>Blank</th><th>Judged</th></tr></thead>
      <tbody>${rows.map(r => {
        const done = pct(r.confirmed + r.flagged, r.with_id);
        return `<tr><td>${esc(r.label)}</td><td>${r.with_id}</td><td>${r.confirmed}</td>` +
          `<td>${r.flagged}</td><td>${r.unjudged}</td><td>${r.blank}</td>` +
          `<td><span class="bar"><span style="width:${done}%"></span></span>${done}%</td></tr>`;
      }).join('')}</tbody></table></div>`;
  }

  function curatorTable(rows) {
    if (!rows.length) return '<p class="empty">No judgments recorded yet.</p>';
    return `<div class="scroll"><table>
      <thead><tr><th>Curator</th><th>Confirmed</th><th>Flagged</th><th>No term</th>
        <th>Ids added</th><th>First</th><th>Last</th></tr></thead>
      <tbody>${rows.map(r => `<tr><td class="who">${esc(who(r.curator))}</td>` +
        `<td>${r.confirmed}</td><td>${r.flagged}</td><td>${r.no_term}</td>` +
        `<td>${r.ids_added}</td><td>${esc(r.first)}</td><td>${esc(r.last)}</td></tr>`).join('')}
      </tbody></table></div>`;
  }

  function queueTable(q) {
    const rows = (q && q.curators) || [];
    if (!rows.length) {
      return '<p class="empty">Nothing is assigned. Diseases are claimed from the ' +
        'cross-reference page by selecting rows and adding them to a queue.</p>';
    }
    return `<div class="scroll"><table>
      <thead><tr><th>Curator</th><th>Assigned</th><th>Done</th><th>Progress</th></tr></thead>
      <tbody>${rows.map(r => `<tr><td class="who">${esc(who(r.curator))}</td>` +
        `<td>${r.assigned}</td><td>${r.done}</td>` +
        `<td><span class="bar"><span style="width:${pct(r.done, r.assigned)}%"></span></span></td></tr>`)
        .join('')}</tbody></table></div>`;
  }

  function weeksChart(weeks) {
    const el = document.getElementById('weeks-chart');
    if (!el || !weeks.length || typeof Chart === 'undefined') return;
    new Chart(el, {
      type: 'bar',
      data: {
        labels: weeks.map(w => w.week),
        datasets: [
          { label: 'Confirmed', data: weeks.map(w => w.confirmed), backgroundColor: css('--ok') },
          { label: 'Flagged', data: weeks.map(w => w.flagged), backgroundColor: css('--bad') },
          { label: 'No term', data: weeks.map(w => w.no_term), backgroundColor: css('--warn') },
        ],
      },
      options: {
        maintainAspectRatio: false,
        scales: { x: { stacked: true, grid: { display: false } },
                  y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } },
        plugins: { legend: { position: 'bottom' } },
      },
    });
  }

  function render(d) {
    $('#stamp').textContent = d.generated || '';
    $('#main').innerHTML = cards(d) + `
      <section>
        <h2>Throughput by week</h2>
        <p class="why">Judgments recorded per ISO week, from the published mapping set.
          This is the record of what curation actually produced.</p>
        <div class="chart"><canvas id="weeks-chart"></canvas></div>
      </section>
      <section>
        <h2>Where the work is, by database</h2>
        <p class="why">A database with many ids on file and few judged is one that is
          stalling — which no single screen of the review grid can show.</p>
        ${coverageTable(d.coverage || [])}
      </section>
      <section>
        <h2>By curator</h2>
        <p class="why">Adding an id and judging one are different contributions, and
          under the two-person rule they are never the same person for the same id.</p>
        ${curatorTable(d.curators || [])}
      </section>
      <section>
        <h2>Review queues</h2>
        <p class="why">Claimed work and how much of it is finished.</p>
        ${queueTable(d.queues)}
      </section>`;
    weeksChart(d.weeks || []);
  }

  fetch(apiUrl('stats'))
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(render)
    .catch(e => {
      $('#main').innerHTML = `<section><h2>Could not load the figures</h2>` +
        `<p class="why">${esc(e.message)}. The dashboard reads /api/v2/stats, which ` +
        `needs the app to be running and the ontology loaded.</p></section>`;
    });
})();
