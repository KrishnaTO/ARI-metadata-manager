"""The cross-reference review page.

Everything the ``/ref-edits`` matrix reads: the grid itself, the database
registry behind its columns, the judgments already curated, the predictions and
concept lookups it offers, and the per-curator session that lets a review resume
after a reload.
"""
import logging

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import (
    config,
    predict_service,
    sessions,
    sssom_service,
    stats_service,
    stores,
    synonym_review_store,
    workspace,
    xref_registry,
)
from .. import github_service as gh
from .. import synonym_review as synonym_review_report
from ..errors import NotFound
from ..pr_review import compare, terminology

log = logging.getLogger(__name__)

router = APIRouter()


async def _mapping_judgments(request: Request) -> list:
    """Already-curated positive/negative judgments, from GitHub when signed in."""
    sssom_text = equiv_text = ""
    u = sessions._user(request) if config.GH_ENABLED else None
    if u:
        async def _read(path):
            try:
                blob = await gh.get_file_at(u["token"], config.GH_OWNER, config.GH_REPO, path,
                                            workspace._source_branch(request))
                return blob.decode("utf-8")
            except Exception as e:
                log.debug("Could not read %s@%s from GitHub, falling back to local: %s",
                          path, workspace._source_branch(request), e)
                return ""
        sssom_text = await _read(config.MAPPINGS_SSSOM_PATH)
        equiv_text = await _read(config.MAPPINGS_EQUIV_PATH)
    if not sssom_text and not equiv_text:
        for p, is_sssom in ((config.MAPPINGS_SSSOM_PATH, True), (config.MAPPINGS_EQUIV_PATH, False)):
            try:
                txt = (config.ROOT / p).read_text(encoding="utf-8")
            except OSError as e:
                log.debug("Could not read local mapping file %s: %s", p, e)
                txt = ""
            if is_sssom:
                sssom_text = txt
            else:
                equiv_text = txt
    return sssom_service.load_judgments(sssom_text, equiv_text)


@router.get("/api/v2/xrefs")
async def xrefs(request: Request):
    """Non-obsolete diseases with their database cross-references, for the
    reference-review page. Obsolete terms aren't curated, so they're left out."""
    return [r for r in workspace.service_for(request).get_xref_rows() if not r["obsolete"]]


@router.get("/api/v2/ref-session")
async def get_ref_session(request: Request):
    """The signed-in user's saved cross-reference review session (verdicts,
    edited-id markers and the PR pointer), so work resumes across page reloads.
    Empty for anonymous users — review state is only persisted when signed in."""
    login = sessions._login(request)
    return workspace._load_ref_session(login) if login else {}


@router.put("/api/v2/ref-session")
async def put_ref_session(request: Request, payload: dict = Body(...)):
    """Merge one window's changes into the signed-in user's review session.

    Body: ``{patch: {reviewed, edited, published}, branch, pr}``, where a patch
    value of ``null`` clears that key. Comparing two records side by side is the
    product's core loop and takes two windows, so these writes have to merge
    rather than overwrite — see ``workspace._merge_ref_session``."""
    login = sessions._login(request)
    if not login:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    workspace._merge_ref_session(login, payload.get("patch") or {},
                                 payload.get("branch"), payload.get("pr"))
    return {"ok": True}


@router.get("/api/v2/stats")
async def stats(request: Request):
    """What the curation effort has produced, from the stores already on disk.

    The only trace of curation used to be a gitignored log on one host, so
    nothing could say how much each curator confirms, which databases stall, or
    how much work is waiting for its second reviewer (issue #124). Read-only and
    offline: no network call, so the dashboard loads instantly."""
    svc = workspace.service_for(request)
    return stats_service.build(svc.get_xref_rows(), await _mapping_judgments(request),
                               stores.ID_AUTHORS.authors(), stores.ASSIGNMENTS.assignees())


@router.get("/api/v2/synonym-review")
async def synonym_review(request: Request):
    """Report 9 from the ARI repo's base branch, read live so the page always shows
    the report as last committed. Uses the curator's token when signed in (the
    repo is public, so anonymous reads work within GitHub's unauthenticated limit)."""
    if not (config.GH_OWNER and config.GH_REPO):
        raise HTTPException(status_code=503, detail="GITHUB_OWNER and GITHUB_REPO are not configured")
    u = sessions._user(request) if config.GH_ENABLED else None
    try:
        blob = await gh.get_file_at(u["token"] if u else None, config.GH_OWNER, config.GH_REPO,
                                    config.SYNONYM_REVIEW_PATH, config.GH_BASE_BRANCH)
    except (ValueError, httpx.HTTPError) as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return {"source": f"{config.GH_OWNER}/{config.GH_REPO}@{config.GH_BASE_BRANCH}:"
                      f"{config.SYNONYM_REVIEW_PATH}",
            "rows": synonym_review_report.parse(blob.decode("utf-8")),
            "curation": synonym_review_store.read(),
            "login": sessions._login(request)}


