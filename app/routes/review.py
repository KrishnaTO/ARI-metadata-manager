"""The cross-reference review page.

Everything the ``/ref-edits`` matrix reads: the grid itself, the database
registry behind its columns, the judgments already curated, the predictions and
concept lookups it offers, and the per-curator session that lets a review resume
after a reload.
"""
import logging

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from .. import concept_service, config, predict_service, sessions, sssom_service, stores, workspace, xref_registry
from .. import github_service as gh

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
    """All diseases with their database cross-references, for the reference-review page."""
    return workspace.service_for(request).get_xref_rows()


@router.get("/api/v2/ref-session")
async def get_ref_session(request: Request):
    """The signed-in user's saved cross-reference review session (verdicts,
    edited-id markers and the PR pointer), so work resumes across page reloads.
    Empty for anonymous users — review state is only persisted when signed in."""
    login = sessions._login(request)
    return workspace._load_ref_session(login) if login else {}


@router.put("/api/v2/ref-session")
async def put_ref_session(request: Request, payload: dict = Body(...)):
    """Persist the signed-in user's cross-reference review session. The body is
    the frontend-owned state blob ({reviewed, edited, published, branch, pr})."""
    login = sessions._login(request)
    if not login:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    workspace._save_ref_session(login, payload)
    return {"ok": True}


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
async def concept_detail_lookup(db: str, obj_id: str):
    """Label, synonyms, definition and parents for one target-database id.

    Backs the right-hand side of the reference-review compare pane: the curator sees
    what a candidate concept actually is, next to the ARI disease. ``{obj_id:path}``
    because ids carry colons (and dots, for ICD-10). Distinguishes a database's own
    term (``direct: true``) from a hub cross-reference (``direct: false`` + ``via``);
    a valid-but-unindexed id is a normal ``found: false`` 200, not a 404. Reads the
    public index files only — no auth, same as ``/api/v2/predictions``. An unknown
    ``db`` raises ``KeyError`` -> 404 via the existing handler."""
    return concept_service.lookup(db, obj_id, predict_service.get_indexes())
