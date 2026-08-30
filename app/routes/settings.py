"""Which branch a curator works from, and the spreadsheet export.

Reading the app's current settings, pointing a curator at a different source
branch (which refetches their working copy), choosing the branch their pull
requests open against, and exporting the current data as .xlsx.
"""
import logging

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import atomic_store, config, export_service, sessions, sssom_service, workspace
from .. import github_service as gh
from ..ontology_service import OntologyService

log = logging.getLogger(__name__)

router = APIRouter()


def _allowed_branches(branches):
    """Only the working branch and edit/* branches are selectable."""
    return [b for b in branches if b == config.GH_BASE_BRANCH or b.startswith("edit/")]


async def _fetch_branch(token, branch, login):
    """Point ``login`` at ``branch``, fetching it into their own working copy.

    This used to write the downloaded bytes over ``ontologies/ari_t1d.owl`` —
    the git-tracked file — so one curator switching branch replaced what every
    other curator and every anonymous reader saw, and left `deploy/update.sh`
    finding a dirty tree (and autostashing) every ten minutes forever.
    """
    data = await gh.get_file_at(token, config.GH_OWNER, config.GH_REPO,
                                config.GH_ONTOLOGY_PATH, branch)
    workspace._reset_user(login)       # verdicts and edits reference the old base
    config.USER_DIR.mkdir(parents=True, exist_ok=True)
    atomic_store.write_bytes(config.USER_DIR / f"{login}.owl", data, mode=0o644)
    workspace.USER_SVC.pop(login, None)   # reload on next use, from the file just written
    workspace._set_branch_state(login, source_branch=branch, pr_base=branch)


@router.get("/api/v2/settings")
async def get_settings(request: Request):
    u = sessions._user(request)
    token = u["token"] if u else None
    branches = []
    if config.GH_ENABLED:
        try:
            branches = _allowed_branches(await gh.list_branches(token, config.GH_OWNER, config.GH_REPO))
        except Exception as e:
            log.warning("Could not list branches from GitHub: %s", e)
            branches = [config.GH_BASE_BRANCH]
    return {"github_enabled": config.GH_ENABLED, "authenticated": bool(u),
            "working_branch": config.GH_BASE_BRANCH, "source_branch": workspace._source_branch(request),
            "pr_base": workspace._pr_base(request), "dirty": workspace._dirty(request), "branches": branches,
            "mapping_license": sssom_service.MAPPING_LICENSE,
            "working_copy": workspace.working_copy_expiry(sessions._login(request))}


@router.post("/api/v2/fetch")
async def fetch_changes(request: Request, payload: dict = Body(default={})):
    """Pull the latest of the current source branch into the app."""
    if not config.GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    u = sessions._user(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    if workspace._dirty(request) and not payload.get("discard"):
        return {"needs_confirm": True,
                "detail": "Local edits exist and will be discarded by fetching."}
    branch = workspace._source_branch(request)
    await _fetch_branch(u["token"], branch, u["identity"]["login"])
    return {"ok": True, "source_branch": branch}


@router.post("/api/v2/source")
async def set_source(request: Request, payload: dict = Body(...)):
    """Switch which branch the app populates from (working or any edit/* branch)."""
    if not config.GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    u = sessions._user(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    branch = payload.get("branch", "")
    allowed = _allowed_branches(await gh.list_branches(u["token"], config.GH_OWNER, config.GH_REPO))
    if branch not in allowed:
        raise ValueError(f"Branch not allowed: {branch}")
    if workspace._dirty(request) and not payload.get("discard"):
        return {"needs_confirm": True,
                "detail": "Local edits exist and will be discarded by switching branch."}
    await _fetch_branch(u["token"], branch, u["identity"]["login"])
    return {"ok": True, "source_branch": branch}


@router.post("/api/v2/pr-base")
async def set_pr_base(request: Request, payload: dict = Body(...)):
    """Set the branch that edits open PRs against."""
    if not config.GH_ENABLED:
        raise ValueError("GitHub integration is not configured")
    u = sessions._user(request)
    if not u:
        return JSONResponse(status_code=401, content={"detail": "Sign in with GitHub first"})
    branch = payload.get("branch", "")
    allowed = _allowed_branches(await gh.list_branches(u["token"], config.GH_OWNER, config.GH_REPO))
    if branch not in allowed:
        raise ValueError(f"Branch not allowed: {branch}")
    workspace._set_branch_state(sessions._login(request), pr_base=branch)
    return {"ok": True, "pr_base": branch}


@router.get("/api/v2/export")
async def export_excel(request: Request):
    """Export current data to an .xlsx in the core-report format. When signed in,
    changed cells are marked against the source branch (the baseline)."""
    import io as _io
    import os as _os
    import tempfile
    baseline = None
    u = sessions._user(request)
    if config.GH_ENABLED and u:
        tmp_name = None
        try:
            data = await gh.get_file_at(u["token"], config.GH_OWNER, config.GH_REPO,
                                        config.GH_ONTOLOGY_PATH, workspace._source_branch(request))
            tmp = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
            tmp.write(data); tmp.close(); tmp_name = tmp.name
            baseline = OntologyService(tmp_name)
        except Exception as e:
            log.warning("Could not load export baseline from source branch %s: %s",
                        workspace._source_branch(request), e)
            baseline = None
        finally:
            # Always remove the temp file, even if OntologyService() raised after
            # it was created (owlready2 loads into memory, so the file isn't needed).
            if tmp_name:
                try:
                    _os.unlink(tmp_name)
                except OSError as e:
                    log.debug("Could not remove temp baseline %s: %s", tmp_name, e)
    xlsx = export_service.build_report(workspace.service_for(request), baseline)
    return StreamingResponse(
        _io.BytesIO(xlsx),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="ARI_current_changes.xlsx"'})
