# Architecture

How the ARI Metadata Manager works, part by part. For setup, see the
[README](../README.md); for the endpoint reference, [API.md](API.md); for known
performance costs, [EFFICIENCY.md](EFFICIENCY.md). History (why something is the
way it is) lives in [`changelog.md`](../changelog.md) and in the docstrings.

## Context

```
                         KrishnaTO/ARI (data repo)
          ontologies/*.owl · mappings/*.tsv · data/4-reports/*
               ▲  pull requests, authored by          │  raw file reads
               │  the signed-in curator               ▼  (contents API)
┌──────────────┴──────────────────────────────────────────────────────┐
│ ARI Metadata Manager (this repo) — one uvicorn process, :8001       │
│                                                                     │
│  routes/ ──► services ──► owlready2 Worlds                          │
│                             · shared BASE (ontologies/ari_disease.owl) │
│                             · one per curator (.user-data/<login>)  │
│                       ──► file-backed stores (provenance/, …)       │
│                       ──► reference indexes (data/2-databases/)     │
│                       ──► live terminology services (read-only)     │
└──────────────▲──────────────────────────────────────────────────────┘
               │  nginx strips /ari-editor, rate-limits, TLS via Cloudflare
           Browser: /, /ref-edits/, /ref-edits/reviewer/, /ref-edits/synonyms/, /stats/
```

