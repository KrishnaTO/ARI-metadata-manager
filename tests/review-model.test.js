// The review policy, exercised directly.
//
// static/ref-edits/ref-edits.js is 1,265 lines and holds the verdict state
// machine, the missing-first ordering, the separation-of-duties gate that
// decides when a confirm is offered, and the assembly of the publish payload.
// None of it was tested (issue #121). The backend re-checks authorship at
// publish, which is the right defence — but every other rule on that page is
// enforced only by this code.
//
// No framework: `node --test tests/review-model.test.js`.

const test = require('node:test');
const assert = require('node:assert/strict');

const RM = require('../static/ref-edits/review-model.js');

const DBS = [{ key: 'mondo', label: 'MONDO' }, { key: 'snomed', label: 'SNOMED' }];
const PREFIX = { mondo: 'MONDO', snomed: 'SNOMEDCT' };

function session(over = {}) {
  return {
    reviewed: {}, edited: {}, published: {}, mappings: {}, predicted: {},
    idAuthors: {}, PREFIX, DBS, me: null, sessionPr: null,
    ...over,
  };
}

const disease = (over = {}) => ({
  iri: 'http://x/d1', ari_id: 'ARI:0000001', name: 'Addison disease',
  mondo: [], snomed: [], ...over,
});

// --------------------------------------------------------------- id state
test('an id on file that nobody has judged reads as unreviewed', () => {
  const s = session(), r = disease({ mondo: ['0005147'] });
  assert.equal(RM.cellState(s, r, 'mondo'), 'have');
});

test('a verdict recorded this session wins over the stored mappings', () => {
  const r = disease({ mondo: ['0005147'] });
  const s = session({
    mappings: { 'ARI:0000001|MONDO|0005147': 'positive' },
    reviewed: { [RM.idKey(r.iri, 'mondo', '0005147')]: 'bad' },
  });
  assert.equal(RM.cellState(s, r, 'mondo'), 'bad');
});

test('a stored positive mapping pre-confirms the cell', () => {
  const r = disease({ mondo: ['0005147'] });
  const s = session({ mappings: { 'ARI:0000001|MONDO|0005147': 'positive' } });
  assert.equal(RM.cellState(s, r, 'mondo'), 'ok');
});

test('a cell reads confirmed only once every id in it has a verdict', () => {
  const r = disease({ mondo: ['0005147', '0009999'] });
  const s = session({ reviewed: { [RM.idKey(r.iri, 'mondo', '0005147')]: 'ok' } });
  assert.equal(RM.cellState(s, r, 'mondo'), 'have', 'the unjudged second id must win');

  s.reviewed[RM.idKey(r.iri, 'mondo', '0009999')] = 'ok';
  assert.equal(RM.cellState(s, r, 'mondo'), 'ok');
});

test('a blank cell with no prediction has no state at all', () => {
  assert.equal(RM.cellState(session(), disease(), 'mondo'), null);
});

// ------------------------------------------------------------ predictions
test('a prediction only applies to a cell with no ids on file', () => {
  const s = session({
    predicted: { 'ARI:0000001|MONDO|0005147': { label: 'Addison', confidence: 'high' } },
  });
  assert.equal(RM.cellState(s, disease(), 'mondo'), 'pred');
  assert.equal(RM.cellState(s, disease({ mondo: ['0001'] }), 'mondo'), 'have',
    'an id on file must not be replaced by a prediction');
});

test('a synonym-only prediction is distinguished from a label match', () => {
  const s = session({
    predicted: { 'ARI:0000001|MONDO|0005147': { label: 'Addison', confidence: 'low' } },
  });
  assert.equal(RM.cellState(s, disease(), 'mondo'), 'low');
});

test('a prediction already judged negative is not offered again', () => {
  const s = session({
    predicted: { 'ARI:0000001|MONDO|0005147': { label: 'Addison', confidence: 'high' } },
    mappings: { 'ARI:0000001|MONDO|0005147': 'negative' },
  });
  assert.deepEqual(RM.predFor(s, disease(), 'mondo'), []);
  assert.equal(RM.cellState(s, disease(), 'mondo'), null);
});

// ------------------------------------------------------ not in the database
test('a session "not in database" verdict overrides the stored mappings', () => {
  const r = disease();
  const s = session({
    mappings: { 'ARI:0000001|MONDO|NoTermFound': 'absent' },
    reviewed: { [RM.absentKey(r.iri, 'mondo')]: 'ok' },
  });
  assert.equal(RM.isAbsent(s, r, 'mondo'), false);
});

test('a published "not in database" judgment shows without a session verdict', () => {
  const s = session({ mappings: { 'ARI:0000001|MONDO|NoTermFound': 'absent' } });
  assert.equal(RM.cellState(s, disease(), 'mondo'), 'none');
});

// ----------------------------------------------------- missing / complete
test('every state except confirmed and no-term still counts as missing', () => {
  const r = disease({ mondo: ['0005147'] });
  const cases = [
    [{}, 'have', true],
    [{ [RM.idKey(r.iri, 'mondo', '0005147')]: 'ok' }, 'ok', false],
    [{ [RM.idKey(r.iri, 'mondo', '0005147')]: 'bad' }, 'bad', true],
    [{ [RM.absentKey(r.iri, 'mondo')]: 'none' }, 'none', false],
  ];
  for (const [reviewed, state, missing] of cases) {
    const s = session({ reviewed });
    assert.equal(RM.cellState(s, r, 'mondo'), state);
    assert.equal(RM.isMissing(s, r, 'mondo'), missing, `state ${state}`);
  }
});

