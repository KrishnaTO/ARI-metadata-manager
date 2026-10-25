# ARI Disease Metadata Manager

A web app for browsing and curating the Autoimmune Registry (ARI) disease
catalogue. The catalogue is one Protégé-compatible OWL ontology kept in
[`KrishnaTO/ARI`](https://github.com/KrishnaTO/ARI). Curators sign in with their
own GitHub account, edit a private working copy, and publish their changes as a
pull request under their own name. FastAPI backend, vanilla-JavaScript frontend
with no build step, deployed on AWS Lightsail behind nginx at
<https://aurint.ca/ari-editor>.

| Page | What it is for |
| --- | --- |
| `/` | The record view: browse diseases by name, tissue or symptom, read a disease's full record, and edit it. |
| `/ref-edits/` | The cross-reference review matrix: confirm, flag or mark absent each disease's ids in SNOMED, OMOP, DOID, MONDO, NCIt, ICD-10, Orphanet, UMLS and MeSH, with predicted candidates for blank cells. Two-person rule: whoever adds an id cannot confirm it. |
| `/ref-edits/reviewer/` | Administrators review curators' mapping pull requests row by row against each database's current term. |
| `/ref-edits/synonyms/` | Curators judge report 9 from the data repo: is each name a synonym or a subtype? |
| `/stats/` | Curation dashboard: coverage per database, activity per curator, work waiting for a second reviewer. Not linked from the other pages. |

## Quick start

Requires Python 3.10+ and a checkout of `KrishnaTO/ARI` next to this repo (the
runtime ontology is not tracked here).

```bash
pip install -r requirements.txt
mkdir -p ontologies && cp ../ARI/ontologies/ari_t1d.owl ../ARI/ontologies/ari_uberon.owl ontologies/
mkdir -p ari-mappings && cp ../ARI/mappings/ari.synonym_blocklist.tsv ari-mappings/
python run.py
```

`ari_t1d.owl` `owl:imports` the UBERON slice `ari_uberon.owl` by an IRI that does
not resolve on the web. The app finds it in the base ontology's folder, so keep
the two files together. Until KrishnaTO/ARI#151 merges, ARI `main` has no slice:
copy `ari_t1d.owl` alone.

`run.py` serves <http://127.0.0.1:8001> and opens the browser. Options:
`--port 8002`, `--file path/to/ontology.owl`, `--no-browser`.

Without a `.env` the app runs with GitHub sign-in switched off. You can browse,
and edits go straight into `ontologies/ari_t1d.owl`. To sign in locally, create a
GitHub OAuth App with the callback `http://localhost:8001/auth/github/callback`,
copy `.env.example` to `.env`, and fill in the GitHub settings,
`APP_BASE_URL=http://localhost:8001` and a `SESSION_SECRET`.

## How it fits together

```
KrishnaTO/ARI ──(ontology, mappings, report 9)──► this app ──► curator's working copy
      ▲                                                               │
      └──────────── pull request, authored by the curator ◄───────────┘
```

- **Anonymous readers** see the shared base ontology, which
  `deploy/update-ontology.sh` refreshes from the data repo every ten minutes.
- **A signed-in curator's** first edit copies the base to
  `.user-data/<login>.owl`. Every page load merges the source branch into it
  three-way, so their edits and other people's merged work both survive.
- **Publishing** merges again, then commits only the diseases the curator
  touched (written over the branch's own file) plus any review verdicts appended
  to `mappings/ari.sssom.tsv` and `mappings/ari.equivalencies.tsv`. It opens or
  updates a PR, from the curator's fork if they lack push access.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains each part in detail.

## Repository layout

```
run.py                     Local launcher (uvicorn on 127.0.0.1)
requirements.txt           Runtime dependencies (owlready2, fastapi, uvicorn, httpx, itsdangerous, openpyxl)
requirements-dev.txt       + pytest, ruff
ruff.toml                  Lint config
.env.example               Settings template
instructions.md            The original product brief
changelog.md               Changes, per branch
DEPLOY.md · OPERATIONS.md  Server set-up · one-off server tasks
STAGING.md                 The staging copy at staging.aurint.ca

app/                       Backend (FastAPI)
  main.py                  Assembly: middleware, CSP, cache policy, /healthz, error handlers, routers
  config.py                Every setting; app version and asset token
  sessions.py              Server-side session store; who is calling
  workspace.py             Per-curator working copies, write gate, ancestors, sweep
  stores.py                Shared ledgers: assignments, id authorship, xref removals
  atomic_store.py          Atomic file writes; corrupt stores fail loudly
  errors.py                NotFound (404), Invalid (400)
  ontology_service.py      owlready2 read/write layer
  schema.py                Editable item categories
  xref_registry.py         The cross-reference databases, in one place
  id_allocator.py          Server-wide ARI number counter
  id_provenance.py         Who added each cross-reference id
  xref_removals.py         Removed ids, pending as negative judgments
  merge_service.py         Three-way merge; grafting diseases between ontologies
  owl_format.py            Writes a publish in the data repo's canonical OWL layout (ROBOT)
  diff_service.py          Change list, PR title and summary
  sssom_service.py         SSSOM + equivalencies files
  predict_service.py       Lexical indexes and cross-reference prediction
  enrich_service.py        Synonyms/subtypes from confirmed mappings
  concept_service.py       What the local indexes know about one id
  stats_service.py         Dashboard numbers
  github_service.py        OAuth, file reads, commit + pull request
  export_service.py        .xlsx export
  feedback_service.py      Per-term feedback
  assignment_service.py    Review queues
  synonym_review.py        Report 9 parser
  synonym_review_store.py  Marks and notes on report 9
  pr_review/               Mapping-PR reviewer: GitHub reads, live terminology, comparison, shared notes
  routes/                  Endpoints: ontology, review, publish, merge, settings, auth,
                           assignments, feedback, pr_review, pages

static/                    Frontend (no build step)
  index.html, js/, css/    Record view (scripts load in order; see ARCHITECTURE.md)
  ref-edits/               Review matrix (ref-edits.js, review-model.js)
    reviewer/              Mapping-PR reviewer
    synonyms/              Synonym vs subtype review
  stats/                   Curation dashboard
  vendor/                  Chart.js, D3, d3-cloud (vendored; see vendor/README.md)

scripts/                   Data tooling (see "Scripts" below)
data/2-databases/          Reference indexes for prediction (see its README)
mappings/                  Snapshot of the mapping files for the tooling and CI: SSSOM, equivalencies, predicted
tests/                     pytest suite, review-model.test.js, fixture ontology
deploy/                    systemd units, update scripts, nginx config
  staging/                 The staging copy's units and nginx site
design/mockups/            Design exploration for the review page and the editor (not wired up)
docs/                      Architecture, API, efficiency review, design specs
```

## Configuration

Settings come from environment variables, with `.env` at the repo root loaded at
startup (values in `.env` don't override variables already set). Secrets stay
on the server. Keep `.env` at mode 600.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | — | OAuth App credentials. Sign-in is on only when these and the owner/repo are set. |
| `GITHUB_OWNER`, `GITHUB_REPO` | — | The data repo the app reads from and opens PRs on (`KrishnaTO` / `ARI`). |
| `GITHUB_BASE_BRANCH` | `main` | Default source branch and PR target. |
| `GITHUB_ONTOLOGY_PATH` | `ontologies/ari_t1d.owl` | The ontology's path in the data repo. |
| `GITHUB_SSSOM_PATH`, `GITHUB_EQUIV_PATH` | `mappings/ari.sssom.tsv`, `mappings/ari.equivalencies.tsv` | Mapping files, both in the data repo and under this repo's root. |
| `GITHUB_SYNONYM_REVIEW_PATH` | `data/4-reports/9_Synonym_Review.tsv` | Report 9 in the data repo. |
| `GITHUB_SERVICE_TOKEN` | — | Optional token for the unattended refresh scripts. |
| `APP_BASE_URL` | `http://localhost:8001` | Public URL including any subpath; must match the OAuth callback. Cookies are `Secure` when it starts with `https`. |
| `OAUTH_CALLBACK_PATH` | `/auth/github/callback` | |
| `SESSION_SECRET` | — | Signs the session cookie. **Required when sign-in is configured**; the app won't start without it. Write it bare, without quotes. |
| `SESSION_TTL_DAYS` | `30` | Sign-ins older than this are dropped. `0` keeps them forever. |
| `ALLOWED_LOGINS` | empty (anyone) | Comma-separated GitHub logins allowed to sign in. Set it in production. |
| `ASSIGN_ADMINS` | empty (anyone signed in) | Logins who may cut releases, use the PR reviewer, and change other curators' queues and feedback. |
| `USER_DATA_TTL_DAYS` | `14` | Idle working copies are removed after this many days, or archived if they hold unpublished work. `0` disables. |
| `MAX_LOADED_WORLDS` | `8` | Working copies held in memory at once (least recently used are evicted). |
| `MAPPING_LICENSE` | CC0 1.0 URL | Licence written into the mapping files and shown before publishing. |
| `ARI_ONTOLOGY_FILE` | `ontologies/ari_t1d.owl` | Runtime ontology path. `run.py --file` sets it. |
| `PORT` | `8001` | `run.py`'s default port, and the port `deploy/update.sh` reads `/healthz` on. |
| `APP_REPO_BRANCH` | `main` | Branch of this repo that `deploy/update.sh` tracks. |
| `APP_SERVICE` | `ari-mm` | systemd unit `deploy/update.sh` restarts. |
| `ARI_MAPPING_FILES` | the four mapping files | `remote:local` pairs fetched by `deploy/update-mappings.sh`. |

## Server-side state

Created at runtime next to the code, all gitignored. Some of it exists nowhere
else, so back it up ([OPERATIONS.md §6](OPERATIONS.md)).

| Path | Holds | Recoverable? |
| --- | --- | --- |
| `ontologies/ari_t1d.owl` | The shared base ontology | Yes, refetched from the data repo |
| `ontologies/ari_uberon.owl` | The UBERON slice the base ontology imports | Yes, refetched with it |
| `.user-data/` | Working copies, ancestors, touched sets, branch choices, review sessions, publish replay log, archived idle copies | No: unpublished work |
| `.sessions.json` | Sessions, including live GitHub tokens (mode 0600) | Lost only signs people out. **Don't back it up unencrypted.** |
| `provenance/` | Who added each id, pending removals, the ARI number counter | Partly, with `scripts/backfill_id_authors.py` |
| `assignments/` | Review queues and their audit log | No |
| `feedback/` | Per-term feedback, its log and per-release archives | No |
| `releases/` | Release snapshots and `releases.json` | No |
| `pr-review/` | Reviewers' marks and notes per PR | No |
| `synonym-review/` | Marks and notes on report 9 | No |

## Development and tests

```bash
pip install -r requirements-dev.txt
pytest -q                              # backend suite, against tests/fixtures/ontologies/ari_t1d.owl
node --test tests/review-model.test.js # review-page policy
ruff check .                           # lint
python scripts/validate_mappings.py    # the published mapping set is valid SSSOM
```

CI (`.github/workflows/ci.yml`) runs all four on every PR and on pushes to
`main`; every check is required. Tests import `app`, which loads the ontology at
import time; `tests/conftest.py` points it at the fixture. Tests that write must
patch `workspace.BASE` and the `config` paths, never the real files.

## Scripts

| Script | What it does |
| --- | --- |
| `scripts/fetch_databases.py` | Downloads MONDO, DOID, NCIt, MeSH and Orphanet and builds `data/2-databases/` (`--offline`, `--only`). |
| `scripts/eval_fuzzy.py` | Replays the confirmed mappings to re-derive the fuzzy-match threshold. |
| `scripts/report_synonyms.py` | Finds ARI synonyms that name a different disease; writes the synonym blocklist and its report into a data-repo checkout (`--data-repo ../ARI`), to commit there. |
| `scripts/validate_mappings.py` | Checks the mapping files (prefixes declared, no doubled prefixes, ISO dates, no contradictory live rows). Run in CI. |
| `scripts/migrate_mappings.py` | One-off: rewrites the mapping files under the current SSSOM header. |
| `scripts/backfill_id_authors.py` | Seeds the id-authorship ledger from the curated mappings (from `KrishnaTO/ARI@main`, or `--local`). Safe to re-run. |
| `scripts/import_reports.py` | Folds the `data/4-reports/` catalogue into an ontology (additive, idempotent). |
| `scripts/build_t1d_ontology.py` | Builds the original seed T1D ontology from scratch. |

## Deployment

[DEPLOY.md](DEPLOY.md) covers the server: Ubuntu on Lightsail, uvicorn under
systemd (`ari-mm`), nginx serving the app at `/ari-editor`, and Cloudflare SSL.
Three timers keep it current every ten minutes:

- **App code** (`update.sh`) tracks this repo's `APP_REPO_BRANCH` and restarts
  the app when the running commit differs from `HEAD`, deferring while curators
  have work in memory.
- **Ontology** (`update-ontology.sh`) replaces the base ontology atomically; the
  app reloads it without a restart.
- **Mapping files** (`update-mappings.sh`).

[OPERATIONS.md](OPERATIONS.md) lists the server-side tasks a pull request
cannot do: secrets, allow-lists, OAuth scope, nginx, backups and logs.

[STAGING.md](STAGING.md) sets up a second copy on the same box at
`https://staging.aurint.ca`. It runs any branch of this repo against a throwaway
copy of the data repo, `KrishnaTO/ARI-staging`, so a major change can be
published end to end without touching the live site or `KrishnaTO/ARI`. The
deploy scripts act on the checkout they live in, which is what lets both copies
run side by side.

## Versioning

The app version is `2.<commit count> (<short sha>, <commit date>)`, derived from
git at startup. It appears in the page footer, in `/api/v2/overview` and in
`/healthz`, and it bumps on every deployed commit. Ontology releases are
separate: an administrator cuts them from the record view, and each is numbered
`1.<n>` unless given a name.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how each part works.
- [docs/API.md](docs/API.md): every endpoint.
- [docs/EFFICIENCY.md](docs/EFFICIENCY.md): measured performance costs and suggested fixes.
- [data/2-databases/README.md](data/2-databases/README.md): the reference indexes.
- [static/vendor/README.md](static/vendor/README.md): vendored libraries and how to refresh them.
- [design/mockups/README.md](design/mockups/README.md): design exploration for the review page and the editor redesign.
- `docs/superpowers/`: the working-copy merge design and its implementation plan.

## Data sources and licence

Disease content comes from the ARI catalogue in `KrishnaTO/ARI`. The reference
indexes are built from MONDO, DOID, NCIt, MeSH and Orphanet (licences in
`data/2-databases/README.md`). The review compare pane and the PR reviewer look
terms up live at tx.fhir.org, OHDSI, EBI OLS, NLM MeSH and NCBI MedGen. Those
lookups are read-only and never written into the ontology. Mapping files are
published under `MAPPING_LICENSE` (CC0 by default).

The code is licensed under the GNU GPL v3 (see [LICENSE](LICENSE)).
