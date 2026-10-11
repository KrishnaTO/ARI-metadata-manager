# Efficiency review

A review of the app's key code paths for avoidable work, written while the
documentation was rebuilt. Nothing here has been changed yet. Each finding says
where the cost is, how big it is, and what would remove it, so they can be
picked up one at a time.

**Baseline:** `main` at `e8c8a6c`. **Method:** every module under `app/`,
`static/` and `deploy/` was read, and the hot paths were timed against a copy of
the test fixture (`tests/fixtures/ontologies/ari_t1d.owl`, 214 diseases) and the
committed `data/2-databases` indexes. Timings come from a Windows development
machine on Python 3.14. Read them as relative sizes, not as production figures.

| Measured | Time |
| --- | ---: |
| Parse the ontology into an `OntologyService` | 78 ms |
| `get_disease_detail` (one record) | 1.2 ms |
| `get_xref_rows` (whole review grid) | 12 ms |
| `search("arth")` | 16 ms |
| `_save()` (serialise the OWL file) | 42 ms |
| `diff_service.list_changes` (publish summary / pending changes) | **697 ms** |
| `export_service.build_report` with a baseline | **588 ms** |
| `predict_service.get_indexes()`, cold | **1,504 ms** |
| `predict_xrefs()`, first call after the indexes load | **598 ms** |
| `predict_xrefs()`, cached | 0.1 ms |
| `enrichment_preview` for **one** disease | **552 ms** |
| …of which `build_id_index` + `load_subtypes` (rebuilt on every call) | 278 + 209 ms |
| `concept_service._build_reverse` (first lookup, then cached) | 376 ms |
| `world[property IRI]` lookup ×1000 | 1.7 ms |

## Summary, by expected payoff

| # | Finding | Where | Cost today | Effort |
| --- | --- | --- | --- | --- |
| 1 | The change summary and the export build every disease's full detail, twice | `diff_service._rows`, `export_service._rows_from_service` | ~0.6–0.7 s per publish, dialog open or export | Small |
| 2 | Enrichment rebuilds two whole-index structures on every call | `enrich_service.enrich_many` | ~0.5 s per preview, again on publish | Small |
| 3 | CPU-bound work runs on the event loop and blocks every other request | all `async def` routes in `app/routes/` | Everyone waits for the slowest request | Medium |
| 4 | The prediction indexes load on the first request after every restart | `predict_service.get_indexes` | ~2.1 s on the first review-page load | Small |
| 5 | The mapping refresh restarts the app for no reason | `deploy/update-mappings.sh:89` | Drops warm caches; skips the curator-deferral guard | Small (ops) |
| 6 | Three separate reverse-id indexes over the same records | `predict_service`, `concept_service`, `enrich_service` | ~100 MB resident, plus 27 MB rebuilt per enrichment | Medium |
| 7 | Independent network calls run one after another | frontend `init()`s, mapping reads, blob uploads | One round trip each, in series | Small |
| 8 | No HTTP connection reuse | `github_service`, `pr_review/terminology` | One TLS handshake per call; hundreds per PR matrix | Small |
| 9 | PR reviewer repeats GitHub reads, and one loop is quadratic | `pr_review/github.py`, `pr_review/compare.py` | N+1 calls to list PRs; 3 fetches of one file list | Small |
| 10 | Downloaded baselines are not reused | `routes/publish.py`, `merge.py`, `settings.py` | ~1.7 MB download + parse per dialog, publish, resolve, export | Medium |
| 11 | Smaller items | various | see below | Small |

Suggested order: 1 and 2 (pure code, large and certain wins), then 5 (one line,
but it is a server change), then 4, 7 and 6.

---

## 1. The change summary and the export build every disease's full detail, twice

**Where:** `app/diff_service.py:40` (`_rows`) and `app/export_service.py:88`
(`_rows_from_service`).

Both loop over `get_diseases_list()` and call `get_disease_detail()` for every
disease, on the working copy **and** on the downloaded baseline. That is 428
full record builds per call (every symptom, treatment, pathway step and immune
component of every disease). Each detail also re-scans all diseases to find its
subtypes (`ontology_service.py:468`).

