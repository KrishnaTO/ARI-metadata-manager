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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._client.close()

    def _json(self, path: str, **params):
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def _changed_paths(self, number: int) -> list[str]:
        files = self._json(f"/repos/{self.repo}/pulls/{number}/files", per_page=100)
        return [f["filename"] for f in files if f["filename"] in REVIEWED_PATHS]

    def list_review_prs(self) -> list[dict]:
        """Open pull requests whose diff touches the equivalencies or predictions file."""
        out = []
        for pr in self._json(f"/repos/{self.repo}/pulls", state="open", per_page=100):
            paths = self._changed_paths(pr["number"])
            if paths:
                out.append({"number": pr["number"], "title": pr["title"],
                            "author": pr["user"]["login"], "created_at": pr["created_at"],
                            "url": pr["html_url"], "head_sha": pr["head"]["sha"],
                            "paths": paths})
        return out

    def pr_refs(self, number: int) -> dict:
        """Head sha and the merge base the PR's changes are measured from."""
        pr = self._json(f"/repos/{self.repo}/pulls/{number}")
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
        files = self._json(f"/repos/{self.repo}/pulls/{number}/files", per_page=100)
        patch = next((f.get("patch", "") for f in files if f["filename"] == path), "")
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