test('a disease is complete when nothing in the row awaits a verdict', () => {
  const r = disease({ mondo: ['a'], snomed: ['b'] });
  const s = session();
  assert.equal(RM.isComplete(s, r), false);
  s.reviewed[RM.idKey(r.iri, 'mondo', 'a')] = 'ok';
  s.reviewed[RM.idKey(r.iri, 'snomed', 'b')] = 'bad';
  assert.equal(RM.isComplete(s, r), true, 'a flagged cell is resolved, not open');
});

// ------------------------------------------------- separation of duties
test('a curator may not confirm an id they added themselves', () => {
  const r = disease({ mondo: ['0005147'] });
  const s = session({
    me: { login: 'alice' },
    idAuthors: { [RM.idKey(r.iri, 'mondo', '0005147')]: 'alice' },
  });
  assert.equal(RM.mayConfirm(s, r.iri, 'mondo', '0005147'), false);
});

test('another curator may confirm it', () => {
  const r = disease({ mondo: ['0005147'] });
  const s = session({
    me: { login: 'bob' },
    idAuthors: { [RM.idKey(r.iri, 'mondo', '0005147')]: 'alice' },
  });
  assert.equal(RM.mayConfirm(s, r.iri, 'mondo', '0005147'), true);
});

test('an id with no recorded author is confirmable', () => {
  const s = session({ me: { login: 'alice' } });
  assert.equal(RM.mayConfirm(s, 'http://x/d1', 'mondo', '0005147'), true);
});

test('a signed-out viewer is never treated as the adder', () => {
  const s = session({ idAuthors: { 'http://x/d1|mondo|1': 'alice' } });
  assert.equal(RM.isOwnAddition(s, 'http://x/d1', 'mondo', '1'), false);
});

// ------------------------------------------------------------- publishing
test('a new pull request carries only work not already published', () => {
  const s = session({
    reviewed: { a: 'ok', b: 'bad' },
    published: { a: { pr: 7, state: 'ok' } },
  });
  assert.deepEqual([...RM.publishKeys(s, true)], ['b']);
});

test('appending to the tracked pull request re-sends the keys already on it', () => {
  const s = session({
    reviewed: { a: 'ok', b: 'bad' },
    published: { a: { pr: 7, state: 'ok' } },
    sessionPr: { number: 7 },
  });
  assert.deepEqual([...RM.publishKeys(s, false)].sort(), ['a', 'b']);
});

test('a key published to a different pull request is not re-sent', () => {
  const s = session({
    reviewed: { a: 'ok' },
    published: { a: { pr: 99, state: 'ok' } },
    sessionPr: { number: 7 },
  });
  assert.deepEqual([...RM.publishKeys(s, false)], []);
});

test('changing a verdict after publishing makes the key pending again', () => {
  const s = session({
    reviewed: { a: 'bad' },
    published: { a: { pr: 7, state: 'ok' } },
  });
  assert.equal(RM.isPending(s, 'a'), true);
});

test('an edited id is publishable even when its verdict is unchanged', () => {
  const s = session({
    reviewed: { a: 'ok' },
    edited: { a: true },
    published: { a: { pr: 7, state: 'ok' } },
  });
  assert.equal(RM.keyState(s, 'a'), 'ok+e');
  assert.equal(RM.isPending(s, 'a'), true);
});

// ---------------------------------------------------------------- sorting
test('missing-first ranks the emptiest cells ahead of the resolved ones', () => {
  const s = session({
    predicted: { 'ARI:B|MONDO|x': { label: 'x', confidence: 'high' } },
  });
  const blank = { iri: 'i/a', ari_id: 'ARI:A', name: 'A', mondo: [], snomed: [] };
  const pred = { iri: 'i/b', ari_id: 'ARI:B', name: 'B', mondo: [], snomed: [] };
  const onFile = { iri: 'i/c', ari_id: 'ARI:C', name: 'C', mondo: ['1'], snomed: [] };
  const confirmed = { iri: 'i/d', ari_id: 'ARI:D', name: 'D', mondo: ['2'], snomed: [] };
  s.reviewed[RM.idKey('i/d', 'mondo', '2')] = 'ok';

  const sorted = [confirmed, onFile, pred, blank]
    .sort((a, b) => RM.compareByColumn(s, a, b, 'mondo', 'missing'))
    .map(r => r.name);
  assert.deepEqual(sorted, ['A', 'B', 'C', 'D']);
});

test('the reverse direction puts the resolved cells first', () => {
  const s = session();
  const onFile = { iri: 'i/c', ari_id: 'ARI:C', name: 'C', mondo: ['1'], snomed: [] };
  const confirmed = { iri: 'i/d', ari_id: 'ARI:D', name: 'D', mondo: ['2'], snomed: [] };
  s.reviewed[RM.idKey('i/d', 'mondo', '2')] = 'ok';
  const sorted = [onFile, confirmed]
    .sort((a, b) => RM.compareByColumn(s, a, b, 'mondo', 'mapped'))
    .map(r => r.name);
  assert.deepEqual(sorted, ['D', 'C']);
});

test('ties inside a column fall back to disease name', () => {
  const s = session();
  const b = { iri: 'i/b', ari_id: 'ARI:B', name: 'Beta', mondo: ['1'], snomed: [] };
  const a = { iri: 'i/a', ari_id: 'ARI:A', name: 'Alpha', mondo: ['1'], snomed: [] };
  const sorted = [b, a].sort((x, y) => RM.compareByColumn(s, x, y, 'mondo', 'missing'));
  assert.deepEqual(sorted.map(r => r.name), ['Alpha', 'Beta']);
});

test('name sort reverses on za', () => {
  const a = { name: 'Alpha' }, b = { name: 'Beta' };
  assert.ok(RM.compareByName(a, b, 'az') < 0);
  assert.ok(RM.compareByName(a, b, 'za') > 0);
});