The ontology belongs to [`KrishnaTO/ARI`](https://github.com/KrishnaTO/ARI). This
app is an editor for it: curators read a copy, edit their own working copy, and
send changes back as pull requests under their own GitHub identity. Nothing the
app does reaches `KrishnaTO/ARI` except through a pull request.

## Process model

- **One process, one event loop.** uvicorn runs a single worker. In-memory state
  (sessions, loaded ontologies, caches) is process-local, and several pieces
  depend on there being exactly one process: the per-login `asyncio.Lock` in
  `workspace.one_at_a_time`, the `threading.Lock` in `id_allocator` and the
  JSON stores. Do not run more than one worker without first moving those to
  OS-level locks or a database.
- **Async routes, synchronous work.** Most handlers are `async def` and call
  owlready2 and the index code synchronously, so a slow call holds the loop.
  Network calls (`httpx`) are awaited. The PR-reviewer routes and the concept
  lookup are plain `def`, so FastAPI runs them in its thread pool. Predictions
  and the enrichment preview load the reference indexes with
  `asyncio.to_thread` first (it reads no ontology), so a reload after
  `data/2-databases` changes doesn't hold the loop; owlready2 calls stay on it. See
  [EFFICIENCY.md §3](EFFICIENCY.md#3-cpu-bound-work-runs-on-the-event-loop).
- **Background sweep.** `main.lifespan` starts a loop that, every six hours,
  retires idle working copies (`workspace._sweep_user_data`) and expired
  sessions (`sessions._sweep_sessions`).
- **Settings are read once at startup** in `config.py`. Other modules refer to
  them as `config.NAME`, never by importing the value, so tests can patch one
  place.

## Backend modules

| Module | Responsibility |
| --- | --- |
| `main.py` | App assembly: session middleware, security headers and CSP, cache policy, `/healthz`, error handlers, routers, the static mount (last) |
| `config.py` | Every setting: `.env` loading, paths, GitHub config, the git-derived `APP_VERSION`, the mtime-derived asset token |
| `sessions.py` | Server-side session store (`.sessions.json`, mode 0600); who the caller is; whether they may act for others |
| `workspace.py` | Per-curator working copies: the shared `base()`, LRU of loaded Worlds, the write gate `service_for`, ancestors, touched set, branch state, review session, sweep, expiry, per-login lock |
| `stores.py` | Process-wide ledgers: assignments, id authorship, xref removals |
| `atomic_store.py` | Atomic writes (temp file + `os.replace`); JSON reads that refuse to treat a corrupt file as empty (`StoreCorrupt`) |
| `errors.py` | `NotFound` (→ 404) and `Invalid` (→ 400) |
| `ontology_service.py` | The owlready2 layer: every read view (trees, detail, search, review rows), every write (fields, items, xrefs, creation, releases, enrichment, review logging) and atomic saves |
| `schema.py` | The editable item categories (symptoms, treatments, immune components, …), which drive both the forms and the writes |
| `xref_registry.py` | The single list of cross-reference databases: labels, CURIE prefixes, annotation suffixes, link-out and search templates, id shape patterns, id normalisation |
| `id_allocator.py` | One server-side counter for ARI numbers (`provenance/ari-id-counter.json`), so two curators never mint the same id |
| `id_provenance.py` | Who added each cross-reference id (`provenance/id-authors.json`); first author wins |
| `xref_removals.py` | Ids edited off a record, parked as pending negative judgments (`provenance/xref-removals.json`) |
| `merge_service.py` | Three-way merge of a disease and its items between ancestor, working copy and branch; grafting diseases from one ontology into another |
| `owl_splice.py` | Lays a rewritten OWL file out in the source file's entity order, so a commit's diff shows only the change |
| `diff_service.py` | Per-field change list, PR title and PR body summary |
| `sssom_service.py` | Builds and accumulates the SSSOM and equivalencies files; reads them back as the current judgment per cell |
| `predict_service.py` | Lexical indexes over `data/2-databases`, candidate prediction and scoring, predicted-SSSOM I/O |
| `enrich_service.py` | Synonyms and clinical subtypes proposed from confirmed cross-references |
| `concept_service.py` | What the local indexes know about one target id (label, synonyms, definition, parents), with provenance |
| `stats_service.py` | The curation dashboard's numbers, projected from stores already on disk |
| `github_service.py` | OAuth, identity, branch and file reads, and `publish_file` (fork if needed, one commit via the git data API, open or update the PR) |
| `export_service.py` | The `1_Core_ARI_Diseases.xlsx` export, optionally marked against a baseline |
| `feedback_service.py` | Per-term feedback (`feedback/feedback.json` + an audit log), archived at each release |
| `assignment_service.py` | Per-curator review queues (`assignments/assignments.json` + an audit log) |
| `synonym_review.py` | Parses report 9 (`9_Synonym_Review.tsv`) |
| `synonym_review_store.py` | Curators' marks and notes on report 9 (`synonym-review/curation.json`) |
| `pr_review/github.py` | GitHub reads for the PR reviewer, using the reviewer's token; posts reviews |
| `pr_review/terminology.py` | Live lookups at each database's own source (tx.fhir.org, OHDSI, EBI OLS, NLM MeSH, NCBI MedGen), cached for the process |
| `pr_review/compare.py` | The PR comparison matrix: diffs the mapping files, compares each row's ARI disease with the target term |
| `pr_review/store.py` | Reviewers' shared marks and notes per PR (`pr-review/<n>.json`) |
| `routes/*.py` | Endpoints, grouped by the page they serve (see [API.md](API.md)) |

## Frontend

Vanilla JavaScript with no build step. Scripts are classic (shared global scope)
on the main page and IIFEs on the others. Every asset URL carries `?v=__ASSETV__`,
which `routes/pages.py` replaces on each render with a token derived from the
newest mtime under `static/`. The HTML is always revalidated; versioned JS/CSS
is cached for a year (`main.cache_policy`). The CSP allows only same-origin
scripts, so third-party libraries are vendored (`static/vendor/`) and pre-paint
preferences live in small `boot.js` files rather than inline scripts.

| Page | Files | Who it is for |
| --- | --- | --- |
| `/` — the record view | `static/index.html`, `static/js/*`, `static/css/styles.css` | Everyone reads; signed-in curators edit |
| `/ref-edits/` — cross-reference review matrix | `static/ref-edits/index.html`, `ref-edits.js`, `review-model.js`, `boot.js` | Signed-in curators judge; anyone reads |
| `/ref-edits/reviewer/` — mapping-PR reviewer | `static/ref-edits/reviewer/` | Administrators (`ASSIGN_ADMINS`) |
| `/ref-edits/synonyms/` — synonym vs subtype review | `static/ref-edits/synonyms/` | Signed-in curators mark rows; anyone reads |
| `/stats/` — curation dashboard | `static/stats/` | Anyone (not linked from the other pages) |

Main-page scripts, in load order: `words.js` (interface vocabulary), `help.js`
(help sheet), `ui-dialog.js` (inline dialogs, live region, field errors),
`core.js` (state, constants, `api()`, `BASE_PATH`, link builders), `trees.js`
(index rail and search), `detail.js` (record view), `panels.js` (deep dives),
`graph.js` (D3 pathophysiology graph), `editor.js` (edit mode, item CRUD,
releases), `resize.js` (column gutters), `feedback.js`, `github.js` (sign-in,
publish, sync), `settings.js` (branch, export), `symptoms.js` (search by
symptoms), `main.js` (bootstrap). `words.js`, `help.js` and `ui-dialog.js` are
shared with the review page.

The review page's policy (verdict state machine, separation-of-duties gate,
missing-first ordering, publish payload) is in pure functions in
`static/ref-edits/review-model.js`, tested by `tests/review-model.test.js`.

## Data model

The ontology is one RDF/XML OWL file, compatible with Protégé. The app relies on:

- **Classes:** `AutoimmuneDisease` (each disease is an individual of it, under
  `https://diseases.autoimmuneregistry.org/disease/ARI_00NNNNN`),
  `MulticellularAnatomicalStructure` (the UBERON tissue hierarchy root), and one
  class per item category in `schema.py` (`Symptom`, `Treatment`, `Cytokine`, …).
- **Annotations on a disease:** `rdfs:label`, `rdfs:comment` (definition),
  `ARI_ID`, `ARI_Synonym`, `ARI_ClinicalSubtype` (`Name - description`, optionally
  `| <iri>` linking an existing disease), one `ARI_<DB>` per cross-reference
  database (from `xref_registry`), `ARI_DefSource`, `ARI_Pubmed`, `ARI_RefLink`,
  `ARI_Author`, `ARI_AuthorDate`, `ARI_Version`, `ARI_Obsolete`, `ARI_IsGrouping`,
  `ARI_PrevalenceDesc`, `ARI_SurveyCode`, `ARI_ChangeLog`
  (`<utc> | <editor> | <message>`), `ARI_EnrichmentSource`.
- **Data properties:** `diseaseCategory`, `evidenceQuality`, `prevalenceValue`
  (estimated US cases), `demographicBias` (sex / demographic notes), `ageRange`.
  A data property must be declared in the ontology; writing an undeclared one is
  rejected with a per-field reason, never dropped silently.
- **Prevalence and incidence figures** are items of the `prevalence` category
  (`EpidemiologyProfile`, linked by `hasEpidemiologyProfile`, the data model's
  names), one per measure, region, year and sex: `epiMeasure` (Prevalence or
  Incidence), `epiRegion` (US or Worldwide), `epiYear`, `epiSex` (All, Female or
  Male), `epiRatePer100k` and `sourcePMID`. The label is built from the first four
  (`schema.py` `label_from`). `scripts/migrate_epidemiology.py` moved the former
  flat `prevalence*`/`incidence*` rate, year and source properties into figures.
- **Object properties:** `hasParentDisease`, `hasParentCategory`, `targetsTissue`,
  and one link per item category (`hasSymptom`, `hasTreatment`,
  `involvesCytokine`, …).

## Working copies and isolation

- **The shared base.** `workspace.base()` serves `ontologies/ari_disease.owl`
  (with the modules it imports from the same folder), and reloads it whenever the
  disease file's path or the newest mtime of any `*.owl` in the folder changes.
  `deploy/update-ontology.sh` replaces the changed files atomically, so anonymous
  readers see the data repo's `main` within one timer tick, without a restart.
- **A curator's own copy.** The first write by a signed-in curator copies the
  base to `.user-data/<login>.owl`; that curator's reads and writes go there from
  then on. Every write endpoint resolves its ontology through
  `service_for(request, write=True)`, the single write gate: where GitHub sign-in
  is configured it demands a session; with GitHub off (local use) it writes to the
  base.
- **Memory bound.** At most `MAX_LOADED_WORLDS` (default 8) copies are loaded at
  once, evicted least-recently-used. The files are durable, so an evicted copy
  reloads on its next use.
- **Beside each copy** in `.user-data/`: `<login>.touched.json` (diseases edited
  since the last publish — what "this curator's work" means to a publish),
  `<login>.branch.json` (source branch and PR target),
  `<login>.refsession.json` (review-page verdicts and PR pointer),
  `<login>.publishes.json` (replay guard), and `ancestor/<login>.owl` + `.json`
  (the version the copy started from, its commit, and a revision token).
- **Retirement.** Copies idle longer than `USER_DATA_TTL_DAYS` (default 14) are
  deleted if identical to the base and **archived** to `.user-data/archive/` if
  they hold unpublished work. `GET /api/v2/settings` reports days left.

## Editing

- `update_disease` applies a dict of field changes using the `EDITABLE` map
  (label, comment, multi-valued annotations, single annotations, typed data
  properties). A value that cannot be stored is returned in `rejected` with a
  reason, and the form shows it on the field.
- `apply_xref_op` changes one cross-reference id (`add` / `replace` / `remove`)
  against what is on file at the moment of the write, so two windows editing the
  same cell cannot erase each other. `replace` refuses if the old id is gone.
- Cross-reference ids are canonicalised on the way in (`xref_registry.normalize_id`):
  a pasted `MONDO:0012345` is stored as `0012345`.
- Every disease write appends a line to that disease's `ARI_ChangeLog` and saves
  the file atomically. Item writes append to the parent disease's changelog.
- Around each field or xref edit the route snapshots the disease's ids and
  records the difference: new ids are credited to the curator
  (`id_provenance`), removed well-formed ids are parked as pending negative
  judgments (`xref_removals`).
- New diseases get the next number from the shared `IdAllocator`, never below
  the highest number in the ontology, so copies can't collide. A disease created
  as a subtype names its parent in both changelogs.
- **Releases** (`POST /api/v2/releases`, administrators): stamp `ARI_Version` and
  a changelog line on every disease, copy the file to
  `releases/ari_t1d_v<version>_<ts>.owl`, append to `releases/releases.json`,
  and archive non-kept feedback.

## Sync and three-way merge

A working copy can live for days while the branch moves on. `POST /api/v2/sync`
runs when the page loads and whenever the tab becomes visible again:

1. Read the source branch's head sha. If it equals the ancestor's sha, there is
   nothing to do.
2. Download the branch's ontology at that sha and run `merge_service.merge_into`
   for every disease against the ancestor:
   - **list fields** (synonyms, subtypes, ids, changelog, item links) merge value
     by value: `(B ∩ M ∩ T) ∪ (M − B) ∪ (T − B)`;
   - a **single-valued field** takes whichever side changed it;
   - a field **both sides changed differently** is a conflict, and so is one side
     deleting a record the other edited;
   - an item's link follows the item, so a deleted item takes its link with it.
3. Diseases that merge cleanly are written into the working copy and their
   ancestor advances to the branch's version. A disease with a conflict is left
   untouched on both sides.
4. Conflicts come back with the sha they were computed at. The curator chooses
   *mine* or *theirs* per field (or for everything), and `POST /api/v2/resolve`
   re-runs the merge **at that sha** with the choices.

Each merge that rewrites the copy issues a new revision token. A window that
sees a token different from the one it loaded reloads itself, so a background
tab cannot write stale lists back over a merge.

When a source branch is deleted (its PR merged), sync answers 409 with
`missing_branch`. `POST /api/v2/source/follow-base` switches the curator to the
base branch and keeps the working copy; the next sync merges three-way as usual.

## Publishing

`POST /api/v2/publish` holds the curator's per-login lock for its whole run.
Steps, in order:

1. **Replay guard.** A `request_id` already completed returns the first result
   (`repeated: true`) instead of committing twice.
2. **Two-person rule.** A confirmed id the curator added themselves is refused.
3. **Scope.** The diseases this publish may speak for are the touched set, less
   any the curator left out in the publish dialog (`exclude`), plus the diseases
   reviewed in this request. Nothing else in the commit comes from the working
   copy, and the diseases left out stay touched for a later pull request. An
   empty scope is a 400.
4. **Rebase.** Read the source branch at its current sha and merge it into the
   working copy for the scope. Any conflict is a 409 with the conflicts and sha.
5. **Mapping files** (only if the request carries verdicts). The curator's own
   parked removals for the scope join `flagged`, each given its disease's ARI id
   and label. Read the branch's SSSOM and equivalencies files and accumulate this
   request's confirmed, flagged and absent judgments (`sssom_service.build`). A
   judgment with no ARI id is a 400. This runs before anything is written, so a
   refusal leaves the working copy as it was.
6. **Review write-back** (only if the request carries verdicts): log each
   verdict in the disease changelogs, store confirmed ids on the record, remove
   flagged ids (from every property holding that vocabulary's ids, so a SNOMED
   code leaves `ARI_DXCODE` too), and (if `apply_enrichment`) fold in selected
   synonyms and subtypes. The working copy is snapshotted first and restored if
   anything later fails.
7. **Build the commit.** Summarise the scoped changes (`diff_service`), graft the
   scoped diseases from the working copy onto the downloaded branch ontology, and
   splice the result back into the branch's own file order (`owl_splice`), so the
   diff shows only the curator's change.
8. **Commit and PR** (`github_service.publish_file`): push to the upstream repo if
   the curator can, otherwise to their fork (created on first use, with
   `maintainer_can_modify`). Reuse the session's branch if it still exists,
   merging the base into it first; otherwise create
   `edit/<login>/<slug>-<timestamp>`. All files go in one commit via the git data
   API. Open the PR, or update the open one's title. Apply the `edit term` and
   `sssom` labels where permitted.
9. **Clean up.** Remember the result under `request_id`, clear the parked
   removals that went out, and drop the published diseases from the touched set.

## Cross-reference review

The review page (`/ref-edits/`) is a diseases × databases matrix. Each cell's
state comes from four sources: the ids on file (`/xrefs`), the judgments already
published (`/mappings`, read from the source branch when signed in, otherwise
from the local `mappings/` files), predictions (`/predictions`) and this
curator's session (`/ref-session`).

- **Verdicts** are per id: confirmed, flagged, or "no term in this database"
  (an absence, recorded against SSSOM's `NoTermFound` with the database in
  `object_source`).
- **Session saves merge.** The page sends only the keys that changed (a `null`
  clears a key), so two windows reviewing different cells never overwrite each
  other. The session is dropped when the curator switches source branch.
- **Two-person rule.** Whoever added an id may not confirm it. The page withholds
  the ✓ using `/id-authors`, and the publish endpoint re-checks. Flagging and
  declaring an absence stay open to everyone.
- **Mapping files are an event log.** A reversal keeps both rows; the withdrawn
  row's `comment` is marked `Superseded by the … judgment of …`.
  `load_judgments` projects the current state, ranking not-superseded first, then
  date, then file position. `scripts/validate_mappings.py` (run in CI) checks the
  file stays valid SSSOM.
- **The compare pane** looks the candidate id up live at its source
  (`/api/v2/concept/{db}/{id}`, using `pr_review.terminology`) and merges in the
  local index's copy. When the source no longer has the code, it shows the
  snapshot (`live: false`); when the source is unreachable, it reports a 502.
- **Queues.** Each disease belongs to at most one curator's queue
  (`/api/v2/assignments`). A curator manages their own queue; changing someone
  else's needs `ASSIGN_ADMINS`.

## Prediction

`predict_service` proposes ids for blank cells from the `data/2-databases/<db>.index.tsv`
files (MONDO, DOID, NCIt, MeSH, Orphanet). Nothing is written to the ontology;
a curator confirms each one. Routes, strongest first — a weaker route runs only
when every stronger one found nothing:

| Route | How the candidate was found | Base score |
| --- | --- | ---: |
| `xref` | A reference term carrying an id already on file for this disease cross-references it | 70 |
| `label` | The disease's label is exactly a name of the term (label and synonyms are normalised: accents folded, case folded, punctuation collapsed) | 55 |
| `synonym` | Only one of the disease's synonyms matched (used only when the label matched nothing; blocklisted synonyms skipped) | 30 |
| `fuzzy` | No exact match; the term's name shares at least `FUZZY_THRESHOLD` (0.6 Jaccard) of the label's words | 10 + up to 15 |

Agreement between independent index files adds up to 20; the candidate's own
label equalling the disease's label adds 15. Scores are capped at 100 and banded
strong (≥ 80), fair (≥ 55) and weak. `scripts/eval_fuzzy.py` re-derives the
fuzzy threshold from the confirmed mappings.

**Caching.** Parsed index files are cached per process, keyed on each file's
mtime and size. Each `OntologyService` caches its predicted cells against its
ontology file's mtime, so a repeat page load is a cache hit and an edit
triggers a recompute. `main.lifespan` parses the indexes and builds their
lookups in a background thread at startup (`predict_service.warm`), so no request
pays that parse (about 2 s locally); a request that needs them mid-warm-up waits
for that one load rather than starting a second. Prediction, the compare pane
(`concept_service`) and enrichment all look ids up through each index's own id
map (`LexicalIndex.records_for`), so ids match the same way everywhere and there
is one id map per index file rather than one per feature.
Resident cost is roughly 250 MB process-wide, plus a few MB per active curator.

## Enrichment

Confirming a cross-reference asserts that the external term *is* the disease,
so `enrich_service` proposes two of its facts for the ARI record: its label and
exact synonyms as `ARI_Synonym`, and its direct `is_a` children (from
`<db>.subtypes.tsv`, OBO sources only) as `ARI_ClinicalSubtype`.

- Only confirmed mappings feed it. ICD-10 never does (`NON_CONCEPT_DBS`): a code
  is a classification bucket, not a concept.
- An id matching more than one term within one source is ignored for that
  source; an id pinning one term in each of several sources counts from all of
  them.
- Proposals are de-duplicated against existing values, filtered by the synonym
  blocklist, and a disease is never proposed as its own subtype.
- The review page previews them (`POST /api/v2/enrichment-preview`) with a
  checkbox per value. Publish applies only the selected ones, recomputed from the
  confirmed list at publish time, and records each value's source term in
  `ARI_EnrichmentSource`.

## Mapping-PR reviewer

`/ref-edits/reviewer/` is for administrators reviewing curators' PRs on the data
repo. For a PR that changes `mappings/ari.equivalencies.tsv` or
`notebook/ari-grounding/target_predictions.json`, `pr_review/compare.py` diffs
the file between the merge base and the head, reads the ARI diseases from the
PR's own ontology, and looks every target id up live (all of a PR's ids are
prefetched in parallel; UMLS CUIs in MedGen batches). Each row gets a name
match, definition overlap, cross-reference support and conflicts, flags
(narrower than the disease, named like another ARI disease, inactive,
non-standard), a score and a hint. The matrix is cached per PR until the head
commit moves. Marks and notes are shared per PR in `pr-review/<n>.json`; *Post
my notes to PR* sends the caller's unposted notes as one GitHub review, placing
each on its line when the line is in the diff and in the review body when not.
A source that fails makes the matrix fail rather than show partial data.

