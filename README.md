# ARI Disease Metadata Manager

A standalone **FastAPI** web app for browsing and editing the ARI autoimmune‑disease
catalogue, stored as a single Protégé‑compatible **OWL** ontology. Editors sign in with
their **own GitHub account**; every saved change is committed and opened as a pull request
under their identity, so contributions are attributed on GitHub. Deployable on AWS Lightsail
behind nginx.

> **Repository move:** this app was originally developed inside the main
> [`KrishnaTO/ARI`](https://github.com/KrishnaTO/ARI) repository under the
> `metadata-manager/` area. It now lives in this standalone repository,
> [`KrishnaTO/ARI-metadata-manager`](https://github.com/KrishnaTO/ARI-metadata-manager),
> so paths and setup commands in this README are relative to this repository root.

## Repository scope

This repository contains the metadata-manager app only: the FastAPI backend, vanilla-JS
frontend, ontology seed/import tooling, and deployment assets. It no longer includes the
full ARI monorepo history or sibling `data/` and `notebook/` areas from
[`KrishnaTO/ARI`](https://github.com/KrishnaTO/ARI). The local ontology used by the app is
kept under `ontologies/`.

## Areas (project structure)

```
ARI-metadata-manager/
├── run.py                      # Local launcher: uvicorn on 127.0.0.1:8001, opens browser
├── requirements.txt            # owlready2, fastapi, uvicorn, httpx, itsdangerous, openpyxl
├── requirements-dev.txt        # Dev/CI tooling only: pytest, ruff
├── ruff.toml                   # Lint config
├── instructions.md             # Original product brief for the app
├── changelog.md                # Human-facing change log
├── .env.example                # Config template (copy to .env; secrets stay server-side)
├── DEPLOY.md                   # AWS Lightsail + nginx + Cloudflare SSL deployment guide
│
├── app/                        # ── Backend (FastAPI) ──
│   ├── main.py                 #   Routes, OAuth/session, per-user services, publish, settings
│   ├── ontology_service.py     #   owlready2 read/edit layer (one World per working copy)
│   ├── schema.py               #   Editable data-item field schema (drives forms + writes)
│   ├── xref_registry.py        #   Single source of truth for cross-reference databases
│   ├── github_service.py       #   Per-user OAuth, commit, fork, cross-repo pull request
│   ├── sssom_service.py        #   Confirmed cross-refs -> SSSOM + equivalencies TSV
│   ├── diff_service.py         #   Human-readable change summary for PR bodies
│   ├── export_service.py       #   Export ontology -> 1_Core_ARI_Diseases.xlsx (marks changes)
│   └── feedback_service.py     #   File-backed per-term feedback log
│
├── scripts/                    # ── Data builders ──
│   ├── build_t1d_ontology.py   #   Generate the seed T1D ontology from scratch
│   └── import_reports.py       #   Fold data/4-reports/ catalogue into the ontology
│
├── tests/                      # pytest suite for the service layer
├── mappings/                   # Accumulated cross-reference judgments (merged into PRs)
│   ├── ari.sssom.tsv           #   SSSOM exactMatch mappings
│   └── ari.equivalencies.tsv   #   biomappings-style equivalencies
├── .github/workflows/ci.yml    # CI: pytest + advisory ruff lint
│
├── static/                     # ── Frontend (vanilla JS, no build step) ──
│   ├── index.html              #   Main page skeleton
│   ├── css/styles.css          #   All styles (light/dark)
│   ├── js/                     #   Classic scripts, loaded in order:
│   │   ├── core.js             #     state, constants, API helper, BASE_PATH detection
│   │   ├── trees.js            #     left nav: alphabetical / tissue / symptoms trees, search
│   │   ├── detail.js           #     middle panel: disease detail + narrative story
│   │   ├── panels.js           #     right panel: category deep-dive read views
│   │   ├── graph.js            #     D3 force-directed pathophysiology graph
│   │   ├── editor.js           #     edit toggle, field/item editors, admin releases
│   │   ├── symptoms.js         #     "search by symptoms" multi-select board
│   │   ├── feedback.js         #     per-term feedback panel
│   │   ├── github.js           #     sign-in + publish control
│   │   ├── settings.js         #     fetch-from-branch, switch source, PR target
│   │   └── main.js             #     bootstrap
│   ├── ref-edits/              #   Cross-reference review subpage (matrix)
│   │   ├── index.html
│   │   └── ref-edits.js        #     diseases x databases grid, side-panel review, SSSOM publish
│   └── ref-curate/             #   Disease curator subpage (one disease at a time)
│       ├── index.html
│       └── ref-curate.js       #     per-disease DB cards, preview, subtype, same SSSOM publish
│
├── deploy/                          # ── Hosting (systemd + nginx) ──
│   ├── ari-mm.service               #   uvicorn service (runs as ariapp on :8001)
│   ├── ari-mm-update.service        #   oneshot wrapper for update.sh
│   ├── ari-mm-update.timer          #   every 10 min: pull app branch, restart only if changed
│   ├── update.sh                    #   git reset --hard origin/<branch>; restart on change
│   ├── ari-mm-ontology-update.service  # oneshot wrapper for update-ontology.sh
│   ├── ari-mm-ontology-update.timer    # every 10 min: refresh ontology from the ARI repo
│   ├── update-ontology.sh           #   fetch ontology file from GitHub; restart only if changed
│   └── nginx.conf                   #   reverse proxy; strips the /ari-editor prefix
│
├── ontologies/ari_t1d.owl      # The ontology data file (RDF/XML, Protégé-compatible)
├── releases/                   # Versioned OWL snapshots          (gitignored)
├── feedback/                   # Runtime feedback log             (gitignored)
├── .user-data/                 # Per-user working copies          (gitignored, auto-swept)
└── .sessions.json              # Server-side session store        (gitignored, chmod 600)
```

## Key subsystems

### Per-user GitHub identity & publishing
Sign-in uses the GitHub OAuth Authorization-Code flow. The access token is held
**server-side only** (in the session store); the browser keeps just an opaque session id.
On **Publish**, the app commits the edited OWL on a branch named after the disease and opens
a PR authored by the signed-in user, so GitHub attributes the contribution to them.
Contributors without push access are handled by **forking**: the app creates a fork, commits
there, and opens a cross-repo PR with `maintainer_can_modify`. Re-publishing the same disease
appends commits to the existing PR. The only persistent secret is the OAuth client secret,
which never leaves the server. `app/github_service.py` owns this logic.

### Per-user working copies & isolation
Each signed-in editor edits an isolated copy of the ontology at `.user-data/<login>.owl`
(its own owlready2 World), so one editor's unpublished changes never leak into another's view
or into the shared baseline. A startup background task sweeps idle copies older than
`USER_DATA_TTL_DAYS` (default 14) to bound disk use. Defined in `app/main.py`.

### Cross-reference review → SSSOM
The `ref-edits` subpage lays out every disease against its database cross-references
(SNOMED, OMOP, DOID, UMLS, MONDO, ICD-10, MeSH, NCI). A curator reviews each id in a
resizable side panel and marks it correct or needs-change; empty cells link out to the target
database's search. Confirmed matches become `skos:exactMatch` rows in an **SSSOM** TSV plus a
simpler biomappings-style **equivalencies** TSV — both merged idempotently under `mappings/`
and included in the PR. Built by `app/sssom_service.py` + `static/ref-edits/`. The set of
databases (labels, CURIE prefixes, and link-out/search URL templates) lives in one place,
`app/xref_registry.py`, which both frontend pages fetch via `GET /api/v2/xref-databases`, so a
database is added or changed once instead of in four hand-synced spots.

The `ref-curate` subpage is a disease-first companion to that matrix: pick one disease and
curate all of its cross-references in a single stacked view — existing ids, prior judgments,
and exact-match predictions per database — with a live source preview and a new-subtype form.
It reuses the same APIs and writes the same SSSOM + equivalency files, so the two pages are
interchangeable. The main app's field editor deep-links into it (`ref-curate/#<disease-iri>`).

### Report import
`scripts/import_reports.py` folds the curated `data/4-reports/` catalogue (diseases, symptoms,
age-of-onset, prevalence, clinical subtypes, authorship, and all cross-references) into
`ontologies/ari_t1d.owl`. The import is additive and idempotent; the proposed-disease (`2_*`)
and proposed-change (`3_*`) reports are intentionally skipped — only the confirmed catalogue
is imported.

### Versioning
The manager reports a git-derived version, `2.<commit-count> (<sha>, <date>)`, that bumps on
every deployed commit; it is shown in the UI and stamped onto releases.

## Configuration (`.env`)

Copy `.env.example` to `.env` (gitignored, `chmod 600`). Secrets are server-side only and are
never sent to the browser.

| Key | Purpose |
| --- | --- |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | OAuth App credentials |
| `GITHUB_OWNER` / `GITHUB_REPO` | Repo the app reads ontology data from and writes ontology PRs to |
| `GITHUB_BASE_BRANCH` | Default PR target and the branch the ontology refresh tracks |
| `GITHUB_ONTOLOGY_PATH` | Path to the ontology file within that repo (default `ontologies/ari_t1d.owl`) |
| `GITHUB_SERVICE_TOKEN` | Optional server-side token for unattended ontology refreshes (see `deploy/update-ontology.sh`) |
| `APP_BASE_URL` | Public URL incl. subpath; must match the OAuth callback |
| `OAUTH_CALLBACK_PATH` | `/auth/github/callback` |
| `APP_REPO_BRANCH` | Branch of this app repo that `deploy/update.sh` tracks |
| `SESSION_SECRET` | Signs the session cookie (`openssl rand -hex 32`) |
| `ALLOWED_LOGINS` | Optional allow-list of GitHub logins (empty = any user with repo access) |
| `PORT` | Default `8001` |
| `USER_DATA_TTL_DAYS` | Idle per-user copy retention in days (`0` = never sweep) |

## Running locally

```bash
pip install -r requirements.txt
python run.py                 # serves http://127.0.0.1:8001 and opens the browser
```

GitHub sign-in needs a `.env` with OAuth credentials and an OAuth App whose callback is
`http://localhost:8001/auth/github/callback`; without it the app still browses anonymously.
Custom port / ontology: `python run.py --port 8002 --file path/to.owl`.

## Development & tests

```bash
pip install -r requirements-dev.txt   # adds pytest + ruff
pytest -q                             # service-layer suite under tests/
ruff check .                          # lint (config in ruff.toml)
```

CI (`.github/workflows/ci.yml`) runs a blocking smoke gate — install, byte-compile, import
(which loads the ontology), then `pytest` — on every push to `main` and every PR. A separate
ruff lint job is advisory for now (`--exit-zero`) while the existing code is brought up to the
ruleset.

## Deployment

See **DEPLOY.md**: Ubuntu 22.04 Lightsail, uvicorn under systemd (`ari-mm`), nginx reverse
proxy serving the app at `/ari-editor`, and Cloudflare free SSL. Two systemd timers keep the
box current: one tracks this app repo's `APP_REPO_BRANCH` (code), the other refreshes the
ontology file from `GITHUB_BASE_BRANCH` of the ARI data repo — each restarts the service only
when its target actually changes.

## REST API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v2/overview` | Counts + current version |
| GET | `/api/v2/diseases` | Flat disease list |
| GET | `/api/v2/tree/alphabetical` | Parent→child disease tree |
| GET | `/api/v2/tree/tissue` | UBERON tissue tree with diseases attached |
| GET | `/api/v2/symptoms` | Flat symptom index |
| GET | `/api/v2/schema` | Field schema for every editable data-item category |
| GET | `/api/v2/tissues` | Tissue-target individuals (for new-disease forms) |
| GET | `/api/v2/disease/{iri}` | Full disease detail |
| POST | `/api/v2/disease` | Create a new disease individual |
| PUT | `/api/v2/disease/{iri}` | Edit disease fields (appends changelog) |
| POST | `/api/v2/disease/{iri}/item` | Add a data item |
| PUT | `/api/v2/item/{iri}` | Edit a data item |
| DELETE | `/api/v2/item/{iri}` | Delete a data item |
| GET | `/api/v2/releases` | Current version + release history |
| POST | `/api/v2/releases` | Cut a versioned release |
| GET | `/api/v2/xrefs` | Cross-reference matrix for the review page |
| GET | `/api/v2/xref-databases` | Cross-reference database registry (labels, prefixes, link-outs) |
| GET | `/api/v2/mappings` | Already-curated positive/negative cross-reference judgments |
| GET | `/api/v2/search?q=` | Full-text search |
| GET / POST / PUT / DELETE | `/api/v2/feedback[/{id}]` | Per-term feedback CRUD |
| GET | `/api/v2/open-prs` | Open pull requests, matched to diseases by branch name |
| GET | `/api/v2/me` | Current user + `github_enabled` |
| GET | `/auth/github` | Start OAuth (optional `next`) |
| GET | `/auth/github/callback` | OAuth callback |
| POST | `/api/v2/logout` | Sign out |
| POST | `/api/v2/publish` | Commit working copy + open/append PR (+ SSSOM files) |
| GET | `/api/v2/settings` | Source branch, PR target, allowed branches |
| POST | `/api/v2/fetch` | Pull latest from the source branch |
| POST | `/api/v2/source` | Switch the source branch |
| POST | `/api/v2/pr-base` | Set the PR target branch |
| GET | `/api/v2/export` | Download current state as `1_Core_ARI_Diseases.xlsx` |

## Data sources / provenance

App data derives from the ARI catalogue sources previously maintained in
[`KrishnaTO/ARI`](https://github.com/KrishnaTO/ARI) and from the generated local OWL file in
`ontologies/`. No online data sources are pulled into the content at runtime —
external-database identifiers are rendered as link-outs only.
