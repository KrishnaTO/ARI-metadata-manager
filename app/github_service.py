"""Per-user GitHub integration for the Metadata Manager.

Each editor signs in with their own GitHub account; their access token is held
server-side only (in the session) and used to commit the edited ontology file
on a new branch — named after the disease — and open a pull request. Commits
are therefore attributed to the editor on GitHub. The only persistent secret is
the OAuth App client secret, which never leaves the server.
"""
import asyncio
import base64
import logging
import re
import time
import httpx

GH = "https://github.com"
API = "https://api.github.com"

log = logging.getLogger(__name__)


def _is_fork_of(response, owner: str, repo: str) -> bool:
    """Whether ``response`` describes the user's fork of ``owner/repo``.

    Matching on the repository *name* alone is not enough — plenty of people own
    an unrelated repo called the same thing."""
    if response.status_code != 200:
        return False
    j = response.json()
    parent = (j.get("parent") or {}).get("full_name", "")
    return bool(j.get("fork")) and parent.lower() == f"{owner}/{repo}".lower()


# GitHub failures a retry can actually clear: a secondary rate limit, an abuse
# throttle, or a transient 5xx. Anything else is a real answer and is raised.
_RETRYABLE = {403, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4


async def _get(client, url, **kw):
    """GET with exponential backoff, honouring Retry-After.

    Reads are idempotent, so retrying one is always safe. A secondary rate limit
    used to surface as `Publish failed: You have exceeded a secondary rate
    limit` in an alert(), with no retry and nothing the curator could do.
    """
    for attempt in range(_MAX_ATTEMPTS):
        r = await client.get(url, **kw)
        if r.status_code not in _RETRYABLE or attempt == _MAX_ATTEMPTS - 1:
            return r
        delay = float(r.headers.get("Retry-After") or 2 ** attempt)
        log.warning("GitHub %s on %s; retrying in %.0fs (attempt %d/%d)",
                    r.status_code, url, delay, attempt + 1, _MAX_ATTEMPTS)
        await asyncio.sleep(min(delay, 30))
    return r


def explain(status: int, message: str) -> str:
    """A GitHub failure as a sentence a curator can act on.

    The raw API message reached an `alert()` unchanged, which for the common
    failures said nothing about what to do next.
    """
    m = (message or "").lower()
    if "secondary rate limit" in m or status == 429:
        return ("GitHub is rate-limiting this account. Your work is saved — wait a "
                "minute and publish again.")
    if status == 401:
        return "Your GitHub sign-in has expired. Sign out and back in, then publish again."
    if status == 403 and "rate limit" in m:
        return ("GitHub's hourly API limit for this account is used up. Your work is "
                "saved — try again after the hour turns.")
    if status == 403:
        return ("GitHub refused the change — you may not have write access to this "
                "repository. Your work is saved.")
    if status == 404:
        return ("GitHub could not find the repository or branch this publishes to. "
                "Your work is saved; tell an administrator.")
    if status in (500, 502, 503, 504):
        return "GitHub is having trouble right now. Your work is saved — try again shortly."
    return message or f"GitHub returned {status}."


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "disease")[:60]


# The app forks one public repository and pushes one branch to it. `repo` — the
# scope this used to request — is read and write over every repository the
# curator can reach, including their employer's private ones. `public_repo` is
# the narrowest scope that still covers fork + commit + pull request.
OAUTH_SCOPE = "public_repo user:email"


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode
    return f"{GH}/login/oauth/authorize?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "scope": OAUTH_SCOPE, "state": state, "allow_signup": "false",
    })