## Synonym vs subtype review

`/ref-edits/synonyms/` shows report 9 (`data/4-reports/9_Synonym_Review.tsv` in
the data repo), read live from `GITHUB_BASE_BRANCH`. Each load reads only the
file's git blob id; the report is downloaded and parsed again only when that
changes. Each row carries its disease's IRI for the link back to review. A header
that doesn't match the expected columns is refused. Signed-in curators mark each row
correct / incorrect / needs review and add a note; marks are shared and stored in
`synonym-review/curation.json` with a copy of the row they judge. The page never
edits the ontology.

## Curation dashboard

`/stats/` renders `GET /api/v2/stats`: per-database coverage (diseases with an
id, confirmed, unjudged, rejected, no term), per-curator activity, judgments per
ISO week, ids waiting for a second reviewer, and queue progress. It is a
projection of stores already on disk plus the mapping files; nothing new is
recorded.

## The record view (main page)

A two-column layout: a 288 px index rail (A–Z and tissue trees, search) and a
record column. The record has a header (breadcrumb, title, *Copy link*, *Edit
record*), a reading column (definition, citations and the five-step disease
story spine) and a sidebar (synonyms, target tissue, cross-reference ledger,
clinical subtypes, links; each block collapsible, remembered in
`ari_side_collapsed`). Picking a story category opens its deep dive inline below
the spine (as a drawer under 1024 px). *Edit record* is the only way in and out
of edit mode, is enabled only for a loaded record and a signed-in curator, and
every way out of the form checks for unsaved changes. The selected disease is
mirrored in the URL as `#/disease/<iri>`.

