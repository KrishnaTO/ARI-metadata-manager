"""A working copy keeps the version it started from, and merges the branch into it.

Covers the ancestor files beside each working copy and the sync / resolve /
publish endpoints that merge the source branch in (see
docs/superpowers/specs/2026-09-22-working-copy-merge-design.md).
"""
import shutil
from collections import OrderedDict

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import config, sessions, workspace
from app import github_service as gh
from app.ontology_service import OntologyService


@pytest.fixture
def curator(tmp_path, monkeypatch, base_owl):
    """A signed-in curator 'ada' with a private user dir and a temp base file."""
    base = tmp_path / "base.owl"
    shutil.copy2(base_owl, base)
    monkeypatch.setattr(config, "USER_DIR", tmp_path / "user")
    monkeypatch.setattr(config, "ONTOLOGY_FILE", str(base))
    # BASE is loaded from the real file at import; a curator without a working
    # copy is served it, so it must point at the temp base too.
    monkeypatch.setattr(workspace, "BASE", OntologyService(str(base)))
    monkeypatch.setattr(workspace, "USER_SVC", OrderedDict())
    return "ada"


def _first(svc):
    return svc.get_diseases_list()[0]["iri"]


def test_a_new_working_copy_records_its_ancestor(curator):
    workspace.user_service(curator, create=True)

    assert workspace.ancestor(curator) is not None
    assert workspace.ancestor_sha(curator) is None       # made from the local file
    assert not list(config.USER_DIR.glob("*.base*"))      # never beside the working copies


def test_resetting_a_curator_drops_the_ancestor(curator):
    workspace.user_service(curator, create=True)
    workspace._reset_user(curator)

    assert workspace.ancestor(curator) is None
    assert workspace.ancestor_sha(curator) is None


def test_merge_from_writes_the_working_copy_and_evicts_it(curator, make_service):
    svc = workspace.user_service(curator, create=True)
    d = _first(svc)
    theirs = make_service()
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")

    out = workspace.merge_from(curator, theirs, [d])

    assert out["merged"] == [d]
    assert curator not in workspace.USER_SVC                  # owlready cache is stale
    assert workspace.user_service(curator).get_disease_detail(d)["definition"] == "theirs"
    assert OntologyService(str(workspace.ancestor_path(curator))) \
        .get_disease_detail(d)["definition"] == "theirs"


# ---------------------------------------------------------------- endpoints
client = TestClient(main.app)
COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"


@pytest.fixture
def branch(curator, monkeypatch, make_service):
    """Signed in as ada, with GitHub stubbed to serve ``branch['svc']`` at ``branch['sha']``."""
    state = {"svc": make_service(), "sha": "sha-2", "refs": []}
    monkeypatch.setattr(config, "GH_ENABLED", True)
    monkeypatch.setattr(sessions, "_user", lambda r: {"token": "t", "identity": {"login": "ada"}})
    monkeypatch.setattr(sessions, "_login", lambda r: "ada")

    async def _sha(*a):
        return state["sha"]

    async def _file(token, owner, repo, path, ref):
        state["refs"].append(ref)
        state["svc"]._save()
        return state["svc"].path.read_bytes()

    monkeypatch.setattr(gh, "branch_sha", _sha)
    monkeypatch.setattr(gh, "get_file_at", _file)
    return state


def test_sync_is_a_no_op_when_the_branch_has_not_moved(branch):
    workspace.user_service("ada", create=True)
    workspace.set_ancestor_sha("ada", "sha-2")

    assert client.post("/api/v2/sync").json() == {"up_to_date": True,
                                                  "revision": workspace.copy_revision("ada")}


def test_sync_merges_the_branch_and_records_its_commit(branch):
    d = _first(workspace.user_service("ada", create=True))
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/sync").json()

    assert r["merged"] == [d] and r["conflicts"] == []
    assert workspace.ancestor_sha("ada") == "sha-2"
    assert workspace.user_service("ada").get_disease_detail(d)["definition"] == "theirs"


