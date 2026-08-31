"""A publish carries the curator's own diseases and nothing else (issue #146).

The working copy is days old by the time it is published. Committing it whole
reverted every record merged into the source branch since it was made — 208
synonyms, 57 clinical subtypes and ~100 review records over two weeks. These
tests pin the shape of the fix: the source branch is the base of the commit, and
only the diseases in the curator's touched set are written over it.
"""
import pytest

from app import merge_service


@pytest.fixture
def pair(make_service):
    """(working copy, source branch) — two independent copies of the ontology."""
    return make_service(), make_service()


def _iris(svc, n=2):
    return [d["iri"] for d in svc.get_diseases_list()[:n]]


# ------------------------------------------------------------------- rebasing
def test_another_curators_disease_survives_this_curators_publish(pair):
    working, branch = pair
    mine, theirs = _iris(working)

    working.update_disease(mine, {"synonyms": "mine-1, mine-2"}, editor="ada")
    branch.update_disease(theirs, {"synonyms": "theirs-1"}, editor="bob")

    merge_service.rebase(working, branch, {mine})

    # Compared as sets: owlready2 rewrites a multi-valued annotation through a
    # set difference, so the order a value lands in the file is already not
    # stable between two runs of the app itself. The graft reproduces whatever
    # order the working copy holds.
    assert set(branch.get_disease_detail(mine)["synonyms"]) == {"mine-1", "mine-2"}
    # The whole point: publishing my edit did not revert yours.
    assert branch.get_disease_detail(theirs)["synonyms"] == ["theirs-1"]


def test_an_untouched_disease_keeps_the_branchs_version_even_when_the_copy_is_stale(pair):
    working, branch = pair
    mine, stale = _iris(working)
    before = working.get_disease_detail(stale)["synonyms"]

    branch.update_disease(stale, {"synonyms": "added-upstream"}, editor="bob")
    working.update_disease(mine, {"definition": "mine"}, editor="ada")
    assert working.get_disease_detail(stale)["synonyms"] == before   # copy never saw it

    merge_service.rebase(working, branch, {mine})

    assert branch.get_disease_detail(stale)["synonyms"] == ["added-upstream"]


def test_items_added_to_a_touched_disease_are_carried_across(pair):
    working, branch = pair
    mine = _iris(working, 1)[0]
    working.add_item(mine, "symptoms", {"name": "Grafted symptom",
                                        "symptomDescription": "from the working copy"},
                     editor="ada")

    merge_service.rebase(working, branch, {mine})

    got = branch.get_disease_detail(mine)["symptoms"]
    grafted = [s for s in got if s["name"] == "Grafted symptom"]
    assert len(grafted) == 1
    # The item individual's own triples came too, not just the link to it.
    assert grafted[0]["description"] == ["from the working copy"]


def test_items_deleted_in_the_working_copy_do_not_come_back(pair):
    working, branch = pair
    mine = next(i for i in _iris(working, 40) if working.get_disease_detail(i)["symptoms"])
    victim = working.get_disease_detail(mine)["symptoms"][0]
    working.delete_item(victim["iri"], "symptoms", mine, editor="ada")

    merge_service.rebase(working, branch, {mine})

    after = branch.get_disease_detail(mine)["symptoms"]
    assert victim["iri"] not in [s["iri"] for s in after]


def test_a_property_the_branch_has_never_seen_is_declared(pair):
    working, branch = pair
    mine = _iris(working, 1)[0]
    # apply_enrichment writes ARI_EnrichmentSource; a branch predating it has no
    # such property, and using one that is never declared fails schema checks.
    working._ensure_annotation_property("ARI_BrandNew")[working._entity(mine)] = ["hello"]
    working._save()

    merge_service.rebase(working, branch, {mine})

    assert branch.world[branch.base + "ARI_BrandNew"] is not None
    assert branch._get_annotation(branch._entity(mine), branch.base + "ARI_BrandNew") == ["hello"]


def test_the_rebased_file_still_loads(pair, tmp_path):
    from app.ontology_service import OntologyService
    working, branch = pair
    mine = _iris(working, 1)[0]
    working.update_disease(mine, {"synonyms": "round-trip"}, editor="ada")
    merge_service.rebase(working, branch, {mine})
    branch._save()

    reloaded = OntologyService(str(branch.path))
    assert reloaded.get_disease_detail(mine)["synonyms"] == ["round-trip"]
    assert len(reloaded.get_diseases_list()) == len(working.get_diseases_list())


# ----------------------------------------------------------------- conflicts
def test_an_upstream_edit_to_a_disease_i_touched_is_a_conflict(pair):
    working, branch = pair
    mine = _iris(working, 1)[0]
    working.update_disease(mine, {"definition": "mine"}, editor="ada")
    branch.update_disease(mine, {"definition": "theirs"}, editor="bob")

    got = merge_service.upstream_edits(working, branch, {mine})

    assert [c["iri"] for c in got] == [mine]
    assert any("bob" in e for e in got[0]["entries"])


def test_my_own_published_work_coming_back_is_not_a_conflict(pair):
    working, branch = pair
    mine = _iris(working, 1)[0]
    working.update_disease(mine, {"definition": "mine"}, editor="ada")
    # The branch now carries exactly what I have, as it would after my PR merged.
    merge_service.rebase(working, branch, {mine})

    assert merge_service.upstream_edits(working, branch, {mine}) == []


def test_an_untouched_disease_changing_upstream_is_not_a_conflict(pair):
    working, branch = pair
    mine, theirs = _iris(working)
    branch.update_disease(theirs, {"definition": "theirs"}, editor="bob")

    assert merge_service.upstream_edits(working, branch, {mine}) == []


# ------------------------------------------------ confirmations store the id
def test_confirming_a_cross_reference_stores_the_id_on_the_disease(service):
    iri = service.get_diseases_list()[0]["iri"]
    service.update_disease(iri, {"mondo": ""}, editor="setup")

    added = service.store_confirmed_xrefs(
        [{"iri": iri, "db": "mondo", "ids": ["MONDO:0850054"]}], editor="linikujp")

    assert added == 1
    detail = service.get_disease_detail(iri)
    assert detail["mondo"] == ["0850054"]        # stored bare, as every id is
    assert any("Stored confirmed cross-reference" in c and "linikujp" in c
               for c in detail["changelog"])


def test_confirming_an_id_the_disease_already_holds_changes_nothing(service):
    iri = service.get_diseases_list()[0]["iri"]
    service.update_disease(iri, {"mondo": "0850054"}, editor="setup")
    before = service.get_disease_detail(iri)["changelog"]

    added = service.store_confirmed_xrefs(
        [{"iri": iri, "db": "mondo", "ids": ["0850054"]}], editor="linikujp")

    assert added == 0
    assert service.get_disease_detail(iri)["changelog"] == before


def test_confirming_leaves_the_ids_already_on_file_alone(service):
    iri = service.get_diseases_list()[0]["iri"]
    service.update_disease(iri, {"orphanet": "111"}, editor="setup")

    service.store_confirmed_xrefs([{"iri": iri, "db": "orphanet", "ids": ["617930"]}],
                                  editor="linikujp")

    assert set(service.get_disease_detail(iri)["orphanet"]) == {"111", "617930"}
