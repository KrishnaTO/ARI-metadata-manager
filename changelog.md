# Changelog

## feat-ref-curate-disease-curator
- Added the `/ref-curate` disease curator: a disease-first companion to the ref-edits matrix that curates one disease's cross-references at a time (per-database cards, source preview, prior judgments, exact-match predictions, new-subtype form).
- Reuses the existing APIs and writes the same SSSOM + equivalency files; deep-links from the main app's field editor via `ref-curate/#<disease-iri>`, and cross-links with the matrix page.

## feat-ref-edits-orphanet-omim
- Added Orphanet and OMIM as cross-reference sources on the ref-edits page.
- Exposed the new sources through the API, ontology service, SSSOM mapping layer, and ontology build/import paths.
- Ordered the ref-edits database columns to match the requested review sequence.