def test_sync_reports_conflicts_and_does_not_advance(branch):
    svc = workspace.user_service("ada", create=True)
    d = _first(svc)
    svc.update_disease(d, {"definition": "mine"}, editor="ada")
    workspace.mark("ada", d)
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/sync").json()

    assert [c["iri"] for c in r["conflicts"]] == [d]
    assert workspace.ancestor_sha("ada") is None


def test_resolve_applies_the_choices(branch):
    svc = workspace.user_service("ada", create=True)
    d = _first(svc)
    svc.update_disease(d, {"definition": "mine"}, editor="ada")
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/resolve", json={"sha": "sha-2", "choices": {d: {f"{d}|{COMMENT}": "theirs"}}})

    assert r.status_code == 200 and r.json()["merged"] == [d]
    assert workspace.user_service("ada").get_disease_detail(d)["definition"] == "theirs"


def test_resolve_with_an_unanswered_conflict_hands_it_back(branch):
    svc = workspace.user_service("ada", create=True)
    d = _first(svc)
    svc.update_disease(d, {"definition": "mine"}, editor="ada")
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/resolve", json={"sha": "sha-2", "choices": {d: {}}})

    assert r.status_code == 409
    assert r.json()["conflicts"][0]["fields"][0]["key"] == f"{d}|{COMMENT}"


def test_resolve_rejects_a_choice_that_is_not_mine_or_theirs(branch):
    workspace.user_service("ada", create=True)
    r = client.post("/api/v2/resolve", json={"sha": "sha-2", "choices": {"x": {"k": "both"}}})
    assert r.status_code == 400


def test_publish_refuses_with_field_level_conflicts(branch):
    svc = workspace.user_service("ada", create=True)
    d = _first(svc)
    svc.update_disease(d, {"definition": "mine"}, editor="ada")
    workspace.mark("ada", d)
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/publish", json={})

    assert r.status_code == 409
    [c] = r.json()["conflicts"]
    assert c["iri"] == d and c["fields"][0]["label"] == "Definition"


def test_resolve_without_a_working_copy_refuses_and_leaves_the_base_alone(branch):
    before = open(config.ONTOLOGY_FILE, "rb").read()
    d = _first(branch["svc"])
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/resolve", json={"sha": "sha-2", "choices": {d: {}}})

    assert r.status_code == 400
    assert open(config.ONTOLOGY_FILE, "rb").read() == before


def test_a_clean_sync_gives_a_copy_without_an_ancestor_one(branch):
    # Copies made before ancestors existed have neither file.
    d = _first(workspace.user_service("ada", create=True))
    workspace._drop_ancestor("ada")
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")

    r = client.post("/api/v2/sync").json()

    assert r["conflicts"] == []
    assert workspace.ancestor_path("ada").exists()
    assert workspace.ancestor_sha("ada") == "sha-2"
    assert workspace.ancestor("ada").get_disease_detail(d)["definition"] == "theirs"


def test_merge_from_with_nothing_to_merge_leaves_both_files_alone(curator, make_service):
    import os
    workspace.user_service(curator, create=True)
    paths = [config.USER_DIR / f"{curator}.owl", workspace.ancestor_path(curator)]
    for p in paths:
        os.utime(p, (1_000_000, 1_000_000))

    out = workspace.merge_from(curator, make_service(),
                               _all(workspace.user_service(curator)))

    assert out["merged"] == [] and out["advanced"] == []
    assert [p.stat().st_mtime for p in paths] == [1_000_000, 1_000_000]


def _all(svc):
    return {d["iri"] for d in svc.get_diseases_list()}