@router.put("/api/v2/synonym-review/curation")
async def save_synonym_curation(request: Request, payload: dict = Body(...)):
    """Mark one report-9 row correct, incorrect or needs-review, with an optional note.

    Body: ``{row: {ari_id, disease, term, verdict, action, review_date}, status, note}``.
    Empty status and note clear the row. Shared by every curator; signed-in only, so
    each entry records who judged it."""
    login = sessions._require_login(request)
    row = payload.get("row") or {}
    missing = [f for f in synonym_review_store.ROW_FIELDS if not row.get(f)]
    if missing:
        raise HTTPException(status_code=400, detail=f"row is missing {', '.join(missing)}")
    try:
        entry = synonym_review_store.save(row, payload.get("status") or "",
                                          payload.get("note") or "", login)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {"key": synonym_review_store.key(row), "entry": entry}


@router.get("/api/v2/xref-databases")
async def xref_databases():
    """Cross-reference database registry (labels, CURIE prefixes, link-out URL
    templates). The single source both frontend pages build their columns and
    link-outs from, kept in step with the SSSOM prefixes on the server."""
    return xref_registry.public_list()


@router.get("/api/v2/mappings")
async def mappings(request: Request):
    """Already-curated positive/negative cross-reference judgments.

    Read from the accumulated SSSOM (falling back to the equivalencies file) so
    the review page can pre-highlight cells that were confirmed or flagged in an
    earlier session. When signed in, the files are read from the current source
    branch on GitHub; otherwise the local working-tree copy (if any) is used.
    """
    return await _mapping_judgments(request)


@router.get("/api/v2/id-authors")
async def id_authors(request: Request):
    """Which curator added each cross-reference id: ``"<iri>|<db>|<id>" -> login``.

    The review page uses this to separate duties — the curator who added an id
    may not also confirm the mapping it stands for. It is the evidence base for
    that boundary, so it needs a session."""
    sessions._require_login(request)
    return stores.ID_AUTHORS.authors()


@router.get("/api/v2/predictions")
async def predictions(request: Request):
    """Predicted cross-references for blank review-grid cells (issue #42).

    Exact name/synonym matches against the downloaded reference-database indexes
    (``data/2-databases``); the review page shows these as yellow "predicted" cells
    the curator can verify and confirm. Empty list when no indexes are present."""
    return workspace.service_for(request).predict_xrefs()


@router.post("/api/v2/enrichment-preview")
async def enrichment_preview(request: Request, payload: dict = Body(default={})):
    """Synonyms + clinical subtypes a set of confirmed cross-references would add.

    Given a review session's confirmed (positive) mappings
    (``confirmed: [{iri, db, ids}]``), return, per disease iri, the *new*
    ``{synonyms, subtypes}`` the enrichment engine would fold in on publish — the
    matched terms' own synonyms and their direct children. Read-only preview of the
    ``apply_enrichment`` publish step; empty when nothing new is proposed."""
    confirmed = payload.get("confirmed") or []
    return workspace.service_for(request).enrichment_preview(confirmed)


@router.get("/api/v2/concept/{db}/{obj_id:path}")
def concept_detail_lookup(db: str, obj_id: str):
    """What one target-database id is, from that database's own current record.

    Backs the right-hand side of the reference-review compare pane: the curator sees
    what a candidate concept actually is, next to the ARI disease. The term is looked up
    live at its source (``pr_review.terminology``: tx.fhir.org, OHDSI, EBI OLS, NLM MeSH,
    NCBI MedGen), merged with the local index copy as the PR reviewer does
    (``compare.target_views``). ``live: false`` means the source has no such code and the
    answer is the downloaded snapshot's; ``direct: false`` means only hub terms that
    cross-reference the id know it (``via``). ``{obj_id:path}`` because ids carry colons
    (and dots, for ICD-10). An unknown ``db`` is a 404; an unreachable source is a 502,
    not an empty answer."""
    if db not in xref_registry.BY_KEY:
        raise NotFound(f"unknown database {db!r}")
    source = terminology.SOURCES.get(db) or xref_registry.BY_KEY[db]["label"]
    ident = xref_registry.normalize_id(db, obj_id)
    try:
        views = compare.target_views(db, ident, predict_service.get_indexes()) if ident else []
    except httpx.HTTPError as err:
        host = err.request.url.host if err.request else source
        raise HTTPException(status_code=502, detail=f"{host} failed: {err}") from err
    own = next((v for v in views if v["direct"]), None)
    via = [{"source": v["source"], "id": v["id"], "label": v["label"]}
           for v in views if not v["direct"]]
    out = {"found": bool(views), "direct": own is not None, "source": source,
           "live": own is not None and own["source"] == source, "label": "",
           "synonyms": [], "narrow": [], "broad": [], "definition": "", "parents": [],
           "inactive": False, "facts": [], "via": via}
    if own is not None:
        out.update({k: own[k] for k in ("label", "synonyms", "narrow", "broad", "definition",
                                        "parents", "inactive", "facts")})
    elif via:
        # A hub's synonyms and definition describe the hub concept, not this id.
        out["label"] = via[0]["label"]
    return out
