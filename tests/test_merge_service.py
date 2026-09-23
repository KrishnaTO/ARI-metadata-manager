"""Three-way merge of a curator's working copy with the source branch.

``base`` is the version the working copy started from, ``mine`` the working copy,
``theirs`` the branch. Each fixture service is an independent copy of the same
ontology, so all three start identical — exactly an ancestor and two descendants.
"""
import pytest

from app import merge_service
from app.ontology_service import OntologyService


@pytest.fixture
def trio(make_service):
    """(base, mine, theirs)."""
    return make_service(), make_service(), make_service()


def _iri(svc, n=0):
    return svc.get_diseases_list()[n]["iri"]


def _with_synonyms(svc):
    return next(d["iri"] for d in svc.get_diseases_list()
                if svc.get_disease_detail(d["iri"])["synonyms"])


def _with_symptom(svc):
    for d in svc.get_diseases_list():
        syms = svc.get_disease_detail(d["iri"])["symptoms"]
        if syms:
            return d["iri"], syms[0]["iri"]
    raise AssertionError("fixture ontology has no disease with a symptom")


def _reloaded(svc):
    svc._save()
    return OntologyService(str(svc.path))


def test_a_field_only_they_changed_comes_in(trio):
    base, mine, theirs = trio
    d = _iri(mine)
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)
    assert m.conflicts == []
    merge_service.apply(mine, m, (mine, theirs))

    assert _reloaded(mine).get_disease_detail(d)["definition"] == "theirs"


def test_a_field_only_i_changed_is_kept(trio):
    # Also the submitted-but-unmerged case: my edit is not on the branch yet.
    base, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"definition": "mine"}, editor="ada")

    m = merge_service.merge_disease(base, mine, theirs, d)
    merge_service.apply(mine, m, (mine, theirs))

    assert _reloaded(mine).get_disease_detail(d)["definition"] == "mine"


def test_both_changing_one_field_is_a_conflict_that_names_both_values(trio):
    base, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"definition": "mine"}, editor="ada")
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)

    [c] = m.conflicts
    assert c["key"] == f"{d}|http://www.w3.org/2000/01/rdf-schema#comment"
    assert c["label"] == "Definition"
    assert (c["mine"], c["theirs"]) == ("mine", "theirs")
    assert any("| bob |" in e for e in m.upstream_log)


@pytest.mark.parametrize("pick,expected", [("mine", "mine"), ("theirs", "theirs")])
def test_a_choice_settles_the_conflict(trio, pick, expected):
    base, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"definition": "mine"}, editor="ada")
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")
    key = f"{d}|http://www.w3.org/2000/01/rdf-schema#comment"

    m = merge_service.merge_disease(base, mine, theirs, d, {key: pick})
    assert m.conflicts == []
    merge_service.apply(mine, m, (mine, theirs))

    assert _reloaded(mine).get_disease_detail(d)["definition"] == expected


def test_list_fields_merge_value_by_value(trio):
    base, mine, theirs = trio
    d = _with_synonyms(mine)
    existing = mine.get_disease_detail(d)["synonyms"]
    kept = existing[1:]
    mine.update_disease(d, {"synonyms": ", ".join(existing + ["added by me"])}, editor="ada")
    theirs.update_disease(d, {"synonyms": ", ".join(kept)}, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)
    assert m.conflicts == []
    merge_service.apply(mine, m, (mine, theirs))

    got = set(_reloaded(mine).get_disease_detail(d)["synonyms"])
    assert got == set(kept) | {"added by me"}


def test_both_sides_changelog_lines_are_kept(trio):
    base, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"definition": "mine"}, editor="ada")
    theirs.update_disease(d, {"age_range": "adults"}, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)
    merge_service.apply(mine, m, (mine, theirs))

    log = _reloaded(mine).get_disease_detail(d)["changelog"]
    assert any("| ada |" in e for e in log) and any("| bob |" in e for e in log)


@pytest.mark.parametrize("pick", ["mine", "theirs"])
def test_an_item_they_deleted_and_i_edited_is_a_conflict(trio, pick):
    base, mine, theirs = trio
    d, item = _with_symptom(mine)
    mine.update_item(item, "symptoms", {"symptomDescription": "edited by me"},
                     disease_iri=d, editor="ada")
    theirs.delete_item(item, "symptoms", d, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)
    assert [c["key"] for c in m.conflicts] == [f"{item}|"]

    m = merge_service.merge_disease(base, mine, theirs, d, {f"{item}|": pick})
    merge_service.apply(mine, m, (mine, theirs))

    kept = [s["iri"] for s in _reloaded(mine).get_disease_detail(d)["symptoms"]]
    assert (item in kept) is (pick == "mine")


def test_an_item_they_deleted_and_i_left_alone_is_deleted(trio):
    base, mine, theirs = trio
    d, item = _with_symptom(mine)
    theirs.delete_item(item, "symptoms", d, editor="bob")

    m = merge_service.merge_disease(base, mine, theirs, d)
    assert m.conflicts == []
    merge_service.apply(mine, m, (mine, theirs))

    assert item not in [s["iri"] for s in _reloaded(mine).get_disease_detail(d)["symptoms"]]


def test_without_an_ancestor_every_differing_single_field_is_asked(trio):
    _, mine, theirs = trio
    d = _iri(mine)
    theirs.update_disease(d, {"definition": "theirs"}, editor="bob")

    m = merge_service.merge_disease(None, mine, theirs, d)

    assert [c["label"] for c in m.conflicts] == ["Definition"]
    assert m.conflicts[0]["base"] is None


def test_without_an_ancestor_list_fields_keep_both_sides(trio):
    _, mine, theirs = trio
    d = _iri(mine)
    mine.update_disease(d, {"synonyms": "only mine"}, editor="ada")
    theirs.update_disease(d, {"synonyms": "only theirs"}, editor="bob")

    m = merge_service.merge_disease(None, mine, theirs, d)
    assert m.conflicts == []
    merge_service.apply(mine, m, (mine, theirs))

    assert set(_reloaded(mine).get_disease_detail(d)["synonyms"]) >= {"only mine", "only theirs"}
