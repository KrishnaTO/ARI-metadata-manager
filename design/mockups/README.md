# Cross-reference curation matrix — UI mockups

Design exploration for the cross-reference review page (`ref-edits`). These are
standalone mockups, not production code — see "How to view" below before
assuming anything is wired up.

## Files

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

## How to view

These are self-contained mockups — no build step, nothing to install. Open
any `.html` file directly in a browser.

They render via a small template runtime (`support.js`, included in this
folder) rather than plain static HTML — all three `.dc.html` files load it
from the same relative path, so keep `support.js` alongside them if you move
or copy these files. `support.js` in turn loads React, ReactDOM, and Babel
from `unpkg.com`, so an internet connection is needed the first time a page
renders.

## Status

Design exploration only. **Not wired to the FastAPI app** — no calls to
`app/` routes or `static/` assets, and none of the data shown here is real.
Nothing under `static/` or `app/` was touched to produce these mockups.
