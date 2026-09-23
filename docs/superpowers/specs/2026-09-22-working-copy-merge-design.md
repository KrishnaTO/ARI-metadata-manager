# Working-copy merge: keeping curators current and letting them choose

Date: 2026-09-22 · Status: design approved in chat, pending spec review

## Problem

Submitting from the cross-reference review page was refused with *"someone else
has edited it since your copy was made"* and the only offer was to drop the
curator's work on the disease. Investigation:

- A curator's working copy (`.user-data/<login>.owl`) is snapshotted once and
  never catches up with the source branch. A successful publish does not refresh
  it, nor does the PR merging; only *Fetch changes now* (drop everything) or
  *Take their version* (drop the disease) do.
- `merge_service.upstream_edits` refuses a publish when the branch carries an
  `ARI_ChangeLog` entry the working copy lacks. The check is correct: the
  observed collision was ARI PR #84 (Claude's synonym review, merged
  2026-09-07), which edited Cutaneous lupus erythematosus directly on `main`.
  Every curator with a copy older than that collides on each disease #84 touched.
- The dialog shows disease names only, not what changed or who changed it.
- *Take their version* drops the curator's review verdicts on the disease, but
  verdicts are held in the review session and applied to the ontology at publish
  time, after the collision check — they never needed dropping.

Replacing the working copy with the branch whenever nothing is pending is not a
fix: submitted-but-unmerged work lives only in the working copy and an open PR,
so it would vanish from the curator's view until the merge.

## Goals

1. The dialog says what changed upstream and who changed it.
2. Working copies stay current with the source branch without losing the
   curator's unpublished or unmerged work.
3. Where both sides changed the same thing, the curator chooses, per field.

## Design

### Ancestor (starting version)

Each working copy gains an ancestor: `.user-data/<login>.base.owl` plus
`.user-data/<login>.base.json` holding `{"sha": <source-branch commit>}`.

- Written wherever the working copy is (re)created: `workspace.user_service(create=True)`
  (a copy of `config.ONTOLOGY_FILE`; sha unknown → `null`, so the first sync
  always runs) and `settings._fetch_branch` (the fetched bytes and branch head sha).
- Removed with the working copy in `workspace._reset_user` and by the sweep.
- After a merge, the merged diseases in the ancestor are replaced by the
  branch's version (`graft_diseases(theirs, ancestor, iris)`), and the sha
  advances only when every disease merged.
- Copies that predate this change have no ancestor; see *No ancestor* below.

### Three-way merge (`merge_service`)

`merge_disease(base, mine, theirs, iri, choices=None) -> MergeResult` works at
the triple level over the disease and every item individual linked from it on
any of the three sides (`ITEM_LINKS`).

For each `(subject, predicate)`, take the value sets `B`, `M`, `T`:

- **Multi-valued predicates** — a named constant `MULTI_VALUED` in
  `merge_service`: `ARI_ChangeLog`, synonyms, clinical subtypes, every
  cross-reference id property (from `xref_registry`), `rdf:type`, `rdfs:seeAlso`,
  and the item links. Result is `(B ∩ M ∩ T) ∪ (M − B) ∪ (T − B)`. This cannot
  conflict: a value cannot be both added by one side and removed by the other.
- **Every other predicate is single-valued.** If only one side differs from `B`,
  that side wins. If both differ from `B` and from each other, it is a conflict.
- **Items**: an item present in `B`, absent on one side (deleted) and changed on
  the other is a conflict — "they removed it / you edited it". Deleted on one
  side and unchanged on the other: deleted.

A conflict is `{key, label, subject, mine, theirs, base}` where `key` is
`<subject iri>|<predicate iri>`, `label` is the field's display label
(`diff_service.FIELDS`, or the item category field label), and the values are
display strings. `choices` maps conflict keys to `"mine"` or `"theirs"`; a
merge with any unanswered conflict returns the conflicts and writes nothing.

The result is written into the working copy with the existing `_graft`
primitives, per subject.

