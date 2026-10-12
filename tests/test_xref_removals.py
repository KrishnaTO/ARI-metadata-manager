"""An id edited off a disease record still has to reach the mapping files.

Flagging on the review page records a judgment; editing the same id out of the
disease record used to record nothing, so a curated cross-reference could leave
the registry with no decision behind it. The data repo rejects that
(``xref-deleted``), which is how ARI:0001158 lost four ids in an open PR.
"""
import pytest

from app import stores, xref_removals


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    store = xref_removals.XrefRemovalStore(tmp_path / "provenance")
    monkeypatch.setattr(stores, "XREF_REMOVALS", store)
    return store


def test_removing_an_id_parks_it_as_a_judgment(ledger):
    ledger.record("iri:1", {"doid": ["0060234"]}, {"doid": []}, "ada")
    assert ledger.pending() == [{"iri": "iri:1", "db": "doid", "ids": ["0060234"]}]


def test_replacing_an_id_parks_only_the_one_that_left(ledger):
    """The ARI:0001158 case: a curator swaps a wrong code for the right one, so
    the old code is the judgment and the new one is not."""
    ledger.record("iri:1", {"doid": ["0060234"]}, {"doid": ["0050168"]}, "ada")
    assert ledger.pending() == [{"iri": "iri:1", "db": "doid", "ids": ["0060234"]}]


def test_adding_an_id_parks_nothing(ledger):
    assert ledger.record("iri:1", {"mondo": []}, {"mondo": ["0010012"]}, "ada") == 0
    assert ledger.pending() == []


def test_an_id_that_comes_back_is_not_a_judgment(ledger):
    """A removal undone before publishing was never a decision about the id."""
    ledger.record("iri:1", {"mesh": ["C563187"]}, {"mesh": []}, "ada")
    ledger.record("iri:1", {"mesh": []}, {"mesh": ["C563187"]}, "ada")
    assert ledger.pending() == []


def test_malformed_ids_are_repairs_not_judgments(ledger):
    """The data repo requires a negative row for a removed id only when the id is
    well-formed — a row carrying a malformed one is itself rejected. Dropping an
    ICD-9 code stored under ICD-10 is a repair, so it must not be parked."""
    ledger.record("iri:1", {"icd10": ["250.01"], "umls": ["null"]},
                  {"icd10": [], "umls": []}, "ada")
    assert ledger.pending() == []


def test_a_prefixed_id_parks_in_its_stored_form(ledger):
    """Curators paste ``MONDO:0010012``; the mapping row needs the bare local part."""
    ledger.record("iri:1", {"mondo": ["MONDO:0010012"]}, {"mondo": []}, "ada")
    assert ledger.pending() == [{"iri": "iri:1", "db": "mondo", "ids": ["0010012"]}]


def test_pending_is_limited_to_the_diseases_a_publish_covers(ledger):
    ledger.record("iri:1", {"doid": ["1"]}, {"doid": []}, "ada")
    ledger.record("iri:2", {"doid": ["2"]}, {"doid": []}, "ada")
    assert ledger.pending({"iri:1"}) == [{"iri": "iri:1", "db": "doid", "ids": ["1"]}]


def test_publishing_a_judgment_releases_it(ledger):
    """Otherwise the next publish would rule against the same id again."""
    ledger.record("iri:1", {"doid": ["0060234"]}, {"doid": []}, "ada")
    flagged = [{"iri": "iri:1", "db": "doid", "ids": ["0060234"]},
               {"iri": "iri:9", "db": "mondo", "ids": ["0019012"]}]  # from the review page
    assert ledger.clear(flagged) == 1
    assert ledger.pending() == []


def test_an_anonymous_edit_still_records_the_judgment(ledger):
    """Authorship may be unknown offline, but the id is still gone from the
    record and the mapping set has to say so."""
    ledger.record("iri:1", {"doid": ["0060234"]}, {"doid": []}, None)
    assert ledger.pending() == [{"iri": "iri:1", "db": "doid", "ids": ["0060234"]}]


# ---------------------------------------------------------------- the route

def _a_disease_with_a_doid(service):
    for d in service.get_diseases_list():
        ids = service.get_xrefs(d["iri"]).get("doid") or []
        if len(ids) == 1:
            return d["iri"], ids[0]
    pytest.skip("no disease with exactly one DOID in the fixture")


@pytest.fixture
def route(service, monkeypatch, ledger):
    """The real endpoints, against a temp copy of the ontology.

    ``service_for`` is replaced because an anonymous call with GitHub
    integration off resolves to BASE — the tracked ontology — and these tests
    write.
    """
    from fastapi.testclient import TestClient

    import app.main as main
    from app import workspace

    monkeypatch.setattr(workspace, "service_for", lambda request, write=False: service)
    return TestClient(main.app), service, ledger


def test_the_field_editor_records_a_removal(route):
    """`PUT /api/v2/disease/{iri}` — the "Edit record" form. Clearing the field
    used to leave no trace outside the ontology."""
    client, service, ledger = route
    iri, doid = _a_disease_with_a_doid(service)

    r = client.put(f"/api/v2/disease/{iri}", json={"changes": {"doid": ""}, "editor": "ada"})
    assert r.status_code == 200
    assert service.get_xrefs(iri)["doid"] == []
    assert ledger.pending() == [{"iri": iri, "db": "doid", "ids": [doid]}]