# ------------------------------------------------ telling a stale window it is stale
# A window left in the background does not see another window's sync: by the
# time it comes back the branch is merged, its own sync says "up to date", and
# a list saved from its old rows writes the branch's additions away (#162).
# Every sync answer carries the working copy's revision, which changes whenever
# a merge or fetch rewrites the copy, so the window can tell.
def test_sync_reports_a_revision_that_moves_only_when_the_copy_changes(branch):
    d = _first(workspace.user_service("ada", create=True))
    first = client.post("/api/v2/sync").json()          # nothing new: identical branch
    assert first["merged"] == [] and first["revision"]

    again = client.post("/api/v2/sync").json()
    assert again == {"up_to_date": True, "revision": first["revision"]}

    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")
    branch["sha"] = "sha-3"
    moved = client.post("/api/v2/sync").json()           # another window's sync
    assert moved["merged"] == [d] and moved["revision"] != first["revision"]

    later = client.post("/api/v2/sync").json()           # the stale window, back again
    assert later == {"up_to_date": True, "revision": moved["revision"]}


def test_recording_the_branch_commit_keeps_the_revision(branch):
    workspace.user_service("ada", create=True)
    rev = workspace.copy_revision("ada")
    workspace.set_ancestor_sha("ada", "sha-9")
    assert workspace.copy_revision("ada") == rev


# ------------------------------------------------------- a source branch that is gone
# A curator can source from an edit/* branch; once its PR merges the branch is
# deleted, and every load used to say only "Couldn't check … for updates" (#165).
def _github(monkeypatch, handler):
    """Route github_service's HTTP calls to ``handler`` (an httpx MockTransport handler)."""
    import httpx
    real = httpx.AsyncClient
    monkeypatch.setattr(gh.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_branch_sha_reads_the_branch_head(monkeypatch):
    import asyncio

    import httpx
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"name": "edit/ada/x", "commit": {"sha": "abc123"}})

    _github(monkeypatch, handler)
    assert asyncio.run(gh.branch_sha("t", "o", "r", "edit/ada/x")) == "abc123"
    assert seen == ["/repos/o/r/branches/edit/ada/x"]


def test_a_deleted_branch_is_reported_as_such(monkeypatch):
    import asyncio

    import httpx
    _github(monkeypatch, lambda request: httpx.Response(404, json={"message": "Branch not found"}))
    with pytest.raises(gh.BranchNotFound):
        asyncio.run(gh.branch_sha("t", "o", "r", "edit/ada/x"))


def _gone(branch, monkeypatch, name="edit/ada/merged"):
    """Point ada at ``name`` and make GitHub say it no longer exists."""
    workspace._set_branch_state("ada", source_branch=name, pr_base=name)

    async def _missing(token, owner, repo, b):
        if b == name:
            raise gh.BranchNotFound(b)
        return branch["sha"]

    monkeypatch.setattr(gh, "branch_sha", _missing)


def test_sync_says_the_source_branch_is_gone_and_leaves_the_copy(branch, monkeypatch):
    svc = workspace.user_service("ada", create=True)
    before = svc.path.read_bytes()
    _gone(branch, monkeypatch)

    r = client.post("/api/v2/sync")

    assert r.status_code == 409
    assert r.json()["missing_branch"] == "edit/ada/merged"
    assert r.json()["base_branch"] == config.GH_BASE_BRANCH
    assert (config.USER_DIR / "ada.owl").read_bytes() == before


def test_following_the_base_keeps_the_working_copy_and_its_work(branch, monkeypatch):
    svc = workspace.user_service("ada", create=True)
    d = _first(svc)
    svc.update_disease(d, {"definition": "mine, unsubmitted"}, editor="ada")
    workspace.mark("ada", d)
    _gone(branch, monkeypatch)

    r = client.post("/api/v2/source/follow-base")

    assert r.status_code == 200
    assert workspace._branch_state("ada") == {"source_branch": config.GH_BASE_BRANCH,
                                              "pr_base": config.GH_BASE_BRANCH}
    assert workspace.touched("ada") == {d}
    # The next sync merges the base in three-way; the curator's edit stands.
    assert client.post("/api/v2/sync").status_code == 200
    assert workspace.user_service("ada").get_disease_detail(d)["definition"] == "mine, unsubmitted"


