# REST API

Every endpoint the app serves, grouped by the part of the app that uses it.
Paths are relative to the app root. In production nginx serves the app under
`/ari-editor` and strips that prefix before the request reaches the app.

Request and response bodies are JSON. See [ARCHITECTURE.md](ARCHITECTURE.md) for
what the endpoints do behind the scenes.

## Access

| Label | Meaning |
| --- | --- |
| **open** | No session needed. |
| **signed-in** | A GitHub session, or 401 `Sign in with GitHub …`. |
| **writer** | The write gate (`workspace.service_for(write=True)`): signed-in where GitHub sign-in is configured; anyone on a deployment with GitHub off, where writes go to the base ontology. |
| **admin** | Signed-in and listed in `ASSIGN_ADMINS`. When that list is empty, any signed-in curator counts. |
| **GitHub** | GitHub integration must be configured (`GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_OWNER`, `GITHUB_REPO`) and the caller signed in. |
| **locked** | Holds the caller's per-login lock, so it never overlaps another locked call from the same curator. |

Reads go to the caller's own working copy once they have one, otherwise to the
shared base ontology.

## Errors

| Status | When |
| --- | --- |
| 400 | `Invalid` or `ValueError` from the service layer: a refusal the caller can act on. `detail` says why. This includes a non-admin cutting a release, changing another curator's queue or editing another curator's feedback. |
| 401 | Not signed in where a session is required. |
| 403 | A non-admin calling the PR-reviewer API, or a sign-in by a login not in `ALLOWED_LOGINS`. |
| 404 | `NotFound`: unknown disease, item, feedback entry, category or database. |
| 409 | A merge conflict (`conflicts`, `sha`), a deleted source branch (`missing_branch`), or a PR row that's gone. |
| 502 | GitHub or a terminology service failed or was unreachable. Nothing was written. |
| 503 | `/healthz` cannot load the base ontology; synonym review without a configured repo. |

An unexpected `KeyError` is a 500 with a logged traceback, not a 404.

## Health and pages

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/healthz` | open | `{ok, version, diseases, working_copies, worlds_in_memory, sessions, github_enabled}`; 503 when the base ontology won't load. `deploy/update.sh` reads `version` and `worlds_in_memory`. |
| GET | `/`, `/index.html` | open | The record view. |
| GET | `/ref-edits/` | open | Cross-reference review matrix. |
| GET | `/ref-edits/reviewer/` | open (API is admin) | Mapping-PR reviewer. |
| GET | `/ref-edits/synonyms/` | open | Synonym vs subtype review. |
| GET | `/stats/` | open | Curation dashboard. |

The slash-less forms (`/ref-edits`, `/stats`, …) redirect (308) to the directory
form, because the pages address their assets relatively.

## Sign-in and identity

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/auth/github?next=/path` | open | Start the OAuth flow. `next` must be a same-origin relative path. 404 when GitHub is off. |
| GET | `/auth/github/callback` | open | OAuth callback (path set by `OAUTH_CALLBACK_PATH`). 403 if the login isn't in `ALLOWED_LOGINS`. |
| POST | `/api/v2/logout` | open | Drop the session. |
| GET | `/api/v2/me` | open | `{github_enabled, authenticated, login, name, avatar, repo, base_branch, can_assign_others}`. |
| GET | `/api/v2/open-prs` | open | Open PRs in the data repo (`number, title, url, branch, author, updated_at`); the record view matches them to a disease by branch name. Empty list on failure. |

