"""Keeping a curator's working copy current with the source branch.

``sync`` merges whatever the branch gained since the copy's ancestor; ``resolve``
applies the curator's per-field choices where both sides changed the same thing.
Publish runs the same merge before it commits (see ``publish.py``).

Every list of conflicts names the commit it was computed against (``sha``), and
``resolve`` merges at that commit: the answers are about the values the curator
was shown, not whatever the branch holds by the time they press Apply (#164).
Anything newer arrives through the next sync, three-way like everything else.
"""
import logging

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from .. import config, sessions, workspace
from .. import github_service as gh
from .publish import _baseline_service, _discard

log = logging.getLogger(__name__)

router = APIRouter()


def _signed_in(request):
    if not config.GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    return sessions._user(request)


def _all_diseases(*services) -> set:
    return {d["iri"] for s in services for d in s.get_diseases_list()}


@router.post("/api/v2/sync")
@workspace.one_at_a_time
async def sync(request: Request):
    """Merge the source branch into this curator's working copy."""
    u = _signed_in(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    login = u["identity"]["login"]
    if not (config.USER_DIR / f"{login}.owl").exists():
        return {"up_to_date": True}               # nothing of theirs to bring up to date
    branch = workspace._source_branch(request)
    try:
        sha = await gh.branch_sha(u["token"], config.GH_OWNER, config.GH_REPO, branch)
        if sha == workspace.ancestor_sha(login):
            return {"up_to_date": True}
        theirs = await _baseline_service(request, u, ref=sha)
    except Exception as e:
        log.warning("Could not check %s for updates for @%s: %s", branch, login, e)
        return JSONResponse(status_code=502, content={
            "detail": f"Couldn't check {branch} for updates. Your working copy is unchanged."})
    theirs_bytes = theirs.path.read_bytes()
    try:
        out = workspace.merge_from(login, theirs,
                                   _all_diseases(theirs, workspace.user_service(login)))
    finally:
        _discard(theirs.path)
    if not out["conflicts"]:
        if workspace.ancestor_path(login).exists():
            workspace.set_ancestor_sha(login, sha)
        else:
            # A copy made before ancestors were kept. After a clean merge with no
            # ancestor every single-valued field equals the branch's, so the
            # branch is a valid ancestor from here on.
            workspace.set_ancestor(login, theirs_bytes, sha)
    log.info("Synced @%s with %s@%s: %d merged, %d in conflict", login, branch, sha[:7],
             len(out["merged"]), len(out["conflicts"]))
    return {**out, "sha": sha}


@router.post("/api/v2/resolve")
@workspace.one_at_a_time
async def resolve(request: Request, payload: dict = Body(...)):
    """Apply the curator's choices where both sides changed the same field."""
    u = _signed_in(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    sha = payload.get("sha")
    if not isinstance(sha, str) or not sha:
        return JSONResponse(status_code=400, content={
            "detail": "Name the commit the conflicts were shown against (sha)."})
    choices = payload.get("choices")
    if not isinstance(choices, dict) or not all(
            isinstance(c, dict) and all(v in ("mine", "theirs") for v in c.values())
            for c in choices.values()):
        return JSONResponse(status_code=400, content={
            "detail": "choices must map each disease to {field: 'mine' | 'theirs'}"})
    login = u["identity"]["login"]
    try:
        theirs = await _baseline_service(request, u, ref=sha)
    except Exception as e:
        log.error("Could not read the source branch at %s to resolve for @%s: %s",
                  sha[:7], login, e)
        return JSONResponse(status_code=502, content={
            "detail": "Could not read the source branch. Nothing was changed — try again."})
    try:
        out = workspace.merge_from(login, theirs, set(choices), choices)
    finally:
        _discard(theirs.path)
    if out["conflicts"]:
        return JSONResponse(status_code=409, content={
            "detail": "Some fields still need a choice.", "conflicts": out["conflicts"],
            "sha": sha})
    return {"merged": out["merged"]}