def test_following_the_base_is_refused_while_the_branch_still_exists(branch):
    workspace.user_service("ada", create=True)
    workspace._set_branch_state("ada", source_branch="edit/ada/live", pr_base="edit/ada/live")

    r = client.post("/api/v2/source/follow-base")

    assert r.status_code == 400
    assert workspace._branch_state("ada")["source_branch"] == "edit/ada/live"


# ------------------------------------------------ one write at a time per curator
# A publish holds the working copy across its GitHub calls and restores it if
# they fail. A sync from a second window used to be able to land inside that
# gap, and the rollback then put back the pre-sync copy while the ancestor had
# already moved on — the branch's changes looked reverted by the curator (#163).
def test_a_sync_waits_for_a_publish_in_flight(branch, monkeypatch):
    import asyncio

    import httpx

    svc = workspace.user_service("ada", create=True)
    mine, upstream = _first(svc), svc.get_diseases_list()[1]["iri"]
    svc.update_disease(mine, {"definition": "mine"}, editor="ada")
    workspace.mark("ada", mine)
    branch["svc"].update_disease(upstream, {"definition": "theirs"}, editor="bob")
    order = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def _publish_file(**kw):
            entered.set()
            await release.wait()                 # the GitHub calls, taking their time
            order.append("publish")
            return {"pr_number": 1, "pr_url": "https://example.invalid/pr/1"}

        async def _sha(*a):
            if entered.is_set():                 # publish asked before its GitHub calls
                order.append("sync")
            return branch["sha"]

        monkeypatch.setattr(gh, "publish_file", _publish_file)
        monkeypatch.setattr(gh, "branch_sha", _sha)
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            pub = asyncio.create_task(c.post("/api/v2/publish", json={}))
            await entered.wait()
            syn = asyncio.create_task(c.post("/api/v2/sync"))
            await asyncio.sleep(0.3)
            release.set()
            r_pub, r_syn = await pub, await syn
        return r_pub, r_syn

    r_pub, r_syn = asyncio.run(scenario())

    assert (r_pub.status_code, r_syn.status_code) == (200, 200)
    assert order == ["publish", "sync"]
    assert r_syn.json()["merged"] == [upstream]


# ------------------------------------------ answers apply to the commit shown
# A conflict is shown against one commit of the branch. Resolving against
# whatever the branch holds by then could apply a "theirs" the curator never
# saw (#164), so every conflict list names its commit and resolve merges there.
def _clash(branch):
    svc = workspace.user_service("ada", create=True)
    d = _first(svc)
    svc.update_disease(d, {"definition": "mine"}, editor="ada")
    workspace.mark("ada", d)
    branch["svc"].update_disease(d, {"definition": "theirs"}, editor="bob")
    return d


def test_sync_names_the_commit_its_conflicts_came_from(branch):
    _clash(branch)
    assert client.post("/api/v2/sync").json()["sha"] == "sha-2"


def test_publish_merges_at_a_pinned_commit_and_names_it(branch):
    _clash(branch)

    r = client.post("/api/v2/publish", json={})

    assert r.status_code == 409 and r.json()["sha"] == "sha-2"
    assert branch["refs"] == ["sha-2"]            # never the moving branch name


def test_resolve_merges_at_the_commit_the_curator_saw(branch):
    d = _clash(branch)
    branch["sha"] = "sha-3"                       # the branch moved on meanwhile

    r = client.post("/api/v2/resolve", json={"sha": "sha-2",
                                             "choices": {d: {f"{d}|{COMMENT}": "theirs"}}})

    assert r.status_code == 200
    assert branch["refs"] == ["sha-2"]


def test_resolve_names_the_commit_of_what_is_still_open(branch):
    d = _clash(branch)
    r = client.post("/api/v2/resolve", json={"sha": "sha-2", "choices": {d: {}}})
    assert r.status_code == 409 and r.json()["sha"] == "sha-2"


def test_resolve_without_a_commit_is_refused(branch):
    d = _clash(branch)
    r = client.post("/api/v2/resolve", json={"choices": {d: {f"{d}|{COMMENT}": "theirs"}}})
    assert r.status_code == 400
    assert branch["refs"] == []