Design tokens (colour, type, spacing) live in `:root` in `static/css/styles.css`,
with a dark set under `[data-theme="dark"]`. Type is Barlow Condensed (wordmark)
and IBM Plex Sans / Mono / Serif. Icons are inline Lucide SVGs. Text size is a
user preference, and every font size is in `rem` so it scales.

## Caches

| Cache | Key | Invalidated by |
| --- | --- | --- |
| Shared base ontology | file path + mtime | `update-ontology.sh` replacing the file |
| Loaded working copies | login (LRU, `MAX_LOADED_WORLDS`) | eviction, merge, publish rollback, branch switch |
| Prediction indexes | each index file's mtime + size | rebuilding `data/2-databases` |
| Id map per index (`LexicalIndex.records_for`: prediction, compare pane, enrichment) | the index it belongs to | an index reload |
| Predicted cells | ontology file mtime + index dir | any edit to that ontology |
| Source-branch ontology (`.user-data/baselines/<sha>.owl`) | commit sha | never stale; the 8 most recently used are kept |
| Report 9, parsed (`routes/review._REPORT`) | the file's git blob id on the base branch | a commit that changes the report |
| Enrichment subtype map | identity of the index list + each `*.subtypes.tsv`'s mtime and size | an index reload or a changed subtypes file |
| Detail sidecars | sidecar mtime + size | rebuilding `data/2-databases` |
| Live terminology answers | `(db, code)` | process restart only |
| PR matrices | PR number + head sha | a new head commit |
| Static assets in browsers | `?v=` token (newest mtime under `static/`) | any change to a static file |