`list_changes` then throws away everything outside `touched_iris`, which is
usually one to five diseases. The result is ~0.7 s of CPU for a summary of a few
records. It runs on every publish (`routes/publish.py:261`), every time the
publish dialog opens (`pending-changes`, `routes/publish.py:103`), and in every
export (`routes/settings.py:174`, ~0.6 s).

**Fix:**

- **Diff:** build details only for `touched_iris`. The "is this disease new?"
  check needs only the baseline's set of IRIs or ARI ids, which
  `get_diseases_list()` or `get_xref_rows()` already provide cheaply. The same
  goes for the "removed" pass. The cost becomes proportional to the edit (a few
  ms) instead of to the catalogue.
- **Export:** the sheet uses about 15 fields per disease, not the full detail.
  Read them directly, as `get_xref_rows()` does, and build one
  parent → children map for the subtypes column instead of scanning per record.

## 2. Enrichment rebuilds two whole-index structures on every call

**Where:** `app/enrich_service.py:235` (`enrich_many`), which calls
`build_id_index(indexes)` (`:254`) and `load_subtypes(indexes, index_dir)`
(`:251`) every time.

The lexical indexes themselves are cached (`get_indexes`), but these two
structures, derived from them, are not. `build_id_index` walks every
cross-reference of every term in all five indexes (278 ms, 39 MB peak).
`load_subtypes` builds a label map over every record and re-parses the three
`*.subtypes.tsv` files (209 ms).

`POST /api/v2/enrichment-preview` pays this on every call, even for one disease
(552 ms measured). Publishing with *Apply on publish* pays it again, because
`apply_enrichment` re-runs the preview (`ontology_service.py:1201`).

**Fix:** cache both alongside the index cache, with the same invalidation.
`concept_service._reverse_for` already shows the pattern: key on the identity of
the list `get_indexes()` returns, which changes only when the files do. After the
first call, each preview costs only `enrich()` itself (sub-millisecond per
disease).

## 3. CPU-bound work runs on the event loop

**Where:** every handler in `app/routes/` except `concept_detail_lookup` and the
PR-reviewer routes is `async def`, and calls synchronous owlready2 or index code
directly.

The app runs one uvicorn worker with one event loop (`workspace.py` relies on
this for its per-login `asyncio.Lock`). A synchronous call inside an `async def`
handler holds that loop. Nothing else is served until the call returns: no other
curator's request, no static file, not even `/healthz`. The slow cases are
findings 1, 2 and 4: 0.5–2 s each, with publish stacking several.

This also affects deploys. `deploy/update.sh` reads `/healthz` with a 5-second
timeout. If it gets no answer, it treats the app as not running, and the
`worlds_in_memory` deferral check finds nothing to read. So a sufficiently long
stall during a timer tick leads to a restart without the usual protection for
curators with unsaved work.

**Fix, in order:**

1. Shrink the blocking work (findings 1, 2 and 4). This removes most of the
   problem without touching the concurrency model.
2. Move index-only work off the loop with `asyncio.to_thread`: index loading,
   enrichment and prediction over already-built rows. It touches no owlready2
   `World`, so it is safe in a thread. Guard the cache build with a lock so two
   requests don't load the indexes at once.
3. Only then consider threading owlready2 calls. A `World` is SQLite-backed and
   is not safe for unguarded concurrent writes, so this needs a per-`World` lock
   in addition to today's per-login one. Measure before taking this on.

## 4. The prediction indexes load on the first request after every restart

**Where:** `app/predict_service.py:387` (`get_indexes`), first reached through
`GET /api/v2/predictions`.

The first review-page load after a restart parses ~85k terms from the five
index files (1.5 s). The first prediction pass then builds the fuzzy word index
and the per-index reverse maps (0.6 s). The curator who happens to open the page
first waits ~2.1 s, and because of finding 3, so does everyone else. Restarts
are frequent: every deploy, and every mapping refresh (finding 5).

**Fix:** warm the cache in `main.lifespan`. Call
`asyncio.to_thread(predict_service.get_indexes)` (and, once finding 2 is done,
the enrichment structures) as a background task at startup. Use the lock from
finding 3 so a request arriving mid-warm-up waits for that load instead of
starting a second one.

