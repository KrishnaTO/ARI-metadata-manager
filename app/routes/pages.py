"""The two HTML pages.

These are registered before the catch-all StaticFiles mount so they take
precedence and can inject the per-deploy cache-bust token. The mount still
serves the actual js/css/asset files.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config

router = APIRouter()


def _render_html(rel_path: str) -> HTMLResponse:
    """Serve an app HTML page with the `__ASSETV__` asset token substituted.

    The page is marked no-cache so it is always revalidated: it must be fresh to
    carry the current deploy's token, which is what busts the (cacheable) assets."""
    html = (config.STATIC_DIR / rel_path).read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__ASSETV__", config.ASSET_VERSION),
                        headers={"Cache-Control": "no-cache, must-revalidate"})


@router.get("/", include_in_schema=False)
@router.get("/index.html", include_in_schema=False)
async def _index_html():
    return _render_html("index.html")


# The slash-less form redirects rather than renders: the page addresses its own
# assets and sibling pages relatively (the app is mounted under /ari-editor in
# production, so root-absolute paths 404), and relative URLs only resolve correctly
# from the directory form.
@router.get("/stats", include_in_schema=False)
async def _stats_page_slash(request: Request):
    target = request.url.path.rsplit("/", 1)[-1] + "/"
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=308)


@router.get("/stats/", include_in_schema=False)
@router.get("/stats/index.html", include_in_schema=False)
async def _stats_html():
    return _render_html("stats/index.html")


@router.get("/ref-edits", include_in_schema=False)
async def _ref_page_slash(request: Request):
    # A relative Location keeps this correct behind the production prefix, which
    # nginx strips before the app sees the path (see deploy/nginx.conf).
    target = request.url.path.rsplit("/", 1)[-1] + "/"
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=308)


@router.get("/ref-edits/", include_in_schema=False)
@router.get("/ref-edits/index.html", include_in_schema=False)
async def _ref_edits_html():
    return _render_html("ref-edits/index.html")
