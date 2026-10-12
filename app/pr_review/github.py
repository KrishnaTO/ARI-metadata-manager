"""Access to the mapping repository's pull requests, as the signed-in reviewer.

Every call carries the reviewer's own GitHub OAuth token (the one the app signed them in
with), so requests count against their rate limit and see what they can see. The one
write is :meth:`Reader.create_review`, which posts a reviewer's notes as their own PR
review. Calls are synchronous: the reviewer endpoints run in FastAPI's thread pool, and a
matrix build is a short sequence of dependent reads.
"""
from __future__ import annotations

import re

import httpx

from .. import config

EQUIV_PATH = "mappings/ari.equivalencies.tsv"
SSSOM_PATH = "mappings/ari.sssom.tsv"
ONTOLOGY_PATH = "ontologies/ari_t1d.owl"
# Predicted matches per (disease, database), best first, from predict_target_matches.py.
PREDICTIONS_PATH = "notebook/ari-grounding/target_predictions.json"
REVIEWED_PATHS = (EQUIV_PATH, PREDICTIONS_PATH)

API = "https://api.github.com"


class Reader:
    """GitHub reads for ``config.GH_OWNER/GH_REPO`` with one reviewer's token."""

    def __init__(self, token: str):
        if not (config.GH_OWNER and config.GH_REPO):
            raise ValueError("GITHUB_OWNER and GITHUB_REPO must be set to review pull requests")
        self.repo = f"{config.GH_OWNER}/{config.GH_REPO}"
        self._client = httpx.Client(base_url=API, timeout=60, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        # A Reader lives for one request; within it, a PR and its file list are
        # read once however many steps need them.
        self._pulls: dict[int, dict] = {}
        self._file_lists: dict[int, list[dict]] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._client.close()

    def _json(self, path: str, **params):
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def _pull(self, number: int) -> dict:
        if number not in self._pulls:
            self._pulls[number] = self._json(f"/repos/{self.repo}/pulls/{number}")
        return self._pulls[number]

    def _files(self, number: int) -> list[dict]:
        """Every file the PR changes. Only the first 100 used to be read, so a
        reviewed file past them was missed."""
        if number not in self._file_lists:
            files, page = [], 1
            while True:
                batch = self._json(f"/repos/{self.repo}/pulls/{number}/files",
                                   per_page=100, page=page)
                files += batch
                if len(batch) < 100:
                    break
                page += 1
            self._file_lists[number] = files
        return self._file_lists[number]

    def _changed_paths(self, number: int) -> list[str]:
        return [f["filename"] for f in self._files(number) if f["filename"] in REVIEWED_PATHS]

    # Open PRs with their changed paths, in one GraphQL call per 50 PRs. The REST
    # listing has no file list, so it took one more call per open PR.
    _OPEN_PRS = """query($owner: String!, $name: String!, $after: String) {
      repository(owner: $owner, name: $name) {
        pullRequests(states: OPEN, first: 50, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes { number title url createdAt headRefOid author { login }
                  files(first: 100) { totalCount nodes { path } } } } } }"""

    def list_review_prs(self) -> list[dict]:
        """Open pull requests whose diff touches the equivalencies or predictions file."""
        owner, name = self.repo.split("/")
        out, after = [], None
        while True:
            resp = self._client.post("/graphql", json={"query": self._OPEN_PRS, "variables": {
                "owner": owner, "name": name, "after": after}})
            resp.raise_for_status()
            body = resp.json()
            if body.get("errors"):
                raise ValueError(f"GitHub GraphQL refused the PR listing: {body['errors']}")
            page = body["data"]["repository"]["pullRequests"]
            for pr in page["nodes"]:
                files = pr["files"]
                # A PR changing more than 100 files is read in full the REST way.
                paths = ([n["path"] for n in files["nodes"] if n["path"] in REVIEWED_PATHS]
                         if files["totalCount"] <= len(files["nodes"])
                         else self._changed_paths(pr["number"]))
                if paths:
                    out.append({"number": pr["number"], "title": pr["title"],
                                "author": (pr["author"] or {}).get("login", "ghost"),
                                "created_at": pr["createdAt"], "url": pr["url"],
                                "head_sha": pr["headRefOid"], "paths": paths})
            if not page["pageInfo"]["hasNextPage"]:
                return out
            after = page["pageInfo"]["endCursor"]

    def head_sha(self, number: int) -> str:
        """The PR's head commit: one call, and all a cached matrix needs."""
        return self._pull(number)["head"]["sha"]

    def pr_refs(self, number: int) -> dict:
        """Head sha and the merge base the PR's changes are measured from."""
        pr = self._pull(number)
        head_sha = pr["head"]["sha"]
        compare = self._json(f"/repos/{self.repo}/compare/{pr['base']['sha']}...{head_sha}")
        return {"number": number, "title": pr["title"], "author": pr["user"]["login"],
                "url": pr["html_url"], "state": pr["state"],
                "head_sha": head_sha, "merge_base": compare["merge_base_commit"]["sha"],
                "base_ref": pr["base"]["ref"], "paths": self._changed_paths(number)}

    def file_at(self, path: str, ref: str) -> bytes:
        """Raw bytes of ``path`` at ``ref`` (a sha or branch).

        PR head commits are reachable from the base repository through its
        ``refs/pull/N/head``, so fork PRs need no second repository here.
        """
        resp = self._client.get(f"/repos/{self.repo}/contents/{path}", params={"ref": ref},
                                headers={"Accept": "application/vnd.github.raw"})
        resp.raise_for_status()
        return resp.content

    def diff_lines(self, number: int, path: str) -> dict[str, set[int]]:
        """``{"LEFT": {...}, "RIGHT": {...}}``: the lines of ``path`` inside the PR's diff
        hunks, the only lines GitHub lets a review comment attach to. Empty when GitHub
        omits the patch (a diff too large to show)."""
        patch = next((f.get("patch", "") for f in self._files(number) if f["filename"] == path), "")
        lines = {"LEFT": set(), "RIGHT": set()}
        left = right = 0
        for ln in patch.splitlines():
            hunk = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", ln)
            if hunk:
                left, right = int(hunk[1]), int(hunk[2])
            elif ln.startswith("-"):
                lines["LEFT"].add(left)
                left += 1
            elif ln.startswith("+"):
                lines["RIGHT"].add(right)
                right += 1
            elif not ln.startswith("\\"):          # context; skip "\ No newline at end"
                lines["LEFT"].add(left)
                lines["RIGHT"].add(right)
                left += 1
                right += 1
        return lines

    def create_review(self, number: int, commit_id: str, body: str,
                      comments: list[dict]) -> dict:
        """Post one COMMENT review: ``comments`` are ``{path, line, side, body}``."""
        resp = self._client.post(f"/repos/{self.repo}/pulls/{number}/reviews", json={
            "commit_id": commit_id, "event": "COMMENT", "body": body, "comments": comments})
        resp.raise_for_status()
        return resp.json()
