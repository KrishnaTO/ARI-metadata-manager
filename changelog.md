# Changelog

## feat-cross-ref-manager-persistence
- The cross-reference manager (ref-edits matrix + ref-curate disease view) now saves a signed-in user's in-progress review to the server: their correct/needs-change verdicts, edited-id markers and the pull-request pointer persist per user, so a page reload resumes the work instead of losing it. State is saved automatically (debounced) on every verdict/edit and stored beside the user's working ontology copy; it is dropped when they switch source branch or their copy is swept.
- Publishing now offers a choice once a PR exists: the primary button commits to the current PR, and a new "New PR" button opens a fresh pull request instead. The PR pointer is restored on reload so "Publish to PR #N" survives a refresh.
- Added `.user-data/` and `.sessions.json` to `.gitignore` (runtime server-side state).

## fix-ref-edits-sticky-column-overlay
- Fixed the sticky "Disease" column on the cross-reference review page bleeding through / being overlaid by the semi-transparent predicted (yellow) data cells when scrolling horizontally (issue #54).
- Gave the sticky column, sticky header row, and top-left corner cell an explicit z-index stacking order (corner > header > disease column > data cells); WebKit paints a `z-index:auto` sticky cell under later cells, so the explicit order is required.

## feat-ref-edits-discard-prediction
- Added a discard option for predicted cross-references on the review page (issue #52): a ✕ on each yellow predicted chip (and a "Discard prediction" button in the side panel) flags the prediction as wrong when there is no correct value in the target database.
- Discards reuse the existing negative-mapping path — they publish as `Not` (negative) SSSOM/equivalency entries and are suppressed from future predictions. Reversible in-session (toggle ✕/↩) before publishing.

## feat-ref-curate-disease-curator
- Added the `/ref-curate` disease curator: a disease-first companion to the ref-edits matrix that curates one disease's cross-references at a time (per-database cards, source preview, prior judgments, exact-match predictions, new-subtype form).
- Reuses the existing APIs and writes the same SSSOM + equivalency files; deep-links from the main app's field editor via `ref-curate/#<disease-iri>`, and cross-links with the matrix page.

## feat-ref-edits-orphanet-omim
- Added Orphanet and OMIM as cross-reference sources on the ref-edits page.
- Exposed the new sources through the API, ontology service, SSSOM mapping layer, and ontology build/import paths.
- Ordered the ref-edits database columns to match the requested review sequence.