## 5. The mapping refresh restarts the app for nothing

**Where:** `deploy/update-mappings.sh:89`.

When `mappings/ari.sssom.tsv` or `ari.equivalencies.tsv` changes, the script
runs `systemctl restart ari-mm`. The app never caches those files. Anonymous
readers get them from disk on every request (`routes/review.py:51`), and
signed-in curators read them from GitHub. So the restart achieves nothing. It
throws away the warm caches (finding 4) and every in-memory working copy, and
unlike `update.sh` it does not defer while curators have work in memory.

**Fix:** remove the restart. Keep the atomic file replacement and the log line.
This is a change on the server's timer path, so it needs a deliberate deploy.

## 6. Three reverse-id indexes over the same records

**Where:**

- `LexicalIndex.records_for` (`predict_service.py:315`): one map per index file,
  keyed `(db, match_key(id))`. **51 MB** across the five indexes.
- `concept_service._build_reverse` (`concept_service.py:79`): one map over all
  indexes, keyed the same way, holding `(source, record)`. **47 MB**.
- `enrich_service.build_id_index`: keyed `(db, exact id)`, keeping only ids
  unique within their source. **27 MB**, rebuilt on every call (finding 2).

All three answer the same question: which terms carry this id? Two stay
resident for the life of the process, and the third is rebuilt per request.

**Fix:** build one map per index load,
`(db, match_key(id)) -> [(source, record)]`, and serve all three callers from it.
`records_for` becomes a filter on `source`. Enrichment's "unique within one
source" rule becomes a check at lookup time on the same list. That saves about
50 MB resident and removes finding 2's largest rebuild.

**Caveat:** enrichment keys on the exact id string today, while the other two
use `match_key` (prefix stripped, leading zeros dropped, casefolded). Unifying
makes enrichment match ids the way prediction and the compare pane already do.
That is probably the intent, but pin it with a test first.

**Related leak:** `_REVERSE_CACHE` (`concept_service.py:72`) is keyed by
`id(indexes)` and never evicts. After `fetch_databases.py` rebuilds the index
files, the old index list and its 47 MB map stay reachable until restart. Keep
one entry, not a dict.

## 7. Independent network calls run one after another

- **Review page start-up** (`static/ref-edits/ref-edits.js:2021`): `me`, then
  `sync`, then `xref-databases`, `xrefs`, `mappings`, `predictions`,
  `id-authors`, `ref-session` and `assignments`, each awaited in turn. Only
  `me` and `sync` have to come first. The other seven are independent and can go
  out together with `Promise.all`, so the page waits for the slowest one instead
  of the sum. `mappings` is itself two GitHub downloads and `predictions` may be
  a cold start, so this is the biggest wait a curator sees.
- **Main page start-up** (`static/js/main.js:5–9`): `overview`, `schema` and
  `xref-databases` run in series. They are independent.
- **Mapping files** (`routes/review.py:49–50`, `routes/publish.py:296`): the
  SSSOM and equivalencies files are downloaded one after the other. Use
  `asyncio.gather`.
- **Publishing** (`github_service.py:249`): one blob upload per file (ontology,
  SSSOM, equivalencies), in series. The uploads are independent and can be
  gathered. Separately, `POST /labels` runs on every publish to create labels
  that almost always exist already (`:303`). That is one wasted call per label.
- **PR matrix** (`pr_review/compare.py:400–412`): five to seven `file_at` reads
  in series. They can run in parallel (the reviewer runs in the thread pool, so
  use a small executor).

## 8. No HTTP connection reuse

- `github_service` opens a fresh `httpx.AsyncClient` in every helper
  (`:106, :122, :178, :325, :344, :365, :384`). That means a new TCP and TLS
  handshake to `api.github.com` for each step of a sync, a publish or a settings
  read. Instead, use one module-level client opened and closed in `lifespan`,
  passing the token per request in headers.
- `pr_review/terminology._get` (`:94`) calls the module-level `httpx.get`, which
  opens a new connection per lookup. A PR matrix makes a few hundred lookups
  across tx.fhir.org, OHDSI, EBI OLS and NLM, from eight threads. One shared
  `httpx.Client` (thread-safe, pooled) would keep connections alive for the
  whole prefetch.

