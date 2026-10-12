# Efficiency review

A review of the app's key code paths for avoidable work. Each finding says where
the cost is, how big it is, and what would remove it, so they can be picked up
one at a time.

**Status (2026-10-12):** findings 1, 2, 4, 5, 7, 8, 9, 10, 12, 13 and 14 are
fixed and deployed (#189–#196, #203–#209); each section says by which PR, with
the cost after. Finding 6 is fixed in #214. Finding 3 is partly fixed (#206).
Finding 11 is partly fixed (#212, #215); its other items were checked and left
as they are, with the reasons in §11.
The measurements below are from before the fixes, except the table
[after the fixes](#production-after-the-fixes).

**Baseline:** `main` at `e8c8a6c`, which is also what production runs
(`2.348 (e8c8a6c, 2026-10-08)`). **Method, in three passes:**

1. **Code reading.** Every module under `app/`, `static/` and `deploy/`.
2. **Local profiling.** Every endpoint was timed in-process, and the service
   functions behind the slow ones were profiled with `cProfile`. This ran
   against a scratch copy of the runtime ontology (214 diseases) and the
   committed `data/2-databases` indexes, on a Windows development machine with
   Python 3.14. A local server (GitHub sign-in off) was then driven in the
   browser to read page-load waterfalls and time client-side rendering.
3. **Production, read-only.** <https://aurint.ca/ari-editor/>, signed out, on
   2026-10-11, from the browser: page waterfalls, response headers, repeated
   endpoint timings and client-side rendering. Nothing was written. Sync and
   publish need a signed-in session, so those were measured locally only.

Where the same work was timed in both places, **production is about 2.5× slower
than the development machine.** Export takes 959 ms against 365 ms; the
prediction cold start takes 5.0 s against 2.1 s. Read local-only figures with
that factor in mind.

### Production, measured

| Request (signed out) | Time | Notes |
| --- | ---: | --- |
| Round trip (`/api/v2/me`, `/healthz`) | 21–31 ms | HTTP/3 through Cloudflare |
| `GET /api/v2/predictions`, first after a restart | **4,983 ms** | The review matrix waits for it, so the page took 5.4 s |
| `GET /api/v2/predictions`, warm | 28–42 ms | |
| `POST /api/v2/enrichment-preview` with an **empty** list | **1,346 ms** | §2 |
| `GET /api/v2/export` (signed out, no baseline) | 959 ms | §1 |
| `GET /api/v2/synonym-review` | 641–946 ms | 2.7 MB of JSON, fetched from GitHub every time (§14) |
| `GET /api/v2/concept/{db}/{id}`, first lookup of an id | 226–1,449 ms | live source; the first one also builds the reverse map |
| `GET /api/v2/concept/{db}/{id}`, repeat | 25–35 ms | |
| `GET /api/v2/open-prs`, `GET /api/v2/settings` | 112–232 ms | one GitHub call each, uncached (§11) |
| `GET /api/v2/symptoms` | 111–182 ms | 340 KB of JSON |
| `GET /api/v2/mappings` · `xrefs` · `stats` | ~100 · ~75 · ~70 ms | `mappings` is 390 KB of JSON |
| Record view: open the largest record (Type 1 diabetes) | 52 ms | including the request |
| Record view: open a deep dive | 5–38 ms | the prevalence chart is the 38 |
| Review page: one keystroke in the filter | 37–129 ms | blocks the page (§12) |

**What production does well:** versioned JS and CSS are Cloudflare cache HITs,
`immutable`, and zstd-compressed. JSON is compressed too (the 2.7 MB report
travels as 200 KB). The HTML is revalidated on each load and arrives in 58 ms.

### Local, measured

| Function | Time |
| --- | ---: |
| Parse the ontology into an `OntologyService` | 78 ms |
| `get_disease_detail` (one record) | 1.2 ms |
| `get_xref_rows` (whole review grid) | 12 ms |
| `search("arth")` | 16 ms |
| `_save()` (serialise the OWL file) | 42 ms |
| `diff_service.list_changes` (publish summary / pending changes) | **697–813 ms** |
| `export_service.build_report` with a baseline | **588–600 ms** |
| `merge_service.merge_into`, every disease (each sync that finds the branch moved) | **714 ms** |
| `predict_service.get_indexes()`, cold | **1,504 ms** |
| `predict_xrefs()`, first call after the indexes load | **598 ms** |
| `predict_xrefs()`, cached | 0.1 ms |
| `enrichment_preview` for **one** disease | **552–638 ms** |
| …of which `build_id_index` + `load_subtypes` (rebuilt on every call) | 278 + 209 ms |
| `concept_service._build_reverse` (first lookup, then cached) | 376 ms |
| Publish, local steps other than the summary (graft, save, splice, SSSOM build) | ~70 ms |
| Review page: one full pass of `ReviewModel.cellState` over the matrix | **156 ms** (1.2 ms with predictions indexed) |
| `world[property IRI]` lookup ×1000 | 1.7 ms |

### Production, after the fixes

Re-timed on 2026-10-12 against `2.376 (81667b8)`, signed out, three runs each.

| Request (signed out) | Before | After |
| --- | ---: | ---: |
| `GET /api/v2/predictions`, first after a restart | 4,983 ms | no request pays it: loaded at startup (§4) |
| `GET /api/v2/predictions`, warm | 28–42 ms | 59–118 ms |
| `POST /api/v2/enrichment-preview` with an empty list | 1,346 ms | 53–93 ms |
| `GET /api/v2/export` (no baseline) | 959 ms | 424–499 ms |
| App restarts from the mapping refresh | every ~10 min | none (§5) |
| `GET /api/v2/synonym-review`, repeat load | 641–946 ms | 428–490 ms (§14) |

The synonym-review row was timed later, against `2.414 (a069f4a)`, with `curl`
from a client whose round trip to the app is 57–103 ms (the browser figures
above had 21–31 ms), so it overstates the wait slightly.

The publish summary and sync need a signed-in session, so their after figures
are local, in §1 and §13. Findings 7–10 change sign-in-only paths (sync, publish,
the PR reviewer); their after figures are local or against the live GitHub API,
in each section.

## Summary, by expected payoff

| # | Finding | Where | Cost when found | Effort | Status |
| --- | --- | --- | --- | --- | --- |
| 12 | The review page recomputes every cell from all predictions, several times per redraw, on every keystroke | `static/ref-edits/review-model.js` `predFor`, `ref-edits.js` filter | 37–129 ms per keystroke in production, 230–620 ms locally; blocks the page | Small | Fixed, #189 |
| 1 | The change summary and the export build every disease's full detail, twice | `diff_service._rows`, `export_service._rows_from_service` | ~0.7 s locally (~1.8 s estimated in production) per publish, dialog open or export | Small | Fixed, #194 and #195 |
| 2 | Enrichment rebuilds two whole-index structures on every call | `enrich_service.enrich_many` | 1.35 s per call in production, even for an empty list | Small | Fixed, #190 |
| 4 | The prediction indexes load on the first request after every restart | `predict_service.get_indexes` | 5.0 s in production for the first review-page visitor | Small | Fixed, #191 |
| 5 | The mapping refresh restarts the app for no reason | `deploy/update-mappings.sh:89` | Each restart puts the next visitor through the cold start above | Small (ops) | Fixed, #191 and #192 |
| 13 | Sync merges every disease and re-reads each record several times | `merge_service.merge_into`, `routes/merge.py` | 0.7 s locally (~1.8 s estimated in production) each time the branch moves | Small | Fixed, #196 |
| 3 | CPU-bound work runs on the event loop and blocks every other request | all `async def` routes in `app/routes/` | Everyone waits for the slowest request | Medium | Partly fixed, #206 |
| 7 | Independent network calls run one after another | frontend `init()`s, mapping reads, blob uploads | 4 serial calls before the tree; 5 before the matrix | Small | Fixed, #204 and #205 |
| 14 | The synonym review re-downloads report 9 on every load | `routes/review.py` `synonym_review`, `synonyms.js` | 0.64–0.95 s and 2.7 MB of JSON per load | Small | Fixed, #203 |
| 6 | Three separate reverse-id indexes over the same records | `predict_service`, `concept_service`, `enrich_service` | ~125 MB resident | Medium | Fixed, #214 |
| 8 | No HTTP connection reuse | `github_service`, `pr_review/terminology` | One TLS handshake per call; hundreds per PR matrix | Small | Fixed, #207 |
| 9 | PR reviewer repeats GitHub reads, and one loop is quadratic | `pr_review/github.py`, `pr_review/compare.py` | N+1 calls to list PRs; 3 fetches of one file list | Small | Fixed, #208 |
| 10 | Downloaded baselines are not reused | `routes/publish.py`, `merge.py`, `settings.py` | ~1.7 MB download + parse per dialog, publish, resolve, export | Medium | Fixed, #209 |
| 11 | Smaller items | various | see below | Small | Partly fixed, #212 and #215; the rest left as is |

What is still open: **§3, step 3**, only if a measurement shows the remaining
loop-blocking work (tens to ~150 ms) matters with more curators at once. §11's
remaining items were weighed and left as they are; each says why, so revisit
one only if what it depends on changes.

---

## 1. The change summary and the export build every disease's full detail, twice

**Fixed in #194 (summary) and #195 (export).** The summary matches records on an index of ARI ids and builds full records only for the diseases in scope: 39 ms instead of 595 ms with four in scope, and the dialog's list 28 ms instead of 526 ms. The export reads `OntologyService.get_disease_records()`, each disease's own fields: 179 ms instead of 642 ms with a baseline, 139 ms instead of 373 ms without (locally).

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

**Fixed in #190.** Both structures are built once and reused until the reference files change, and a preview with nothing confirmed returns at once. Previews take 1–9 ms in-process; the empty preview takes 53–93 ms in production, round trip included.

**Where:** `app/enrich_service.py:235` (`enrich_many`), which calls
`build_id_index(indexes)` (`:254`) and `load_subtypes(indexes, index_dir)`
(`:251`) every time.

The lexical indexes themselves are cached (`get_indexes`), but these two
structures, derived from them, are not. `build_id_index` walks every
cross-reference of every term in all five indexes (278 ms, 39 MB peak).
`load_subtypes` builds a label map over every record and re-parses the three
`*.subtypes.tsv` files (209 ms).

`POST /api/v2/enrichment-preview` pays this on every call, even for one disease
(552–638 ms locally). **In production it took 1,346 ms with an empty list**: the
structures are built before anything checks whether there is work to do.
Publishing with *Apply on publish* pays it again, because `apply_enrichment`
re-runs the preview (`ontology_service.py:1201`). Also return early when
`confirmed` is empty.

**Fix:** cache both alongside the index cache, with the same invalidation.
`concept_service._reverse_for` already shows the pattern: key on the identity of
the list `get_indexes()` returns, which changes only when the files do. After the
first call, each preview costs only `enrich()` itself (sub-millisecond per
disease).

## 3. CPU-bound work runs on the event loop

**Partly fixed in #206.** Steps 1 and 2 below are done. The one case that still held the loop for seconds, an index reload inside the predictions or enrichment-preview handler after `data/2-databases` changed, now runs in a thread (`predict_service.warm`, `enrich_service.warm`), so `/healthz` answers during it. The ontology reads and the matching stay on the loop: locally a prediction recompute after an edit takes ~35 ms, an export with a baseline ~150 ms and `get_xref_rows` ~10 ms. Step 3 is not done.

**Where:** every handler in `app/routes/` except `concept_detail_lookup` and the
PR-reviewer routes is `async def`, and calls synchronous owlready2 or index code
directly.

The app runs one uvicorn worker with one event loop (`workspace.py` relies on
this for its per-login `asyncio.Lock`). A synchronous call inside an `async def`
handler holds that loop. Nothing else is served until the call returns: no other
curator's request, no static file, not even `/healthz`. The slow cases were
findings 1, 2 and 4: 0.5–2 s each, with publish stacking several. Those are now
fixed, so what still runs on the loop is shorter: a summary, a merge or an
export takes tens to hundreds of milliseconds.

This also affects deploys. `deploy/update.sh` reads `/healthz` with a 5-second
timeout. If it gets no answer, it treats the app as not running, and the
`worlds_in_memory` deferral check finds nothing to read. So a sufficiently long
stall during a timer tick leads to a restart without the usual protection for
curators with unsaved work.

**Fix, in order:**

1. Shrink the blocking work (findings 1, 2 and 4). Done.
2. Move index-only work off the loop with `asyncio.to_thread`: enrichment and
   prediction over already-built rows. It touches no owlready2 `World`, so it is
   safe in a thread. Index loading already runs in a thread at startup (#191),
   under a lock that makes concurrent first callers share one load.
3. Only then consider threading owlready2 calls. A `World` is SQLite-backed and
   is not safe for unguarded concurrent writes, so this needs a per-`World` lock
   in addition to today's per-login one. Measure before taking this on.

## 4. The prediction indexes load on the first request after every restart

**Fixed in #191.** `predict_service.warm` loads the indexes and their lookups in a background thread from `main.lifespan`. Locally the first predictions call after a restart takes 81 ms instead of 2,224 ms. The lookups are also published only once complete, so a reader on another thread never sees a half-built map.

**Where:** `app/predict_service.py:387` (`get_indexes`), first reached through
`GET /api/v2/predictions`.

The first review-page load after a restart parses ~85k terms from the five
index files (1.5 s locally). The first prediction pass then builds the fuzzy
word index and the per-index reverse maps (0.6 s). **In production this took
4,983 ms**, and because the review page doesn't draw the matrix until
predictions arrive (§7), the first visitor saw an empty page for 5.4 s.
Because of finding 3, every other request waited too. Restarts are frequent:
every deploy, and every mapping refresh (finding 5). The compare pane's first
lookup after a restart pays a similar one-off cost when it builds its reverse
map (1,449 ms for the first MONDO lookup in production).

**Fix:** warm the cache in `main.lifespan`. Call
`asyncio.to_thread(predict_service.get_indexes)` (and, once finding 2 is done,
the enrichment structures) as a background task at startup. Use the lock from
finding 3 so a request arriving mid-warm-up waits for that load instead of
starting a second one.

## 5. The mapping refresh restarts the app for nothing

**Fixed in #191 and #192.** The script no longer restarts the app (#191). It also wrote over this repo's tracked mapping files, so `update.sh` stashed and reset them every run and the next refresh saw a "change": that was the cause of the restart every ten minutes. The fetched copies now go to the gitignored `ari-mappings/` (#192), and `update.sh` drops a stash entry identical to the previous one (#193).

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

**Fixed in #214.** The compare pane and enrichment now look ids up through `LexicalIndex.records_for`, the per-index map prediction already used, and their own maps are gone: resident memory after the warm-up, one enrichment preview and one compare-pane lookup is 300 MB instead of 372 MB locally. `_REVERSE_CACHE` went with them, so an index rebuild no longer keeps the old map alive. Enrichment now matches ids by `match_key`, as the caveat below anticipated; over the real ontology (every stored id of all 214 diseases treated as confirmed, 1,097 cross-references) the enrichment preview and all 1,276 compare-pane lookups are identical to before. The one id that resolves differently is a MONDO id stored with its prefix (`MONDO:0005147` on ARI:0001080), which exact matching missed; another of that disease's ids already reached the same term.

**Where:**

- `LexicalIndex.records_for` (`predict_service.py:315`): one map per index file,
  keyed `(db, match_key(id))`. **51 MB** across the five indexes.
- `concept_service._build_reverse` (`concept_service.py:79`): one map over all
  indexes, keyed the same way, holding `(source, record)`. **47 MB**.
- `enrich_service.build_id_index`: keyed `(db, exact id)`, keeping only ids
  unique within their source. **27 MB**, kept since #190 fixed finding 2.

All three answer the same question: which terms carry this id? All three stay
resident for the life of the process.

**Fix:** build one map per index load,
`(db, match_key(id)) -> [(source, record)]`, and serve all three callers from it.
`records_for` becomes a filter on `source`. Enrichment's "unique within one
source" rule becomes a check at lookup time on the same list. That saves about
75 MB resident.

**Caveat:** enrichment keys on the exact id string today, while the other two
use `match_key` (prefix stripped, leading zeros dropped, casefolded). Unifying
makes enrichment match ids the way prediction and the compare pane already do.
That is probably the intent, but pin it with a test first.

**Related leak:** `_REVERSE_CACHE` (`concept_service.py:72`) is keyed by
`id(indexes)` and never evicts. After `fetch_databases.py` rebuilds the index
files, the old index list and its 47 MB map stay reachable until restart. Keep
one entry, not a dict.

## 7. Independent network calls run one after another

**Fixed in #204 (pages) and #205 (server).** The review page sends its seven independent start-up requests together after `me` and `sync`, and the main page its three; locally they all start at the same moment instead of one after another. Publish uploads its blobs together and creates each label once per process, the two mapping files are read together, and the PR matrix reads its files through a small thread pool (five reads in flight instead of one).

- **Review page start-up** (`static/ref-edits/ref-edits.js:2021`): `me`, then
  `sync`, then `xref-databases`, `xrefs`, `mappings`, `predictions`,
  `id-authors`, `ref-session` and `assignments`, each awaited in turn. Only
  `me` and `sync` have to come first. The other seven are independent and can go
  out together with `Promise.all`, so the page waits for the slowest one instead
  of the sum. `mappings` is itself two GitHub downloads and `predictions` may be
  a cold start, so this is the biggest wait a curator sees. Measured in
  production (signed out, so without `sync` and the signed-in reads): `me` →
  `xref-databases` → `xrefs` → `mappings` → `predictions` ran strictly in
  series, and the matrix appeared only after the last one finished.
- **Main page start-up** (`static/js/main.js:5–9`): `overview`, `schema` and
  `xref-databases` run in series. They are independent. In production the
  chain `overview` → `schema` → `xref-databases` → `tree/alphabetical` started
  at 441 ms (after the scripts) and finished at 841 ms. The first call alone took
  249 ms, probably the shared base ontology reloading after a refresh.
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

**Fixed in #207.** `github_service` shares one pooled `httpx.AsyncClient` per event loop (one in production), with each call's headers and timeout applied per request. Three sequential GitHub calls take 92 ms warm instead of 335 ms (150 ms instead of 679 ms cold), against the live API. Terminology lookups share one thread-safe `httpx.Client`.

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

**Fixed in #208.** Listing the PRs that touch mapping files is one GraphQL call per 50 PRs instead of 1 + N. A cached matrix costs one call (the head sha) instead of three, and within a request a PR and its file list are read once. The file list is paginated, so files past the first 100 are no longer missed, and ids are matched through the equivalencies file grouped by disease once per matrix.

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

**Fixed in #209.** `app/baselines.py` keeps the source branch's ontology by commit sha under `.user-data/baselines/` (the 8 most recently used, about 14 MB). The publish dialog and the export resolve the branch to its commit first, so the publish that follows the dialog reuses its download. A value that is not a 40-character hex sha is refused before it names a file.

`pending-changes`, `publish`, `sync`, `resolve` and `export` each download the
source branch's ontology (~1.7 MB) and parse it (~80 ms). A routine publish runs
pending-changes (dialog open), then publish, then maybe resolve and publish
again: three or four downloads of the same bytes. `publish`, `sync` and
`resolve` already pin a commit sha, and the bytes at a sha never change.

**Fix:** a small on-disk cache under `.user-data/`, keyed by commit sha, with a
size cap. Pass the sha through `pending-changes` too (it currently reads the
branch name). Each step then copies the cached file instead of downloading it.

## 11. Smaller items

**Partly fixed.** The `/symptoms` payload item and the search half of "Tree and
search" are fixed in #215, and `create_release` in #212. The other items were
checked and left as they are; each one below says why.

- **Sync on every tab focus** (`static/js/github.js:212`,
  `static/ref-edits/ref-edits.js:614`): every return to the tab posts
  `/api/v2/sync`, which costs one GitHub call against the curator's rate limit.
  With several windows and frequent tab switching, that adds up. Throttle it to
  once a minute per tab. The revision check still catches another window's
  merge.
  **Left as is.** The sync on focus is also how a background window learns that
  another window merged (#162): it reads the copy's revision in the same answer.
  A throttle in the page would skip that check for a minute, and a stale window
  could save old list values over the merge. A throttle on the server keeps the
  check but needs its own per-curator state, and it changes what ten sync tests
  rely on. What it saves is one GitHub call per focus, against a limit of 5,000
  an hour per curator, over a pooled connection since #207.
- **Uncached GitHub listings:** `GET /api/v2/settings` lists every branch on
  each open (`routes/settings.py:52`), and `/source` and `/pr-base` list them
  again to validate. `/open-prs` lists all open PRs on each page load. In
  production each costs 112–232 ms against a 22 ms round trip. A 60-second
  cache would cut both the wait and the API traffic.
  **Left as is.** A cache would hide the branch and pull request a curator has
  just published until it expired, unless every publish cleared it. The page
  already fetches `/open-prs` once per load and reuses it (`detail.js`
  `getOpenPRs`), and `/settings` is read when the settings dialog opens. That is
  not enough traffic to justify the invalidation.
- **Large JSON payloads:** `/api/v2/symptoms` (340 KB, 111–182 ms in
  production; it is read for the symptom tab and the symptom search),
  `/api/v2/mappings` (390 KB in production) and `/api/v2/xrefs` (203 KB).
  Cloudflare compresses them well, so the transfer is small, but each is still
  serialised on the server and parsed in the browser on every load.
  `/symptoms` carries an IRI for each of its 2,037 symptoms that neither the
  symptom tab (`trees.js`) nor the symptom search (`symptoms.js`) reads. That is
  40% of the payload (142 of 360 KB). The symptom search then fetches
  `/diseases` to turn the disease *names* back into IRIs, which returning the
  owning disease's IRI would make unnecessary.
  **Fixed in #215 for `/symptoms`.** Each symptom names its diseases as
  `{iri, name}` and no longer carries its own IRI. The symptom search opens a
  disease by that IRI: it no longer fetches `/diseases` (50 KB) or matches
  disease names. The response stays about the same size (363 KB instead of
  369 KB locally), because every symptom has exactly one disease, whose IRI
  replaces the symptom's. The symptom tab is not a consumer: `index.html` has no
  such tab, so its renderer (`renderSymptomsView`) was unreachable and #215
  deletes it. `/mappings` and `/xrefs` are unchanged.
- **Vendored libraries on every load:** the record view loads Chart.js (204 KB)
  and D3 (273 KB) up front, though only the prevalence chart, the word cloud
  and the pathophysiology graph use them. They are cached, but parsed on every
  page load. Loading them when a deep dive first needs them would take ~0.5 MB
  of script parsing off the start-up path.
  **Left as is, measured.** Compiling and running all three, cold (a fresh URL,
  so no code cache), takes ~16 ms locally: Chart.js 6–7 ms, D3 8–11 ms, the
  word-cloud layout 1 ms, against 67 ms to `DOMContentLoaded`. Loading them on
  demand would make the prevalence chart, the word cloud and the graph
  asynchronous, each able to finish after the curator has moved to another
  record, to save that.
- **Local mode reloads the ontology after each write.** With GitHub sign-in
  off, writes go to the shared base file. That changes its mtime, so the next
  request reloads the whole ontology (`workspace.base()`): writes after the
  first took ~125 ms instead of ~46 ms in the local run, and the prediction
  cache was thrown away each time. Production is unaffected (curators write to
  their own copies), but `_save()` on the base could refresh `_BASE_KEY`.
  **Left as is.** It affects development only, and the fix would couple
  `OntologyService._save` to `workspace`'s reload key.
- **Tree and search** (`static/js/trees.js`): switching tabs or toggling
  *Show only autoimmune* refetches the whole tree from the server. The server
  rebuilds it each time (3–7 ms), so it is cheap, but the client could keep it
  until an edit. Search requests are not cancelled, so a slow early response can
  replace a later one. An `AbortController` fixes both the wasted request and the
  out-of-order result.
  **Search fixed in #215.** A newer query cancels the dropdown's request in
  flight, and Enter cancels a pending one, so a late answer can no longer replace
  a newer one or reopen the dropdown over the results page. **The tree is left as
  is:** the server rebuilds it in 3–7 ms, and a copy kept in the page would need
  clearing on every edit.
- **`create_release`** (`ontology_service.py:1380`) reads `releases.json` three
  times and writes it with a plain `write_text` (`:1415`), not
  `atomic_store.write_json`. The repeated reads are trivial. The non-atomic
  write is a durability gap of the kind `atomic_store` was introduced to close.
  **Fixed in #212.** The manifest is read and written through `atomic_store`, and
  a corrupt one stops the release instead of being replaced.

## 12. The review page recomputes every cell from all predictions, on every keystroke

**Fixed in #189.** Predictions are grouped by cell once when they load (`ReviewModel.indexPredictions`). Typing, sorting and the *missing only* toggle take 1–54 ms locally, and the matrix is identical to before.

**Where:** `static/ref-edits/review-model.js:32` (`predFor`), reached through
`cellEntries` → `cellState`; the filter box is wired straight to `renderMatrix`
(`static/ref-edits/ref-edits.js`, `$('#filter').addEventListener('input', renderMatrix)`).

To find the predictions for one blank cell, `predFor` walks **every**
prediction on the page (`Object.entries(s.predicted)`) and splits each key
string. It runs once per blank cell, and a redraw computes each cell's state
several times over:

- the filter in `visibleRows` checks every cell's entries;
- `renderMatrix` computes each visible cell's state and entries;
- the first-run banner's `emptiestDb` runs `isMissing` for every disease in
  every column;
- a column sort calls `cellNeed` inside the comparator, so O(n log n) times.

**Measured, in the browser.** Locally there are 602 predictions and 1,926 cells.

- One full `cellState` pass over the matrix takes **156 ms** as written.
- The same pass with predictions indexed by cell takes **1.2 ms**, and returns
  identical results for every cell.
- One keystroke in the filter blocks the page for 230–620 ms, and still
  150 ms when nothing matches. So the cost is the state computation, not drawing
  rows.

In production, where more cells are already filled and there are fewer
predictions, a keystroke costs 37–129 ms. The cost scales with
predictions × blank cells, so it grows as diseases are added.

**Fix:**

- When `/api/v2/predictions` loads, build `predictedByCell`, keyed
  `"<ari_id>|<prefix>"`, once. Pass it in the model and have `predFor` look it
  up. The function's contract is unchanged, so `tests/review-model.test.js`
  still applies, with one more field on the state.
- Compute each cell's state once per render, not per caller.
- Debounce the filter input by about 100 ms.

## 13. Sync merges every disease, re-reading each record several times

**Fixed in #196**, by fixes 1 and 2 below. The ancestor records the git blob id of the file it was synced against, and sync skips the download, parse and merge when GitHub reports the same blob. When the file has changed, `merge_into` reads both ontologies in bulk and merges only the diseases the branch changed: 73 ms instead of 713 ms locally, with identical results.

**Where:** `routes/merge.py:66` passes `_all_diseases(theirs, mine)` to
`workspace.merge_from` → `merge_service.merge_into` (`merge_service.py:323`).

Whenever the source branch's head has moved, which happens on any commit to the
data repo, even one that touches only mappings or reports, `POST /api/v2/sync`
downloads the ontology and three-way merges **all** 214 diseases. Locally, with
5 diseases changed upstream and 3 edited, that took **714 ms** (about 1.8 s
estimated in production). It ran 371,000 SQLite queries: each subject's
triples are read for the ancestor, the working copy and the branch, then read
again to compare the merged result with the working copy and the ancestor with
the branch. `_pairs` ran 14,700 times. Sync runs on every page load and every
return to the tab.

**Fix, cheapest first:**

1. **Skip unchanged ontology files.** If the ontology at the new sha is
   byte-identical to the ancestor, advance the ancestor's sha and stop. The
   contents API returns each file's blob sha, so comparing that with a stored
   one avoids even the download. Most commits to the data repo don't touch the
   ontology.
2. **Merge only what changed.** `owl_splice._segments` already splits the file
   into per-entity blocks, keyed by `rdf:about`, and the blocks are
   byte-identical whatever their order. A disease whose block, and whose items'
   blocks, are identical in the ancestor and the branch has nothing upstream to
   merge. So only the touched diseases plus the changed ones need
   `merge_disease`. The test errs on the safe side: a block that differs only in
   formatting (say, after a hand edit in the data repo) merely triggers a merge
   that finds nothing to do.
3. **Memoise `_triples(svc, subject)`** for the length of one `merge_into`
   call. That halves the query count even for a full merge.

## 14. The synonym review re-downloads report 9 on every load

**Fixed in #203.** The endpoint reads the report's git blob id (one small call) and reuses the parsed rows until it changes. Each row carries its disease's IRI, so the page no longer loads `/api/v2/xrefs`. A repeat load in production takes 428–490 ms instead of 641–946 ms (see the [after table](#production-after-the-fixes)).

**Where:** `routes/review.py:110` (`GET /api/v2/synonym-review`) and
`static/ref-edits/synonyms/synonyms.js`.

Each load fetches `9_Synonym_Review.tsv` from GitHub, parses it and returns it
whole. In production that is **0.64–0.95 s and 2.7 MB of JSON** (200 KB
compressed) per load, for a report that changes only when the data repo's
notebook rewrites it. The page also loads all of `/api/v2/xrefs` (203 KB) only
to map each ARI id to its IRI for the "open in review" links.

**Fix:**

- Cache the parsed report keyed by the file's blob sha (a short TTL is enough),
  so a repeat load costs one small GitHub call or none.
- Add the IRI to each row on the server, which already holds the ontology, and
  drop the page's `/xrefs` request.

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
- **The record view.** Opening the largest record in production took 52 ms
  including the request; deep dives render in 5–38 ms; the tree endpoints
  answer in 33–57 ms.
- **Publishing, apart from the summary and the merge.** Graft, save, splice
  and the SSSOM build together cost ~70 ms locally.
- **Static delivery.** Versioned assets are Cloudflare cache HITs, `immutable`
  and zstd-compressed, over HTTP/3. Only the first visitor after a deploy fetches
  them from the server.
