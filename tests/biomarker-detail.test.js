// A biomarker row's expanded detail: its shared concept and the concept's relatives.
//
// No framework: `node --test tests/biomarker-detail.test.js`.

const test = require('node:test');
const assert = require('node:assert/strict');

const { conceptDetailHtml } = require('../static/js/biomarker-detail.js');

// The escaper panels.js passes in (static/js/core.js).
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const bare = (kind, label = 'Anti-X antibody') => ({
  iri: 'http://www.aurint.org/ontologies/ari-biomarkers#ARIB_0000009', id: 'ARIB:0000009',
  label, kind, alt_labels: [], external: [], parents: [], children: [], targets: [],
  targeted_by: [], same_target: [], appears_with: [], also_in: [], body_parts: [], symptoms: [],
});
const detail = (concepts, status = 'linked', reason = null) =>
  ({ entry: 'http://www.aurint.org/ontologies/ari-disease#Ab_X', status, reason, concepts });
const count = (html, text) => html.split(text).length - 1;

test('unlinked renders "No shared concept yet."', () => {
  assert.match(conceptDetailHtml(detail([], 'unlinked'), esc), /No shared concept yet\./);
  assert.match(conceptDetailHtml(detail([], 'no_concept', 'no term'), esc), /No shared concept: no term\./);
});

test('a concept with no relatives renders "none recorded" in each empty section', () => {
  // Antibodies have target and same-target sections, antigens a targeted-by
  // one, tests neither; every kind has the other seven.
  assert.equal(count(conceptDetailHtml(detail([bare('antibodies')]), esc), 'none recorded'), 9);
  assert.equal(count(conceptDetailHtml(detail([bare('antigens')]), esc), 'none recorded'), 8);
  assert.equal(count(conceptDetailHtml(detail([bare('tests')]), esc), 'none recorded'), 7);
});

test('labels are escaped', () => {
  const c = bare('antibodies', '<b>Anti-"X"</b>');
  c.alt_labels = ['<i>alt</i>'];
  c.targets = [{ iri: 'http://x/1', label: '<script>t</script>', external: [
    { predicate: 'skos:exactMatch', iri: 'http://purl.obolibrary.org/obo/PR_1', curie: 'PR:1', label: '<em>p</em>' }] }];
  c.appears_with = [{ iri: 'http://x/2', label: 'A & B', shared: 2 }];
  c.also_in = [{ iri: 'http://x/"d"', label: '<u>Disease</u>' }];
  const html = conceptDetailHtml(detail([c]), esc);
  for (const raw of ['<b>', '<i>', '<script>', '<em>', '<u>', 'A & B', 'x/"d"']) assert.ok(!html.includes(raw), raw);
  assert.ok(html.includes('&lt;b&gt;Anti-&quot;X&quot;&lt;/b&gt;'));
  assert.ok(html.includes('A &amp; B'));
});

test('also-in names are disease links and appears-with shows the shared count', () => {
  const c = bare('antibodies');
  c.appears_with = [{ iri: 'http://x/2', label: 'IA-2A', shared: 3 }];
  c.also_in = [{ iri: 'http://x/lada', label: 'LADA' }];
  const html = conceptDetailHtml(detail([c]), esc);
  assert.match(html, /<a href="#" class="concept-disease" data-iri="http:\/\/x\/lada">LADA<\/a>/);
  assert.match(html, /IA-2A \(3 diseases\)/);
});