## Reading the ontology

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v2/overview` | open | Counts, ontology version, `app_version`. |
| GET | `/api/v2/diseases` | open | Flat disease list (`iri, name, local_name, obsolete, synonyms`). |
| GET | `/api/v2/tree/alphabetical` | open | Diseases nested under `hasParentDisease`. |
| GET | `/api/v2/tree/tissue` | open | UBERON tissue tree with diseases attached. |
| GET | `/api/v2/symptoms` | open | Every symptom (`name`, `likelihood`, `obsolete`) with the diseases that carry it, as `{iri, name}`. |
| GET | `/api/v2/schema` | open | The editable item categories and their fields (`app/schema.py`). |
| GET | `/api/v2/tissues` | open | Tissue individuals, for the new-disease form. |
| GET | `/api/v2/disease/{iri}` | open | Full detail of one disease and all its items. |
| GET | `/api/v2/search?q=` | open | Up to 100 matches on name, ARI id (`ARI:0001170`), synonym or target tissue; diseases first. |
| GET | `/api/v2/releases` | open | `{current, releases}`. |

## Editing the ontology

All return the disease's updated detail unless noted.

| Method | Path | Access | Body / purpose |
| --- | --- | --- | --- |
| POST | `/api/v2/disease` | writer | `{data: {label, definition, def_source, tissue_iris, parent_iri?, synonyms?, …}, editor}`. Creates a disease with the next ARI number. |
| PUT | `/api/v2/disease/{iri}` | writer | `{changes: {field: value}, editor}`. Fields that couldn't be stored come back in `rejected: [{field, value, reason}]`. |
| POST | `/api/v2/disease/{iri}/xref` | writer | `{db, op: add\|replace\|remove, value, replaces?, editor}`. One id against what is on file now; `replace` refuses (400) if `replaces` is gone. |
| POST | `/api/v2/disease/{iri}/item` | writer | `{category, values, editor}`. Adds an item (symptom, treatment, …). |
| PUT | `/api/v2/item/{iri}` | writer | `{category, changes, disease, editor}`. |
| DELETE | `/api/v2/item/{iri}` | writer | `{category, disease, editor}`. |
| POST | `/api/v2/releases` | admin | `{version?, notes?}`. Cuts a release and archives non-kept feedback. Returns the release record. |

Field and xref edits also update the id-authorship ledger and park removed ids
as pending negative judgments.

## Cross-reference review

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v2/xref-databases` | open | The database registry: `key, label, prefix, link, search, noframe, review, main_app`. |
| GET | `/api/v2/xrefs` | open | Non-obsolete diseases with their ids per database, definition and synonyms. |
| GET | `/api/v2/mappings` | open | Current judgment per cell (`ari_id, prefix, dbs, id, judgment, author, date`). From the source branch on GitHub when signed in, otherwise from the local `mappings/` files. |
| GET | `/api/v2/predictions` | open | Predicted ids for blank cells (`ari_id, prefix, id, dbs, object_label, match_field, confidence, score, band`). |
| GET | `/api/v2/id-authors` | signed-in | `"<iri>\|<db>\|<id>" -> login`: who added each id. |
| GET | `/api/v2/concept/{db}/{id}` | open | One target id's current record, looked up live at its source and merged with the local index: `found, direct, live, source, label, synonyms, narrow, broad, definition, parents, inactive, facts, via`. 404 for an unknown database, 502 when the source is unreachable. |
| POST | `/api/v2/enrichment-preview` | open | `{confirmed: [{iri, db, ids}]}` → per disease `{synonyms, subtypes}`, each `{value, source}`. Read-only. |
| GET | `/api/v2/ref-session` | open | The caller's saved review session; `{}` when signed out. |
| PUT | `/api/v2/ref-session` | signed-in | `{patch: {reviewed, edited, published}, branch?, pr?}`. Merged key by key; a `null` value clears a key. |

## Review queues

| Method | Path | Access | Body / purpose |
| --- | --- | --- | --- |
| GET | `/api/v2/assignments` | signed-in | Every curator's queue: `login -> {iris, done, note, updated}`. |
| POST | `/api/v2/assignments` | signed-in | `{login?, iris, note?, replace?, reassign?}`. `login` defaults to the caller; another curator's queue needs admin. A disease someone else holds needs `reassign`. |
| DELETE | `/api/v2/assignments` | signed-in | `{login?, iris}`. Same rule for other people's queues. |
| POST | `/api/v2/assignments/done` | signed-in | `{iri, done?}`. Mark one of your diseases finished, or reopen it. |

## Sync, publish and branches

