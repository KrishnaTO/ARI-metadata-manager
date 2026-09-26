"""Read-only access to the ARI repository's pull requests through the ``gh`` CLI.

The reviewer runs on the maintainer's machine, where ``gh`` is already signed in, so
no token ever passes through this code. Nothing here writes to GitHub.
"""
from __future__ import annotations

import json
import subprocess

REPO = "KrishnaTO/ARI"
EQUIV_PATH = "mappings/ari.equivalencies.tsv"
SSSOM_PATH = "mappings/ari.sssom.tsv"
ONTOLOGY_PATH = "ontologies/ari_t1d.owl"


def _gh(args: list[str]) -> bytes:
    proc = subprocess.run(["gh", *args], capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _api_json(path: str):
    return json.loads(_gh(["api", path]))


def list_equivalency_prs(state: str = "open") -> list[dict]:
    """Pull requests on ``REPO`` whose diff touches the equivalencies TSV."""
    prs = _api_json(f"repos/{REPO}/pulls?state={state}&per_page=100")
    out = []
    for pr in prs:
        files = _api_json(f"repos/{REPO}/pulls/{pr['number']}/files?per_page=100")
        if any(f["filename"] == EQUIV_PATH for f in files):
            out.append({"number": pr["number"], "title": pr["title"],
                        "author": pr["user"]["login"], "created_at": pr["created_at"],
                        "url": pr["html_url"], "head_sha": pr["head"]["sha"]})
    return out


def pr_refs(number: int) -> dict:
    """Head sha/repo and the merge base the PR's changes are measured from."""
    pr = _api_json(f"repos/{REPO}/pulls/{number}")
    head_sha = pr["head"]["sha"]
    compare = _api_json(f"repos/{REPO}/compare/{pr['base']['sha']}...{head_sha}")
    return {"number": number, "title": pr["title"], "author": pr["user"]["login"],
            "url": pr["html_url"], "state": pr["state"],
            "head_sha": head_sha, "merge_base": compare["merge_base_commit"]["sha"],
            "base_ref": pr["base"]["ref"]}


def file_at(path: str, ref: str) -> bytes:
    """Raw bytes of ``path`` at ``ref`` (a sha or branch) in ``REPO``.

    PR head commits are reachable from the base repository through its
    ``refs/pull/N/head``, so fork PRs need no second repository here.
    """
    return _gh(["api", "-H", "Accept: application/vnd.github.raw",
                f"repos/{REPO}/contents/{path}?ref={ref}"])
