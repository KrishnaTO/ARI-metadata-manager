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
  Pathophysiology opens with a map above its steps table: the eight steps
  down the middle, coloured by category, with the genes, triggers, antigens,
  antigen-presenting cells, antibodies and immune mediators the app links to
  each step beside it. It is laid out on a fixed grid, so labels never
  overlap. Clickable: every section opens, with previous and next at the end.
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

The next three, with parts of `Search.dc.html` and `Submit.dc.html`, show the
fixes for the six high-severity issues found in testing the current editor:

- **`Search.dc.html`** — a symptom result opens its disease at the Symptoms
  section, or a list when several diseases have it, never a record of its own
  (H1). The highlighted result says Enter opens it (H3).
- **`SearchCode.dc.html`** — searching `E10` finds Type 1 diabetes mellitus
  and Fulminant type 1 diabetes by their ICD-10 code, across all nine
  cross-reference databases (H4).
- **`Leave.dc.html`** — opening another disease with unsaved changes asks
  first and names what would be lost, and the draft is kept after every
  keystroke (H2). One dialog pattern: focus stays inside, Esc keeps editing
  (H6).
- **`Umbrella.dc.html`** — an umbrella record shows the clinical data it holds,
  with a note to move it or make the record a disease (H5). The data is
  Autoimmune retinal vasculitis's: 25 items in 11 sections.
- **`Submit.dc.html`** — the tray closes with Esc (H6).

**Small laptops.** The record screens respond to the window. Under 1400px
wide (a 1366×768 or 1280×800 laptop), the disease index hides and opens over
the record from the menu button, closing on a click outside it. The top bar
also keeps icons only for Cross-references and Your changes, and drops
“Metadata manager” from the wordmark. Under 760px tall, the disease-name bar
slims down so the record keeps most of the height. On a 1366×768 laptop
(1366×657 inside the browser) the search box goes from 310px to 455px, the
name bar from 123px to 94px, and the reading area from 478px to 507px. At
1024px wide the search box goes from 4px, unusable, to 266px. Narrow the
browser window to see it.

**The remaining 25 issues** from the same testing (M1–M16, L1–L9) are fixed
too. Nine need no screen of their own, because the redesign already removes
them: M1 and M2 (the section list replaces the story spine), M3 (editing in
place), M7 (the pathophysiology map on a fixed grid), M13 (all nine databases
on the Overview), L1 (change log newest first), L2 (a legend under the index),
L3 (symptoms in search, New disease under the index), L6 (the phone record keeps
Copy link in its action row) and L7 (every page puts its content in a main
landmark under one h1). The rest:

- **`Main.dc.html`** — a disease with subtypes has a fold button whose
  `aria-expanded` follows what is on screen, and the open disease carries
  `aria-current` (M11). Clickable.
- **`NewDisease.dc.html`** — every field has a label, the missing field is
  named and linked, the close button is named, labels are in sentence case,
  tissue names are shown as stored, and icons replace the fullwidth plus signs
  (M4, L4).
- **`Symptoms.dc.html`** — Match several symptoms: both lists scroll inside the
  dialog, which never grows past the window, and chips keep sentence case (M5).
- **`Settings.dc.html`** — Settings in dark mode with sign-in off. The sign-in
  state comes first, controls that can't work are left out, every colour reads
  at 4.5:1 or better, and export has an icon instead of an emoji (M10). The
  symptom word cloud is retired: it repeated the Symptoms table and was
  unreadable in dark mode (M6). Closing returns focus to the settings button,
  as every dialog does (L5).
- **`Offline.dc.html`** — when the server doesn't answer, search and the record
  say so and offer Try again (M8).
- **`SearchResults.dc.html`** — the results page has its own address, so Back,
  Reload and Copy link work (M9).
- **`PhoneStart.dc.html`** — on a phone, Search diseases opens the search field
  and puts the cursor in it (M12).
- **`Matrix.dc.html`** — the cross-reference matrix as rows and cells a screen
  reader can follow, counts in a readable grey, gap counts taken from the ids
  on file so they hold when suggestions fail to load, and a Dashboard link in
  the header (M14, M16, L9).
- **`MatrixPhone.dc.html`** — the matrix on a phone: one disease at a time, its
  nine databases as a list, the header controls in one menu (M15). The disease
  is Chronic Lyme disease, with its real ids and verdicts.
- **`Reviewer.dc.html`** — the PR reviewer with a visible label on every filter,
  a back link that says where it goes, and a table scroller that takes keyboard
  focus (L8). The rows are from KrishnaTO/ARI PR #117; their name matches,
  definition overlaps, support and hints were worked out with the app's own
  matching against `data/2-databases`.

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