**Diseases the curator has not touched** (`workspace.touched(login)` does not
contain them) skip the merge: the branch's version is grafted over the working
copy. They hold no work of the curator's.

**No ancestor**: a touched disease in a copy with no ancestor is merged with
`B = ∅` for single-valued predicates (every differing value is a conflict) and
`B = M ∩ T` for multi-valued ones (both sides' extra values are kept).

### When it runs

- **`POST /api/v2/sync`** — called at boot by both pages when signed in. Reads
  the source branch head sha (a new `github_service.branch_sha` helper); if it equals the ancestor's sha, returns
  `{up_to_date: true}`. Otherwise fetches the branch ontology, merges every
  disease with no conflict, leaves conflicting diseases untouched, and returns
  `{merged: [iri...], conflicts: [...]}`. Pages show a banner for conflicts
  that opens the choice screen.
- **Publish** — before `upstream_edits`, merge the publish scope against the
  baseline already fetched. Any conflict → **409** with `conflicts`. Auto-merged
  diseases are written to the working copy and the publish continues, so
  `upstream_edits` finds nothing unseen.

### Conflict payload

```json
{"conflicts": [{"iri": "...", "name": "Cutaneous lupus erythematosus",
                "upstream_log": ["2026-09-07 | Claude | Synonym review: ..."],
                "fields": [{"key": "<subject>|<predicate>", "label": "Definition",
                            "subject": "Cutaneous lupus erythematosus",
                            "mine": "...", "theirs": "...", "base": "..."}]}]}
```

`upstream_log` is the branch's changelog entries absent from the working copy
(what `upstream_edits` already computes).

### `POST /api/v2/resolve`

Body `{"choices": {<iri>: {<conflict key>: "mine" | "theirs"}}}`. Re-fetches
the branch, re-runs the merge with the choices for the named diseases, and:

- **400** if any conflict for a named disease is unanswered;
- **409** with a fresh `conflicts` list if the branch moved and produced
  conflicts the curator has not answered;
- otherwise writes the merged diseases into the working copy, advances their
  ancestor, and returns `{merged: [iri...]}`.

The working copy is snapshotted before writing and restored if any graft
refuses. Review verdicts are never dropped. `POST /api/v2/discard` and its
callers are removed — choosing *theirs* throughout is the same operation.

### Choice screen (`static/js/merge-dialog.js`)

Shared by `static/js/github.js` (editor) and `static/ref-edits/ref-edits.js`,
built on `ui-dialog.js`. One section per disease: the upstream changelog lines,
then a row per conflict with the field label, *Yours* and *Theirs* values and a
two-way choice. *Keep all mine* / *Keep all theirs* per disease. *Apply* is
disabled until every row is answered. Returns the choices, or `null` on cancel.

After a successful resolve: from a publish, the publish is retried; from the
sync banner, the page reloads.

### Errors

- Sync cannot reach GitHub → banner "Couldn't check <branch> for updates"; the
  working copy is untouched.
- An anonymous node under a merged subject refuses the merge (as `_graft` does
  today); the snapshot is restored and the error surfaces.

## Testing

- `tests/test_merge_service.py`: single-valued changed on one side / both sides;
  multi-valued adds and removes on each side; changelog union; item deleted vs
  edited; untouched disease takes theirs; no-ancestor behaviour; unanswered
  conflicts write nothing.
- Route tests: sync up-to-date / merged / conflicts; resolve partial (400),
  branch moved (409), success; publish returns 409 with field-level conflicts.
- Live check on a local instance with stubbed GitHub reproducing the lupus
  case: PR #84's synonym withdrawal merges in and the curator's cross-reference
  verdicts survive a submit.

## Delivery

One branch and PR (`claude/ari-edits-submit-conflict-f23669`); README and
changelog updated; app version bumped.

## Out of scope

- Merging the SSSOM/equivalency mapping files (already appended at publish).
- Re-scoping `create_release`'s per-disease changelog stamping.