async def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> str:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{GH}/login/oauth/access_token",
                         headers={"Accept": "application/json"},
                         json={"client_id": client_id, "client_secret": client_secret,
                               "code": code, "redirect_uri": redirect_uri})
    data = r.json()
    if "access_token" not in data:
        raise ValueError(f"OAuth token exchange failed: {data}")
    return data["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


async def get_identity(token: str) -> dict:
    async with httpx.AsyncClient(timeout=20, headers=_headers(token)) as c:
        user = (await c.get(f"{API}/user")).json()
        email = user.get("email")
        try:
            emails = (await c.get(f"{API}/user/emails")).json()
            verified = [e for e in emails if e.get("verified")]
            primary = next((e for e in verified if e.get("primary")), verified[0] if verified else None)
            if primary:
                email = primary["email"]
        except Exception as e:
            log.debug("Could not read verified emails (user:email scope may be absent): %s", e)
    if not email:
        email = f"{user['id']}+{user['login']}@users.noreply.github.com"
    return {"login": user["login"], "name": user.get("name") or user["login"],
            "email": email, "avatar": user.get("avatar_url", "")}


async def _catch_up(client, c_owner: str, c_repo: str, branch: str,
                    base_branch: str, base_sha: str) -> None:
    """Merge ``base_sha`` into ``branch`` when upstream has moved on.

    Refuses on a conflict rather than guessing: a curator has to be told, and
    resolving someone's ontology merge silently is not something to attempt.
    """
    r = await client.post(f"{API}/repos/{c_owner}/{c_repo}/merges", json={
        "base": branch, "head": base_sha,
        "commit_message": f"Merge {base_branch} into {branch}",
    })
    if r.status_code == 204:
        return                                  # already contains the base
    if r.status_code == 409:
        raise ValueError(
            f"'{branch}' conflicts with {base_branch}, which has moved since this "
            f"review started. Start a new submission — your verdicts are saved.")
    if r.status_code >= 300:
        raise ValueError(explain(r.status_code, (r.json() or {}).get("message", "")))
    log.info("Merged %s into %s before committing", base_branch, branch)


async def publish_file(*, token: str, owner: str, repo: str, base_branch: str, pr_body: str = "",
                       extra_files: dict = None, path: str, content_bytes: bytes,
                       disease_name: str, message: str, identity: dict,
                       reuse_branch: str = None, labels: list = None) -> dict:
    """Commit content_bytes (+ extra_files) to a branch and open/update a PR.

    Collaborators with push access commit a branch directly in the upstream repo.
    Outside contributors (no push access) get their own fork created automatically;
    the branch + commits go onto their fork and a cross-repo PR is opened upstream.
    If reuse_branch exists, further changes append to the same branch/PR.
    """
    labels = labels or ["edit term"]
    label_colors = {"edit term": "0e8a16", "sssom": "5319e7"}

    def _author():
        return {"name": identity["name"], "email": identity["email"]}

    async with httpx.AsyncClient(timeout=30, headers=_headers(token)) as c:
        # Where can this user push? Upstream if collaborator, else their fork.
        info = await _get(c, f"{API}/repos/{owner}/{repo}")
        can_push = bool(info.json().get("permissions", {}).get("push")) if info.status_code == 200 else False

        if can_push:
            c_owner, c_repo = owner, repo
        else:
            login = identity["login"]
            fk = await _get(c, f"{API}/repos/{login}/{repo}")
            if not _is_fork_of(fk, owner, repo):
                # A 200 here used to be taken as "the fork exists", so a curator
                # who happened to own an unrelated repository of the same name
                # got their branch and commits pushed into it.
                if fk.status_code == 200:
                    raise ValueError(
                        f"You already own a repository called {login}/{repo}, and it is not a "
                        f"fork of {owner}/{repo}. Rename it, or ask for push access to the "
                        f"main repository, and try again.")
                log.info("Creating @%s's fork of %s/%s — GitHub takes about 30 "
                         "seconds the first time", login, owner, repo)
                cr = await c.post(f"{API}/repos/{owner}/{repo}/forks")
                if cr.status_code >= 300:
                    raise ValueError(f"Could not fork {owner}/{repo}: {cr.json().get('message')}")
                for _ in range(15):                       # forks are async; wait for it
                    await asyncio.sleep(2)
                    fk = await _get(c, f"{API}/repos/{login}/{repo}")
                    if _is_fork_of(fk, owner, repo):
                        break
                else:
                    raise ValueError("Your fork is still being created — try publishing again in a moment.")
            fj = fk.json()
            c_owner, c_repo = fj["owner"]["login"], fj["name"]

        # Base sha always comes from UPSTREAM so the branch is current even on a stale fork.
        base = (await _get(c, f"{API}/repos/{owner}/{repo}/git/ref/heads/{base_branch}")).json()
        if "object" not in base:
            raise ValueError(f"Base branch '{base_branch}' not found: {base.get('message')}")
        base_sha = base["object"]["sha"]

        branch = None
        if reuse_branch:
            ref = await _get(c, f"{API}/repos/{c_owner}/{c_repo}/git/ref/heads/{reuse_branch}")
            if ref.status_code == 200 and "object" in ref.json():
                branch = reuse_branch
                # base_sha was only ever used to *create* a branch, so a reused
                # one was built on its own tip however far upstream had moved.
                # A long review session then produced a PR whose diff carried
                # unrelated drift. Merge the base in first.
                await _catch_up(c, c_owner, c_repo, branch, base_branch, base_sha)
        if not branch:
            branch = f"edit/{identity['login']}/{slugify(disease_name)}-{int(time.time())}"
            r = await c.post(f"{API}/repos/{c_owner}/{c_repo}/git/refs",
                             json={"ref": f"refs/heads/{branch}", "sha": base_sha})
            if r.status_code >= 300:
                raise ValueError(explain(r.status_code, r.json().get("message", "")))
        head_label = f"{c_owner}:{branch}"

        # Commit the ontology + any extra files (mapping tables) as a single
        # commit via the git data API, rather than one PUT /contents per file.
        files = {path: content_bytes, **(extra_files or {})}

        tip = await _get(c, f"{API}/repos/{c_owner}/{c_repo}/git/ref/heads/{branch}")
        if tip.status_code != 200 or "object" not in tip.json():
            raise ValueError(f"Could not read branch tip: {tip.json().get('message')}")
        parent_sha = tip.json()["object"]["sha"]

        parent_commit = (await _get(c, f"{API}/repos/{c_owner}/{c_repo}/git/commits/{parent_sha}")).json()
        base_tree_sha = parent_commit["tree"]["sha"]

        tree_entries = []
        for fpath, fbytes in files.items():
            blob = await c.post(f"{API}/repos/{c_owner}/{c_repo}/git/blobs", json={
                "content": base64.b64encode(fbytes).decode(), "encoding": "base64",
            })
            if blob.status_code >= 300:
                raise ValueError(explain(blob.status_code, blob.json().get("message", "")))
            tree_entries.append({"path": fpath, "mode": "100644", "type": "blob", "sha": blob.json()["sha"]})

        tree = await c.post(f"{API}/repos/{c_owner}/{c_repo}/git/trees", json={
            "base_tree": base_tree_sha, "tree": tree_entries,
        })
        if tree.status_code >= 300:
            raise ValueError(explain(tree.status_code, tree.json().get("message", "")))

        commit = await c.post(f"{API}/repos/{c_owner}/{c_repo}/git/commits", json={
            "message": message or f"Update {disease_name}",
            "tree": tree.json()["sha"], "parents": [parent_sha],
            "author": _author(), "committer": _author(),
        })
        if commit.status_code >= 300:
            raise ValueError(explain(commit.status_code, commit.json().get("message", "")))

        upd = await c.patch(f"{API}/repos/{c_owner}/{c_repo}/git/refs/heads/{branch}", json={
            "sha": commit.json()["sha"],
        })
        if upd.status_code >= 300:
            raise ValueError(explain(upd.status_code, upd.json().get("message", "")))

        # PR is always opened/looked up on UPSTREAM; head may be a fork (owner:branch).
        # `state: all`, not `open`: a tracked PR that had been merged or closed
        # was invisible here, so a *new* PR was opened while the client's
        # sessionPr still pointed at the old number and the header still read
        # "Publish to PR #N" — the curator could not tell where their work went.
        found = await _get(c, f"{API}/repos/{owner}/{repo}/pulls",
                           params={"head": head_label, "state": "all"})
        all_prs = found.json() if found.status_code == 200 else []
        prs = [p for p in all_prs if p.get("state") == "open"]
        closed = [p for p in all_prs if p.get("state") != "open"]
        if prs:
            prj = prs[0]
            if message:
                await c.patch(f"{API}/repos/{owner}/{repo}/pulls/{prj['number']}", json={"title": message})
        else:
            pr = await c.post(f"{API}/repos/{owner}/{repo}/pulls", json={
                "title": message or f"Edit {disease_name}", "head": head_label, "base": base_branch,
                "maintainer_can_modify": True,
                "body": pr_body or f"Edit to **{disease_name}** by @{identity['login']}.",
            })
            if pr.status_code >= 300:
                raise ValueError(explain(pr.status_code, pr.json().get("message", "")))
            prj = pr.json()

        # apply labels on the upstream PR (best effort; outside contributors can't label)
        try:
            for name in labels:
                await c.post(f"{API}/repos/{owner}/{repo}/labels",
                             json={"name": name, "color": label_colors.get(name, "ededed")})
            await c.post(f"{API}/repos/{owner}/{repo}/issues/{prj['number']}/labels",
                         json={"labels": labels})
        except Exception as e:
            log.debug("Could not apply PR labels (best-effort; outside contributors cannot label): %s", e)
    # `superseded` names a merged/closed PR this branch used to feed, so the
    # client can replace a stale pointer instead of silently tracking the wrong one.
    superseded = ([{"number": p["number"], "url": p["html_url"],
                    "merged": bool(p.get("merged_at"))} for p in closed]
                  if not prs and closed else [])
    return {"branch": branch, "pr_number": prj["number"], "pr_url": prj["html_url"],
            "fork": (not can_push), "superseded": superseded}


async def list_branches(token: str | None, owner: str, repo: str) -> list[str]:
    """All branch names in the repo (token optional for public repos)."""
    hdrs = {"Accept": "application/vnd.github+json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    out, page = [], 1
    async with httpx.AsyncClient(timeout=20, headers=hdrs) as c:
        while True:
            r = await c.get(f"{API}/repos/{owner}/{repo}/branches",
                            params={"per_page": 100, "page": page})
            if r.status_code >= 300:
                raise ValueError(f"Could not list branches: {r.json().get('message')}")
            batch = r.json()
            out += [b["name"] for b in batch]
            if len(batch) < 100:
                break
            page += 1
    return out


async def get_file_at(token: str | None, owner: str, repo: str, path: str, ref: str) -> bytes:
    """Raw bytes of `path` on `ref` (token optional for public repos)."""
    hdrs = {"Accept": "application/vnd.github.raw+json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=30, headers=hdrs) as c:
        r = await c.get(f"{API}/repos/{owner}/{repo}/contents/{path}", params={"ref": ref})
        if r.status_code >= 300:
            raise ValueError(f"Could not fetch {path}@{ref}: {r.status_code} {r.text[:200]}")
        return r.content


async def list_open_prs(token: str | None, owner: str, repo: str) -> list[dict]:
    """Open pull requests in the repo (token optional for public repos).

    Returns a compact record per PR — number, title, url, head branch, author and
    updated_at — used to surface unreviewed edits on the matching disease page.
    """
    hdrs = {"Accept": "application/vnd.github+json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    out, page = [], 1
    async with httpx.AsyncClient(timeout=20, headers=hdrs) as c:
        while True:
            r = await c.get(f"{API}/repos/{owner}/{repo}/pulls",
                            params={"state": "open", "per_page": 100, "page": page})
            if r.status_code >= 300:
                raise ValueError(f"Could not list pull requests: {r.status_code} {r.text[:200]}")
            batch = r.json()
            for pr in batch:
                out.append({
                    "number": pr.get("number"),
                    "title": pr.get("title") or "",
                    "url": pr.get("html_url") or "",
                    "branch": (pr.get("head") or {}).get("ref") or "",
                    "author": (pr.get("user") or {}).get("login") or "",
                    "updated_at": pr.get("updated_at") or "",
                })
            if len(batch) < 100:
                break
            page += 1
    return out
