// The pull-request title for the diseases a submission carries.
//
// The default was "Update <whichever disease is open>", which named the record
// on screen rather than the work: publish three edits and the title still named
// one of them, and a brand-new disease still read as an update. The publish
// dialog lets a curator choose which changed diseases go in, so the title is
// worked out here, from the ticked rows, as they change (issue #25).
//
// Loaded as a classic script before github.js, and by `node --test` through the
// CommonJS export at the bottom.

(function (root) {
  'use strict';

  function phrase(verb, names) {
    if (names.length === 1) return `${verb} ${names[0]}`;
    if (names.length === 2) return `${verb} ${names[0]} and ${names[1]}`;
    return `${verb} ${names.length} diseases`;
  }

  // `changes` are /api/v2/pending-changes rows: {name, is_new, ...}.
  function titleFor(changes) {
    const added = changes.filter(c => c.is_new).map(c => c.name);
    const updated = changes.filter(c => !c.is_new).map(c => c.name);
    if (!added.length && !updated.length) return 'Update ontology';
    // Only the verb drops its capital: lowercasing the whole phrase would
    // rewrite the disease names with it.
    if (added.length && updated.length) return `${phrase('Add', added)}; ${phrase('update', updated)}`;
    return added.length ? phrase('Add', added) : phrase('Update', updated);
  }

  root.PublishTitle = { titleFor };
  if (typeof module !== 'undefined' && module.exports) module.exports = root.PublishTitle;
})(typeof globalThis !== 'undefined' ? globalThis : this);
