// The cross-reference review policy, as pure functions.
//
// This is the verdict state machine, the separation-of-duties gate, the
// missing-first ordering and the publish payload — every rule on the review
// page except the ones the server re-checks. It lived inside the page's IIFE,
// wired directly to its mutable closure state, so none of it could be tested
// (issue #121). Nothing here touches the DOM or the network: every function
// takes the session state it needs as an argument.
//
// Loaded as a classic script before ref-edits.js, and by `node --test` through
// the CommonJS export at the bottom.

(function (root) {
  'use strict';

  // SSSOM's "no term found in this database" sentinel: the id a cell-level
  // "not in database" verdict is stored and published under.
  const NO_TERM = 'NoTermFound';

  const idKey = (iri, db, id) => iri + '|' + db + '|' + id;
  const absentKey = (iri, db) => idKey(iri, db, NO_TERM);

  // A key's publishable state: the verdict, plus a marker when its id was
  // edited. Empty means there is nothing to publish for it.
  const keyState = (s, k) => (s.reviewed[k] || '') + (s.edited[k] ? '+e' : '');

  // Pending = has a state, and that state is not already on a pull request.
  const isPending = (s, k) => !!keyState(s, k) && (s.published[k] || {}).state !== keyState(s, k);

  // Predictions for a blank cell. A cell with ids on file is never predicted
  // into, and a prediction already judged negative is not offered again.
  function predFor(s, r, dbkey) {
    const ari = r.ari_id, prefix = s.PREFIX[dbkey];
    if (!ari || !prefix || (r[dbkey] || []).length) return [];
    const out = [];
    for (const [k, meta] of Object.entries(s.predicted)) {
      const [a, p, id] = k.split('|');
      if (a === ari && p === prefix && s.mappings[k] !== 'negative')
        out.push({ id, label: meta.label, match_field: meta.match_field,
                   confidence: meta.confidence, score: meta.score, band: meta.band });
    }
    return out;
  }

  // Per-id pre-judgment from the curated mappings: 'pos' | 'neg' | null.
  function preJudgmentId(s, r, dbkey, id) {
    const ari = r.ari_id, prefix = s.PREFIX[dbkey];
    if (!ari || !prefix) return null;
    const j = s.mappings[ari + '|' + prefix + '|' + id];
    if (j === 'positive') return 'pos';
    if (j === 'negative') return 'neg';
    return null;
  }

  // Per-id state, the single thing the glyphs, tints and counts derive from:
  //   ok    confirmed this session, or positive in the curated mappings
  //   bad   flagged this session, or negative in the curated mappings
  //   pred  lexical prediction for a blank cell (the label matched a concept)
  //   low   lexical prediction from a synonym only
  //   have  an id on file that nobody has judged yet
  function idState(s, r, dbkey, id, pred) {
    const k = idKey(r.iri, dbkey, id);
    if (s.reviewed[k] === 'ok') return 'ok';
    if (s.reviewed[k] === 'bad') return 'bad';
    const pre = preJudgmentId(s, r, dbkey, id);
    if (pre === 'pos') return 'ok';
    if (pre === 'neg') return 'bad';
    return pred ? (pred.confidence === 'low' ? 'low' : 'pred') : 'have';
  }

  // Everything in one cell: the ids on file, or — for a blank cell — predictions.
  function cellEntries(s, r, dbkey) {
    const ids = r[dbkey] || [];
    if (ids.length) return ids.map(id => ({ id, pred: null }));
    return predFor(s, r, dbkey).map(p => ({ id: p.id, pred: p }));
  }

  // The cell-level "this database has no term for the disease" verdict: this
  // session's, or one already published to the mapping files.
  function isAbsent(s, r, dbkey) {
    const k = absentKey(r.iri, dbkey);
    if (s.reviewed[k]) return s.reviewed[k] === 'none';   // this session overrides
    const prefix = s.PREFIX[dbkey];
    return !!(r.ari_id && prefix && s.mappings[r.ari_id + '|' + prefix + '|' + NO_TERM] === 'absent');
  }

  // A cell only reads confirmed once EVERY id in it has a verdict, so any
  // unjudged entry wins.
  function cellState(s, r, dbkey) {
    if (isAbsent(s, r, dbkey)) return 'none';
    const sts = cellEntries(s, r, dbkey).map(e => idState(s, r, dbkey, e.id, e.pred));
    if (!sts.length) return null;
    if (sts.includes('pred')) return 'pred';
    if (sts.includes('low')) return 'low';
    if (sts.includes('have')) return 'have';
    if (sts.includes('ok')) return 'ok';
    return 'bad';
  }

  const isOpenState = st => st === 'pred' || st === 'low' || st === 'have';

  // A database is missing a mapping for a disease until it is either confirmed
  // or declared to have no term for it — blank, predicted, unjudged and flagged
  // cells all still need one.
  const isMissing = (s, r, dbkey) => {
    const st = cellState(s, r, dbkey);
    return st !== 'ok' && st !== 'none';
  };

  // How much work a cell still needs, lowest first: blank cells lead, resolved
  // cells trail. Drives the missing-first column sort.
  const NEED = { bad: 1, low: 2, pred: 3, have: 4, none: 5, ok: 6 };
  const cellNeed = (s, r, dbkey) => NEED[cellState(s, r, dbkey)] || 0;

  // A disease is complete when nothing in its row is still awaiting a verdict.
  const isComplete = (s, r) => s.DBS.every(db => !isOpenState(cellState(s, r, db.key)));

  // ------------------------------------------------- separation of duties
  // Whoever adds a mapping id may not be the one who confirms it, so a second
  // curator always vouches for the match. Flagging, or declaring the database
  // empty, stays open to everyone — only the confirm is withheld.
  const addedBy = (s, iri, db, id) => s.idAuthors[idKey(iri, db, id)] || null;
  const isOwnAddition = (s, iri, db, id) =>
    !!(s.me && s.me.login && addedBy(s, iri, db, id) === s.me.login);
  const mayConfirm = (s, iri, db, id) => !isOwnAddition(s, iri, db, id);

  // The population the two-person rule creates, and had no workflow behind it.
  //
  // The rule is enforced — the ✓ is withheld from the adder, and the server
  // re-checks authorship on publish — but nothing ever routed an id *to* the
  // second curator. The adder finished, and the mapping waited until someone
  // happened to open that row (issue #124).
  //
  // An id qualifies when the authorship ledger names someone other than the
  // viewer and nobody has judged it yet. An id with no recorded author is
  // deliberately excluded: the ledger cannot say whose it is, so the rule does
  // not apply to it and including it would drown the scope in every unjudged
  // cell in the matrix.
  function needsSecondReview(s, r, dbkey, id) {
    const author = addedBy(s, r.iri, dbkey, id);
    if (!author) return false;
    if (s.me && s.me.login && author === s.me.login) return false;
    return idState(s, r, dbkey, id, null) === 'have';
  }

  // True when any cell in the disease is waiting on this curator's second pair
  // of eyes. Predictions never count: nobody added them.
  function awaitsSecondReviewer(s, r) {
    return s.DBS.some(db => (r[db.key] || [])
      .some(id => needsSecondReview(s, r, db.key, id)));
  }

  // ------------------------------------------------------------- publishing
  // Keys a publish covers. A new PR carries this session's pending work only;
  // appending to the tracked PR also re-sends the keys already on it, because
  // the mapping files are rebuilt from the base branch on every publish.
  function publishKeys(s, newPr) {
    const keys = new Set();
    for (const k of [...Object.keys(s.reviewed), ...Object.keys(s.edited)]) {
      if (isPending(s, k)) keys.add(k);
    }
    if (!newPr && s.sessionPr) {
      for (const [k, p] of Object.entries(s.published)) {
        if (p.pr === s.sessionPr.number && keyState(s, k)) keys.add(k);
      }
    }
    return keys;
  }

  // --------------------------------------------------------------- sorting
  // Three-click cycles. Sorting is view-only: it never touches verdicts,
  // counts, or what a publish contains.
  function compareByName(a, b, dir) {
    const n = (a.name || '').localeCompare(b.name || '');
    return dir === 'za' ? -n : n;
  }

  function compareByColumn(s, a, b, dbkey, dir) {
    const na = cellNeed(s, a, dbkey), nb = cellNeed(s, b, dbkey);
    if (na !== nb) return dir === 'mapped' ? nb - na : na - nb;
    return compareByName(a, b, 'az');          // stable tiebreak
  }

  const api = {
    NO_TERM, NEED,
    idKey, absentKey, keyState, isPending,
    predFor, preJudgmentId, idState, cellEntries, isAbsent, cellState,
    isOpenState, isMissing, cellNeed, isComplete,
    addedBy, isOwnAddition, mayConfirm, needsSecondReview, awaitsSecondReviewer,
    publishKeys, compareByName, compareByColumn,
  };

  root.ReviewModel = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
