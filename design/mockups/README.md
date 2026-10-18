# UI mockups

Design exploration for the app's two main pages: the cross-reference review
page (`ref-edits`) and the disease editor (`/`). These are standalone mockups,
not production code — see "How to view" below before assuming anything is
wired up.

## Cross-reference curation matrix

- **`ref-edits-current.dc.html`** — recreation of today's live `ref-edits`
  page, for side-by-side comparison against the new design directions.

- **`curation-matrix-concepts.dc.html`** — design options, scrollable in one
  page:
  - **1a** — banded matrix: same grid as today, distinct row bands, no
    horizontal scroll.
  - **1d** — 1a in dark mode.
  - **1c** — expanding matrix: all ten databases visible as columns; clicking
    a disease expands its review strip in place instead of navigating away.
  - **1e** — 1c in dark mode.
  - **1f** — 1c at compact row density (~24 diseases per screen instead of 9).
  - **1g** — side panel variant: selecting a database cell opens a detail
    panel, covering both previewable and non-previewable sources.

- **`curation-matrix-1c-prototype.dc.html`** — clickable prototype of the 1c
  direction. Supports: compact/comfortable density toggle, light/dark theme
  toggle, expanding a disease row, confirming/rejecting individual
  cross-reference ids inline, opening the side detail panel, and a
  confirm/reject/skip flow within it. State is in-memory only (nothing
  persists on reload).

## Editor redesign (`editor-redesign/`)

A redesign of the disease editor that keeps every major feature and places
each by how often a curator needs it. Built on the ARI Catalog design system
(`editor-redesign/ds/ari-catalog/tokens.css`). The record shown is Type 1
diabetes mellitus as it stands in KrishnaTO/ARI `main`; the other names, ids
and counts are real too. The edits, unsaved changes and submission shown are
examples.

- **`Main.dc.html`** — the record, read mode. The top bar and the
  disease-name bar stay pinned; only the record scrolls. A section list on the
  left (Overview, then Clinical picture, Causes, Mechanism, Record) replaces
  the story spine and stays on screen, so changing section never means
  scrolling back up. Overview gathers the definition, synonyms, target tissue,
  subtypes, all nine cross-references and a preview of every section.
  Clickable: every section opens, with previous and next at the end.
- **`Edit.dc.html`** — the same record being edited in place. Fields become
  inputs where they are read, rows edit inline, changed sections carry a dot,
  and a bar at the foot lists unsaved changes with Discard and Save.
  Clickable: Overview and Symptoms.
- **`Search.dc.html`** — one search for names, synonyms, codes and symptoms,
  grouped by kind. Symptoms are de-duplicated and list the diseases that have
  them; “Match several symptoms at once” replaces the separate symptom modal.
- **`Submit.dc.html`** — the changes tray behind “Your changes”: each changed
  disease with a tick to include it and a pencil that opens it in edit mode,
  then a title and a note for reviewers.
- **`Phone.dc.html`** — the record at 390px, with sections as a strip of
  chips.

Start at `editor-redesign/Main.dc.html`; links between the files work in the
browser.

## How to view

These are self-contained mockups — no build step, nothing to install. Open
any `.html` file directly in a browser.

They render via a small template runtime (`support.js`, included in this
folder) rather than plain static HTML. The matrix files load it from the same
folder and the editor-redesign files from `../support.js`, so keep the folder
layout if you move or copy them. `support.js` in turn loads React, ReactDOM,
and Babel from `unpkg.com`, so an internet connection is needed the first time
a page renders.

## Status

Design exploration only. **Not wired to the FastAPI app** — no calls to
`app/` routes or `static/` assets. Nothing under `static/` or `app/` was
touched to produce these mockups.
