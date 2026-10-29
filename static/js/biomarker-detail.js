// The expanded row under an antibody, antigen or clinical-biomarker entry: the
// shared ARIB concept the entry links (several, for a bundle) and the concept's
// relatives, from /api/v2/biomarker-concept/<entry iri>. Read-only: concepts
// and their relations are edited in Protégé, in the data repo.
//
// Loaded as a classic script before panels.js, and by `node --test` through the
// CommonJS export at the bottom. `esc` is the escaper panels.js uses, passed in.

(function (root) {
  'use strict';

  const NONE = '<span class="concept-none">none recorded</span>';

  function list(items, render) {
    return items.length ? items.map(render).join(' &middot; ') : NONE;
  }

  function section(title, body) {
    return `<dt>${title}</dt><dd>${body}</dd>`;
  }

  // An external term: its curie, linked, then the slice's label (none for HPO).
  // Anything but an exact match says which match it is.
  function externalHtml(x, esc) {
    const kind = x.predicate === 'skos:exactMatch' ? '' : ` <span class="concept-pred">${esc(x.predicate.replace('skos:', ''))}</span>`;
    return `<a href="${esc(x.iri)}" target="_blank" rel="noopener">${esc(x.curie)}</a>` +
      (x.label ? ` ${esc(x.label)}` : '') + kind;
  }

  function conceptHtml(c, esc) {
    const ref = r => esc(r.label);
    const alt = c.alt_labels.length ? ` &middot; also called ${c.alt_labels.map(esc).join(', ')}` : '';
    let html = `<div class="concept"><div class="concept-head"><strong>${esc(c.label)}</strong> ` +
      `<span class="concept-id">${esc(c.id)}</span>${alt}</div><dl class="concept-rel">`;
    html += section('External', list(c.external, x => externalHtml(x, esc)));
    html += section('Parents', list(c.parents, ref));
    html += section('Children', list(c.children, ref));
    if (c.kind === 'antibodies') {
      html += section('Target antigen', list(c.targets, t =>
        esc(t.label) + (t.external.length ? ` (${t.external.map(x => externalHtml(x, esc)).join(', ')})` : '')));
      html += section('Same target', list(c.same_target, ref));
    }
    if (c.kind === 'antigens') html += section('Targeted by', list(c.targeted_by, ref));
    html += section('Appears with', list(c.appears_with, r =>
      `${esc(r.label)} (${r.shared} disease${r.shared === 1 ? '' : 's'})`));
    html += section('Also in', list(c.also_in, d =>
      `<a href="#" class="concept-disease" data-iri="${esc(d.iri)}">${esc(d.label)}</a>`));
    html += section('Body parts', list(c.body_parts, ref));
    html += section('Symptoms', list(c.symptoms, ref));
    return html + '</dl></div>';
  }

  function conceptDetailHtml(detail, esc) {
    switch (detail.status) {
      case 'unlinked': return '<div class="concept-empty">No shared concept yet.</div>';
      case 'no_concept': return `<div class="concept-empty">No shared concept: ${esc(detail.reason)}.</div>`;
      case 'linked': return conceptHtml(detail.concepts[0], esc);
      case 'bundle':
        return `<div class="concept-empty">This entry stands for ${detail.concepts.length} concepts.</div>` +
          detail.concepts.map(c => conceptHtml(c, esc)).join('');
      default: throw new Error(`Unknown concept status: ${detail.status}`);
    }
  }

  root.BiomarkerDetail = { conceptDetailHtml };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.BiomarkerDetail;
})(typeof globalThis !== 'undefined' ? globalThis : this);
