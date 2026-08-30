"""Disease records: reading the ontology and editing it.

The endpoints behind the main curation page — the trees and indexes it renders,
the disease and data-item writes, and the release snapshots cut from them.
"""
from fastapi import APIRouter, Body, Request

from .. import config, sessions, stores, workspace

router = APIRouter()


@router.get("/api/v2/overview")
async def overview(request: Request):
    return {**workspace.service_for(request).overview(), "app_version": config.APP_VERSION}


@router.get("/api/v2/diseases")
async def diseases_list(request: Request):
    return workspace.service_for(request).get_diseases_list()


@router.get("/api/v2/tree/alphabetical")
async def alphabetical_tree(request: Request):
    return workspace.service_for(request).get_alphabetical_tree()


@router.get("/api/v2/tree/tissue")
async def tissue_tree(request: Request):
    return workspace.service_for(request).get_tissue_hierarchy()


@router.get("/api/v2/symptoms")
async def symptoms_index(request: Request):
    return workspace.service_for(request).get_symptoms_index()


@router.get("/api/v2/schema")
async def schema(request: Request):
    """Field schema for editable disease-data item categories."""
    return workspace.service_for(request).get_schema()


@router.get("/api/v2/disease/{iri:path}")
async def disease_detail(request: Request, iri: str):
    return workspace.service_for(request).get_disease_detail(iri)


@router.put("/api/v2/disease/{iri:path}")
async def update_disease(request: Request, iri: str, payload: dict = Body(...)):
    """Edit disease fields. Body: {"changes": {...}, "editor": "name"}."""
    changes = payload.get("changes", payload)
    editor = payload.get("editor", "user")
    svc = workspace.service_for(request, write=True)
    before = svc.get_xrefs(iri)
    r = svc.update_disease(iri, changes, editor=editor)
    # Credit this curator with any cross-reference id the edit introduced, so the
    # review page can stop them confirming their own mapping (separation of duties).
    stores.ID_AUTHORS.record(iri, before, svc.get_xrefs(iri), sessions._login(request))
    workspace._mark_dirty(request)
    workspace._touch(request, iri)
    return r


@router.post("/api/v2/disease/{iri:path}/xref")
async def apply_xref_op(request: Request, iri: str, payload: dict = Body(...)):
    """Apply one cross-reference id change. Body: {db, op, value, replaces}.

    ``op`` is add / replace / remove. The whole point is that the client sends
    the single id rather than the cell's new contents: the list is rebuilt from
    what is on file at the moment of the write, so a second window that added an
    id to the same cell no longer has it silently erased (issue #114).

    Write access is gated by ``service_for(..., write=True)`` like every other
    write endpoint, rather than by a login check here — that would also refuse
    the offline deployments that run with GitHub integration switched off."""
    svc = workspace.service_for(request, write=True)
    before = svc.get_xrefs(iri)
    r = svc.apply_xref_op(iri, payload.get("db", ""), payload.get("op", ""),
                          value=payload.get("value", ""), replaces=payload.get("replaces", ""),
                          editor=payload.get("editor", "user"))
    stores.ID_AUTHORS.record(iri, before, svc.get_xrefs(iri), sessions._login(request))
    workspace._mark_dirty(request)
    workspace._touch(request, iri)
    return r


@router.post("/api/v2/disease/{iri:path}/item")
async def add_item(request: Request, iri: str, payload: dict = Body(...)):
    """Add a data item to a disease. Body: {category, values:{...}, editor}."""
    r = workspace.service_for(request, write=True).add_item(iri, payload["category"], payload.get("values", {}),
                         editor=payload.get("editor", "user"))
    workspace._mark_dirty(request)
    workspace._touch(request, iri)
    return r


@router.put("/api/v2/item/{iri:path}")
async def update_item(request: Request, iri: str, payload: dict = Body(...)):
    """Edit a data item. Body: {category, changes:{...}, disease, editor}."""
    r = workspace.service_for(request, write=True).update_item(iri, payload["category"], payload.get("changes", {}),
                            disease_iri=payload.get("disease", ""),
                            editor=payload.get("editor", "user"))
    workspace._mark_dirty(request)
    workspace._touch(request, payload.get("disease", ""))
    return r


@router.delete("/api/v2/item/{iri:path}")
async def delete_item(request: Request, iri: str, payload: dict = Body(...)):
    """Delete a data item. Body: {category, disease, editor}."""
    r = workspace.service_for(request, write=True).delete_item(iri, payload.get("category", ""),
                            payload["disease"], editor=payload.get("editor", "user"))
    workspace._mark_dirty(request)
    workspace._touch(request, payload.get("disease", ""))
    return r


@router.get("/api/v2/releases")
async def releases_list(request: Request):
    svc = workspace.service_for(request)
    return {"current": svc._current_version(), "releases": svc.list_releases()}


@router.post("/api/v2/releases")
async def create_release(request: Request, payload: dict = Body(default={})):
    """Admin action: cut a versioned release snapshot of the ontology.

    Cutting a release also archives every non-kept feedback entry, so this is
    gated on ``ASSIGN_ADMINS`` rather than on merely being signed in."""
    login = sessions._require_login(request)
    if not sessions._can_assign_others(login):
        raise ValueError(f"@{login} is not an administrator; releases are cut by "
                         f"{', '.join('@' + a for a in config.ASSIGN_ADMINS)}")
    version = payload.get("version", "")
    notes = payload.get("notes", "")
    return workspace.service_for(request, write=True).create_release(version=version, notes=notes, editor=login)


@router.get("/api/v2/tissues")
async def tissues_list(request: Request):
    """All tissue-target individuals for new-disease creation forms."""
    return workspace.service_for(request).get_tissues()


@router.post("/api/v2/disease")
async def create_disease(request: Request, payload: dict = Body(...)):
    """Create a new disease individual. Body: {data: {...}, editor: str}."""
    data = payload.get("data", {})
    editor = payload.get("editor", "user")
    svc = workspace.service_for(request, write=True)
    r = svc.create_disease(data, editor=editor)
    stores.ID_AUTHORS.record(r["iri"], {}, svc.get_xrefs(r["iri"]), sessions._login(request))
    workspace._mark_dirty(request)
    # The parent gains a changelog entry naming the subtype, so it is part of this
    # session's work too — without this the pull request describes the child and
    # says nothing about the record it was split out of (issue #24).
    workspace._touch(request, r["iri"], data.get("parent_iri"))
    return r


@router.get("/api/v2/search")
async def search(request: Request, q: str = ""):
    return workspace.service_for(request).search(q)