def test_the_field_editor_records_a_replacement(route):
    """The ARI:0001158 case, end to end: the code that left is the judgment."""
    client, service, ledger = route
    iri, doid = _a_disease_with_a_doid(service)

    r = client.put(f"/api/v2/disease/{iri}", json={"changes": {"doid": "0050168"}})
    assert r.status_code == 200
    assert service.get_xrefs(iri)["doid"] == ["0050168"]
    assert ledger.pending() == [{"iri": iri, "db": "doid", "ids": [doid]}]


def test_the_xref_op_route_records_a_removal(route):
    """`POST /api/v2/disease/{iri}/xref` — the review page's own id-at-a-time path."""
    client, service, ledger = route
    iri, doid = _a_disease_with_a_doid(service)

    r = client.post(f"/api/v2/disease/{iri}/xref", json={"db": "doid", "op": "remove", "value": doid})
    assert r.status_code == 200
    assert ledger.pending() == [{"iri": iri, "db": "doid", "ids": [doid]}]


def test_pending_is_limited_to_the_publishing_curator(ledger):
    """Issue #171: a removal one curator saved was published under the next
    curator who published that disease."""
    ledger.record("iri:1", {"doid": ["1"]}, {"doid": []}, "ada")
    ledger.record("iri:1", {"mesh": ["D001"]}, {"mesh": []}, "bob")
    assert ledger.pending({"iri:1"}, login="ada") == [{"iri": "iri:1", "db": "doid", "ids": ["1"]}]


# ---------------------------------------------------------- through a publish
SSSOM_SUBJECT = 0          # subject_id
EQUIV_SOURCE_ID = 1        # source_id


@pytest.fixture
def signed_in(tmp_path, monkeypatch, base_owl, make_service, ledger):
    """Signed in as ada with a private working copy; GitHub serves the fixture
    ontology as the source branch, no mapping files yet, and captures the commit."""
    import shutil
    from collections import OrderedDict

    from fastapi.testclient import TestClient

    import app.main as main
    from app import config, sessions, workspace
    from app import github_service as gh
    from app.ontology_service import OntologyService

    base = tmp_path / "base.owl"
    shutil.copy2(base_owl, base)
    monkeypatch.setattr(config, "USER_DIR", tmp_path / "user")
    monkeypatch.setattr(config, "ONTOLOGY_FILE", str(base))
    monkeypatch.setattr(workspace, "BASE", OntologyService(str(base)))
    monkeypatch.setattr(workspace, "USER_SVC", OrderedDict())
    monkeypatch.setattr(config, "GH_ENABLED", True)
    monkeypatch.setattr(sessions, "_user", lambda r: {
        "token": "t", "identity": {"login": "ada", "name": "Ada", "email": "a@x"}})
    monkeypatch.setattr(sessions, "_login", lambda r: "ada")
    branch = make_service()
    committed = {}

    async def _sha(*a):
        return "1111111111111111111111111111111111111111"

    async def _file(token, owner, repo, path, ref):
        if path != config.GH_ONTOLOGY_PATH:
            raise FileNotFoundError(path)      # no mapping files on the branch yet
        branch._save()                         # as the branch stores it, not the checkout's line endings
        return branch.path.read_bytes()

    async def _publish_file(**kw):
        committed.update(kw)
        return {"pr_url": "https://example.test/pr/1"}

    monkeypatch.setattr(gh, "branch_sha", _sha)
    monkeypatch.setattr(gh, "get_file_at", _file)
    monkeypatch.setattr(gh, "publish_file", _publish_file)
    return TestClient(main.app), committed


def _rows(committed, path):
    text = committed["extra_files"][path].decode("utf-8")
    return [line.split("\t") for line in text.splitlines()
            if line and not line.startswith("#")][1:]          # past the header


def test_a_field_editor_removal_publishes_with_its_subject(signed_in, ro_service):
    """Issue #171: a removal saved by the field editor became a mapping row with
    an empty subject in both files, which the data repo rejects."""
    from app import config
    client, committed = signed_in
    iri, doid = _a_disease_with_a_doid(ro_service)
    ari_id = next(r["ari_id"] for r in ro_service.get_xref_rows() if r["iri"] == iri)

    assert client.put(f"/api/v2/disease/{iri}", json={"changes": {"doid": ""}}).status_code == 200
    r = client.post("/api/v2/publish", json={})

    assert r.status_code == 200, r.text
    [sssom] = [row for row in _rows(committed, config.MAPPINGS_SSSOM_PATH)
               if row[4] == f"DOID:{doid}"]
    assert sssom[SSSOM_SUBJECT] == ari_id and sssom[1] and sssom[3] == "Not"
    [equiv] = [row for row in _rows(committed, config.MAPPINGS_EQUIV_PATH) if row[5] == doid]
    assert equiv[EQUIV_SOURCE_ID] == ari_id.split(":")[-1] and equiv[2]