| Method | Path | Access | Body / purpose |
| --- | --- | --- | --- |
| POST | `/api/v2/sync` | GitHub, locked | Merge the source branch into the caller's working copy. Returns `{merged, conflicts, advanced, sha, revision}`, or `{up_to_date: true, revision}`. 409 with `missing_branch` when the branch is gone. |
| POST | `/api/v2/resolve` | GitHub, locked | `{sha, choices: {iri: {"<subject>\|<predicate>": "mine"\|"theirs"}}}`. Re-merges at `sha` with the choices; 409 if any remain. |
| GET | `/api/v2/pending-changes` | open | The caller's changed diseases with per-field differences. The publish dialog lists them with a tick box each and titles the PR from the ones ticked (`static/js/publish-title.js`). Empty when signed out; `unavailable: true` when the branch can't be read. |
| POST | `/api/v2/publish` | GitHub, locked | See below. |
| GET | `/api/v2/settings` | open | `{github_enabled, authenticated, working_branch, source_branch, pr_base, dirty, branches, mapping_license, working_copy}`. |
| POST | `/api/v2/fetch` | GitHub, locked | `{discard?}`. Replace the working copy with the source branch. Returns `{needs_confirm}` first if there are local edits. |
| POST | `/api/v2/source` | GitHub, locked | `{branch, discard?}`. Switch the source branch (`GITHUB_BASE_BRANCH` or an `edit/*` branch) and refetch. |
| POST | `/api/v2/source/follow-base` | GitHub, locked | After the source branch was deleted, follow the base branch and keep the working copy. 400 while the branch still exists. |
| POST | `/api/v2/pr-base` | GitHub | `{branch}`. Set the branch PRs open against. |
| GET | `/api/v2/export` | open | `ARI_current_changes.xlsx` in the core-report format; when signed in, changed cells are marked against the source branch. |

**`POST /api/v2/publish` body:**

| Field | Meaning |
| --- | --- |
| `message` | Commit message and PR title. |
| `disease` | Names the branch slug for a new PR. |
| `exclude` | IRIs of changed diseases to leave out of this PR; they stay in the working copy for a later one. Each must be one the caller changed, and at least one must remain (400 otherwise). |
| `comment` | Curator comment, quoted in the PR body. |
| `confirmed`, `flagged` | `[{iri, ari_id, name, db, ids}]`: verdicts from the review page. The curator's own parked removals are added to `flagged`. A verdict with no `ari_id` is a 400. |
| `absent` | `[{iri, ari_id, name, db}]`: "no term in this database". |
| `apply_enrichment`, `enrichment_selection` | Apply enrichment for the confirmed mappings, optionally only `{iri: {synonyms: [...], subtypes: [...]}}`. |
| `author` | An ORCID for the mapping rows' `author_id`; defaults to `github:<login>`. |
| `branch` | Reuse this branch (and its PR) if it still exists. |
| `labels` | PR labels (default `edit term`; `sssom` is added when there are verdicts). |
| `request_id` | Idempotency key: a repeat returns the first result with `repeated: true`. |

Returns `{branch, pr_number, pr_url, fork, superseded}`. 400 for nothing in
scope or a self-confirmation, 409 with `conflicts` and `sha` when the branch
changed the same fields, 502 when the branch can't be read.

## Feedback

| Method | Path | Access | Body / purpose |
| --- | --- | --- | --- |
| GET | `/api/v2/feedback?disease=<iri>` | open | Feedback entries, oldest first. |
| POST | `/api/v2/feedback` | signed-in | `{disease, term, message, keep}`. The author is the caller. Messages are capped at 4,000 characters. |
| PUT | `/api/v2/feedback/{id}` | author or admin | `{message?, keep?}`. |
| DELETE | `/api/v2/feedback/{id}` | author or admin | Remove an entry. |

## Synonym review and stats

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v2/synonym-review` | open | Report 9, read live from the data repo's base branch: `{source, rows, curation, login}`; each row carries its disease's `iri`. Re-downloaded only when the file's blob id changes. 502 when GitHub refuses; 503 when the repo isn't configured. |
| PUT | `/api/v2/synonym-review/curation` | signed-in | `{row: {ari_id, disease, term, verdict, action, review_date}, status: correct\|incorrect\|needs-review\|"", note}`. Empty status and note clear the row. |
| GET | `/api/v2/stats` | open | The dashboard: `coverage, curators, weeks, waiting, queues`. |

## Mapping-PR reviewer

All admin. GitHub is read with the reviewer's own token.

| Method | Path | Body / purpose |
| --- | --- | --- |
| GET | `/api/v2/pr-review/prs` | Open data-repo PRs that change `mappings/ari.equivalencies.tsv` or `notebook/ari-grounding/target_predictions.json`. |
| GET | `/api/v2/pr-review/prs/{number}` | The comparison matrix, with the PR's shared `marks` and `notes` and `me`. |
| POST | `/api/v2/pr-review/prs/{number}/marks` | `{key, marked}`. |
| POST | `/api/v2/pr-review/prs/{number}/notes` | `{key, text}`; blank text deletes the note. |
| POST | `/api/v2/pr-review/prs/{number}/review` | Post the caller's unposted notes as one COMMENT review. Returns `review_url, on_lines, in_body, not_posted`. |
