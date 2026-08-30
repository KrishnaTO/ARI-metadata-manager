"""The review queue: which diseases each curator holds.

A curator always manages their own queue; handing work to *someone else* is what
the ``ASSIGN_ADMINS`` allow-list gates.
"""
from fastapi import APIRouter, Body, Request

from .. import sessions, stores

router = APIRouter()


def _queue_target(request: Request, target: str) -> str:
    """The curator whose queue a write applies to, defaulting to the caller.

    A curator always manages their own queue; touching someone else's is what
    the ``ASSIGN_ADMINS`` allow-list gates.
    """
    login = sessions._require_login(request)
    target = (target or "").strip() or login
    if target != login and not sessions._can_assign_others(login):
        raise ValueError(f"@{login} may only change their own review queue")
    return target


@router.get("/api/v2/assignments")
async def assignments_all(request: Request):
    """Every curator's assignment record — the "Reassign…" / admin view."""
    sessions._require_login(request)
    return stores.ASSIGNMENTS.assignees()


@router.post("/api/v2/assignments")
async def assignments_set(request: Request, payload: dict = Body(...)):
    """Assign diseases. Body: {login?, iris: [...], note?, replace?, reassign?}.

    ``login`` defaults to the caller — self-assignment needs no privilege. A
    disease another curator already holds is refused unless ``reassign``.
    """
    target = _queue_target(request, payload.get("login", ""))
    return stores.ASSIGNMENTS.assign(
        target, payload.get("iris", []),
        note=payload.get("note", ""), replace=bool(payload.get("replace")),
        reassign=bool(payload.get("reassign")))


@router.delete("/api/v2/assignments")
async def assignments_remove(request: Request, payload: dict = Body(...)):
    """Unassign diseases. Body: {login?, iris: [...]}."""
    target = _queue_target(request, payload.get("login", ""))
    return stores.ASSIGNMENTS.unassign(target, payload.get("iris", []))


@router.post("/api/v2/assignments/done")
async def assignments_done(request: Request, payload: dict = Body(...)):
    """Mark one of my diseases finished (or reopen it). Body: {iri, done?}."""
    login = sessions._require_login(request)
    return stores.ASSIGNMENTS.set_done(login, payload.get("iri", ""),
                                done=payload.get("done", True))