## Security

- **Sessions.** The GitHub token stays on the server (`.sessions.json`, created
  0600). The browser holds a signed cookie (`SESSION_SECRET`) containing only an
  opaque id. Sessions expire after `SESSION_TTL_DAYS`. `SESSION_SECRET` is
  required whenever sign-in is configured.
- **OAuth scope** is `public_repo user:email`. `ALLOWED_LOGINS` limits who can
  sign in; `ASSIGN_ADMINS` limits releases, the PR reviewer, other people's
  queues and other people's feedback.
- **Write gate.** Every ontology write goes through `service_for(write=True)`.
- **Headers.** The app sets `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy` and a CSP with `script-src 'self'`. nginx adds HSTS and rate
  limits (`/api` 20 r/s burst 40; `/publish` 6 r/min burst 3).
- **Redirects.** `next` after sign-in must be a same-origin relative path.
- **PR bodies** quote the curator's comment rather than interpolating it.

## Durability

All JSON stores and the ontology are written atomically (temp file in the same
directory, then `os.replace`). A store that exists but cannot be parsed stops
the app (`StoreCorrupt`) instead of being read as empty, except the session
store, whose loss only signs people out. The state that exists only on the
server, and how to back it up, is in [OPERATIONS.md §6](../OPERATIONS.md).
