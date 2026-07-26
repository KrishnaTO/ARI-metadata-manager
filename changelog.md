# Changelog

## feat-concept-detail
- Added `GET /api/v2/concept/{db}/{id}` — label, exact synonyms, definition and parent terms for one target-database id, so the reference-review compare pane can show the candidate concept next to the ARI disease instead of just its label. The pane (in the ref-edits side panel) mirrors the two as columns and highlights the strings they share, folded the same way `predict_service` folds names so the highlighting agrees with the matcher.
- Every field is attributed to the index it came from. Only MONDO, DOID, NCIt, MeSH and Orphanet have an index of their own; SNOMED, OMOP, ICD-10, OMIM and UMLS appear only as cross-reference columns on those. So a lookup of a SNOMED/OMOP/… id returns `direct: false` with a `via` list of the hub term(s) that cross-reference it and a plain-language `note`; the pane labels that column "via MONDO" (etc.), never as the target database's own term. When several hubs claim one id their labels are all returned, and a disagreement is flagged for the curator. A valid-but-unindexed id is a normal `found: false` 200, not a 404.
- Definitions and parent terms are stored in a **sidecar** `<db>.details.tsv` (id → definition, parents) next to each index, not in the index itself: folding them in would roughly double the index size (measured 13.9 MB → 32.2 MB) and slow every prediction load for data prediction never uses. `concept_service` loads the sidecars lazily and caches them; the prediction hot path (`<db>.index.tsv`) is byte-identical to before. A missing sidecar reports `details_available: false` with a note (a checkout that never regenerated still answers with label + synonyms + provenance); a missing id within a present sidecar means the term genuinely has none. `scripts/fetch_databases.py` now also writes the sidecars, capturing OBO `def:`/`is_a:` (parents resolved to labels in a second pass, OBO qualifiers stripped), MeSH scope notes, and Orphanet definitions.
- Regenerated and committed the five detail sidecars (~18 MB total, loaded on demand); the prediction indexes are unchanged. Prediction behaviour, confidence rules and SSSOM output are unchanged — proved by a snapshot test over `predict_matches` (adding a sidecar does not alter predictions).
- Kept the lookup's memory cost proportional to use, for the small hosted instance (see `DEPLOY.md`): only the sidecar of the database actually being looked up is read (a DOID lookup holds ~8 MB resident, not the ~52 MB all five would), and the reverse id-index stores `(source, record)` tuples rather than a dict per entry (~49 MB instead of ~77 MB). Together that trims the lookup from ~129 MB to ~57 MB, all of it lazy — nothing loads until a curator opens the compare pane, so app startup is unaffected.

## feat-review-queue-api
- Added a per-curator review-queue API backing the redesigned ref-edits page: each curator works their own assigned queue of diseases one at a time instead of the full 224×10 matrix. `GET /api/v2/queue` returns the curator's assigned diseases with per-disease progress and a per-database coverage strip; `GET /api/v2/queue/{iri}` returns one disease as the review panel renders it — one entry per review database, each carrying a `status` (decided / confirmed / flagged / predicted / unreviewed / missing) the UI colours off.
- Added assignment management (`GET/POST/DELETE /api/v2/assignments`, `POST /api/v2/assignments/done`), gated by an optional `ASSIGN_ADMINS` allow-list (empty = anyone signed in).
- Decisions now autosave: every curator click posts to `POST /api/v2/decisions` (keyed per disease/database/id, so re-clicking overwrites rather than duplicating), so a reload or browser crash never loses work. `DELETE /api/v2/decisions/{id}` undoes one; `GET /api/v2/decisions` and `GET /api/v2/review-summary` show the unpublished set and the exact payload publish will send.
- Publishing falls back to the curator's autosaved decisions when the client sends none, renders them into the existing `{ari_id, iri, name, db, ids}` publish/SSSOM payload unchanged, and clears them only after the PR call succeeds — a failed publish leaves the work intact. The existing `GET /api/v2/mappings` body was lifted into a shared `_mapping_judgments()` helper with a byte-identical response.
- Added `assignments/` to `.gitignore` (runtime server-side state, same pattern as `.user-data/`).

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
