"""Curator commentary on terms.

Feedback is attributable: an entry belongs to the curator who wrote it, and only
they (or an admin) may change it.
"""
from fastapi import APIRouter, Body, Request

from .. import sessions, workspace

router = APIRouter()


@router.get("/api/v2/feedback")
async def feedback_list(disease: str = ""):
    return workspace.BASE.feedback.list(disease or None)


def _own_feedback(request: Request, fid: str) -> str:
    """The caller, having checked they may edit this entry.

    Feedback is attributable curator commentary, so an entry is editable by its
    own author or by an admin — never by whoever names themselves in the body.
    """
    login = sessions._require_login(request)
    entry = next((x for x in workspace.BASE.feedback.list() if x.get("id") == fid), None)
    if entry is None:
        raise KeyError(fid)
    if entry.get("author") != login and not sessions._can_assign_others(login):
        raise ValueError(f"@{login} may only change their own feedback")
    return login


@router.post("/api/v2/feedback")
async def feedback_add(request: Request, payload: dict = Body(...)):
    """Add feedback for a term. Body: {disease, term, message, keep}.

    The author is the signed-in curator; it is never taken from the payload."""
    login = sessions._require_login(request)
    return workspace.BASE.feedback.add(
        payload.get("disease", ""), payload.get("term", ""), payload.get("message", ""),
        keep=payload.get("keep", False), author=login)


@router.put("/api/v2/feedback/{fid}")
async def feedback_update(request: Request, fid: str, payload: dict = Body(...)):
    """Edit your own feedback. Body: {message?, keep?}."""
    _own_feedback(request, fid)
    return workspace.BASE.feedback.update(fid, message=payload.get("message"),
                                   keep=payload.get("keep"))


@router.delete("/api/v2/feedback/{fid}")
async def feedback_delete(request: Request, fid: str):
    _own_feedback(request, fid)
    return workspace.BASE.feedback.delete(fid)
