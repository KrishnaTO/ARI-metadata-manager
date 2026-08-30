// The words the interface uses, in one place.
//
// The stated audience is domain experts — clinicians and researchers — and the
// primary verbs they met were GitHub's: Publish, PR, branch, fork, commit,
// "Open PR #123 (from your fork) ↗". A clinician does not need to know a fork
// exists, and the ↗ links dropped them into a GitHub diff of RDF/XML (issue #119).
//
// This renames at the UI layer only. Nothing in the API, the payloads, the branch
// names or the pull requests themselves changes — a maintainer reading the server
// or GitHub still sees pull requests and branches, which is what they are. To go
// back to GitHub's words, or to try different ones, change this table: it is the
// only place they are written.
//
// Loaded as a classic script by both pages, before their own scripts.
(function (root) {
  'use strict';

  const WORDS = {
    // The act of sending work for review.
    publish: 'Submit for review',
    publishing: 'Submitting…',
    publishShort: 'Submit',
    // What that produces. "Pull request" is GitHub's word for it.
    submission: 'submission',
    Submission: 'Submission',
    // Where work is read from.
    sourceBranch: 'Working from',
  };

  // "Submission 123", never "PR #123" — the number is GitHub's, the noun is ours.
  const submissionName = n => `${WORDS.Submission} ${n}`;

  // The primary action, which changes once a submission is open: the first click
  // starts one, later clicks add to it.
  const submitLabel = pr => (pr ? `Add to ${submissionName(pr.number)}` : WORDS.publish);

  root.Words = { ...WORDS, submissionName, submitLabel };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.Words;
})(typeof globalThis !== 'undefined' ? globalThis : this);