## 9. PR reviewer: repeated GitHub reads and one quadratic loop

- `Reader.list_review_prs` (`pr_review/github.py:52`) makes one `/files` call per
  open PR to find the ones that touch mapping files (N+1).
- Opening a matrix calls `pr_refs`, which makes three calls (`/pulls/{n}`,
  `/compare`, `/files`), **even when the matrix is cached**
  (`routes/pr_review.py:52`). `post_review` then fetches `/files` again in
  `diff_lines` (`github.py:89`). Fetching the file list once per request and
  passing it down would remove the repeats. Checking the head sha from
  `/pulls/{n}` alone before doing the rest would make a cache hit one call.
- `compare._ari_ids` (`compare.py:337`) scans every row of the PR's
  equivalencies file for each changed row, which is O(changes × rows). Group
  `head_equiv` by ARI number once in `build_matrix` and pass the group.
- `_changed_paths` reads only the first 100 changed files (`per_page=100`, no
  pagination). That is a correctness edge rather than a cost, but it lives in
  the same code.

## 10. Downloaded baselines are not reused

`pending-changes`, `publish`, `sync`, `resolve` and `export` each download the
source branch's ontology (~1.7 MB) and parse it (~80 ms). A routine publish runs
pending-changes (dialog open), then publish, then maybe resolve and publish
again: three or four downloads of the same bytes. `publish`, `sync` and
`resolve` already pin a commit sha, and the bytes at a sha never change.

**Fix:** a small on-disk cache under `.user-data/`, keyed by commit sha, with a
size cap. Pass the sha through `pending-changes` too (it currently reads the
branch name). Each step then copies the cached file instead of downloading it.

## 11. Smaller items

- **Sync on every tab focus** (`static/js/github.js:212`,
  `static/ref-edits/ref-edits.js:614`): every return to the tab posts
  `/api/v2/sync`, which costs one GitHub call against the curator's rate limit.
  With several windows and frequent tab switching, that adds up. Throttle it to
  once a minute per tab. The revision check still catches another window's
  merge.
- **Uncached GitHub listings:** `GET /api/v2/settings` lists every branch on
  each open (`routes/settings.py:52`), and `/source` and `/pr-base` list them
  again to validate. `/open-prs` lists all open PRs on each page load. A
  60-second cache would cut the API traffic.
- **Tree and search** (`static/js/trees.js`): switching tabs or toggling
  *Show only autoimmune* refetches the whole tree from the server. The server
  rebuilds it each time (3–7 ms), so it is cheap, but the client could keep it
  until an edit. Search requests are not cancelled, so a slow early response can
  replace a later one. An `AbortController` fixes both the wasted request and the
  out-of-order result.
- **`create_release`** (`ontology_service.py:1380`) reads `releases.json` three
  times and writes it with a plain `write_text` (`:1415`), not
  `atomic_store.write_json`. The repeated reads are trivial. The non-atomic
  write is a durability gap of the kind `atomic_store` was introduced to close.

## Checked and not worth changing

- **Property lookups.** `world[iri]` costs 1.7 µs, so caching property objects in
  `_get_annotation` / `_get_data` would add state for no measurable gain.
- **`get_disease_detail`'s subtype scan.** 1.2 ms per record, scan included. It
  only matters when multiplied by finding 1.
- **`search`, `get_tissue_hierarchy`, `get_alphabetical_tree`.** Linear scans or
  O(classes × diseases), but 3–16 ms at 214 diseases. Revisit if the catalogue
  grows by an order of magnitude.
- **`_save()` on every edit.** 42 ms to serialise the whole file. This is
  inherent to keeping the ontology as one Protégé-compatible OWL file, which is a
  product requirement.
- **`sssom_service._merge_tsv`** re-emits the whole mapping history on every
  publish. It is linear and already called out in the code as a deliberate,
  bounded cost.
- **`workspace._has_unpublished_work`** reads both files in full only when their
  sizes match, which is rare once a copy has been edited.
