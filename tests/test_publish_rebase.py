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

    merge_service.graft_diseases(working, branch, {mine})

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

    merge_service.graft_diseases(working, branch, {mine})

    assert branch.get_disease_detail(stale)["synonyms"] == ["added-upstream"]


def test_items_added_to_a_touched_disease_are_carried_across(pair):
    working, branch = pair
    mine = _iris(working, 1)[0]
    working.add_item(mine, "symptoms", {"name": "Grafted symptom",
                                        "symptomDescription": "from the working copy"},
                     editor="ada")

    merge_service.graft_diseases(working, branch, {mine})

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

    merge_service.graft_diseases(working, branch, {mine})

    after = branch.get_disease_detail(mine)["symptoms"]
    assert victim["iri"] not in [s["iri"] for s in after]


def test_a_property_the_branch_has_never_seen_is_declared(pair):
    working, branch = pair
    mine = _iris(working, 1)[0]
    # apply_enrichment writes ARI_EnrichmentSource; a branch predating it has no
    # such property, and using one that is never declared fails schema checks.
    working._ensure_annotation_property("ARI_BrandNew")[working._entity(mine)] = ["hello"]
    working._save()

    merge_service.graft_diseases(working, branch, {mine})

    assert branch.world[branch.base + "ARI_BrandNew"] is not None
    assert branch._get_annotation(branch._entity(mine), branch.base + "ARI_BrandNew") == ["hello"]


def test_the_rebased_file_still_loads(pair, tmp_path):
    from app.ontology_service import OntologyService
    working, branch = pair
    mine = _iris(working, 1)[0]
    working.update_disease(mine, {"synonyms": "round-trip"}, editor="ada")
    merge_service.graft_diseases(working, branch, {mine})
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
    merge_service.graft_diseases(working, branch, {mine})

    assert merge_service.upstream_edits(working, branch, {mine}) == []


def test_an_untouched_disease_changing_upstream_is_not_a_conflict(pair):
    working, branch = pair
    mine, theirs = _iris(working)
    branch.update_disease(theirs, {"definition": "theirs"}, editor="bob")

    assert merge_service.upstream_edits(working, branch, {mine}) == []


# --------------------------------------------------- taking the branch's version
# The way out of a conflict. Fetching the branch again drops the whole working
# copy, so one collision cost the curator every unpublished edit and verdict they
# held; the graft runs the other way for just the diseases that collided.
# The service is reloaded from disk after each graft, exactly as the endpoint
# does by evicting the working copy: owlready2 caches an entity's values on the
# Python object, and a graft writes triples underneath that cache.
def _saved(svc):
    from app.ontology_service import OntologyService
    svc._save()
    return OntologyService(str(svc.path))


def test_taking_the_branchs_version_clears_the_conflict_and_keeps_my_other_work(pair):
    working, branch = pair
    collided, mine = _iris(working)
    branch.update_disease(collided, {"definition": "theirs"}, editor="bob")
    working.update_disease(collided, {"definition": "mine"}, editor="ada")
    working.update_disease(mine, {"definition": "my other edit"}, editor="ada")
    assert [c["iri"] for c in merge_service.upstream_edits(working, branch, {collided, mine})]         == [collided]

    merge_service.graft_diseases(branch, working, {collided})
    working = _saved(working)

    assert working.get_disease_detail(collided)["definition"] == "theirs"
    # The whole point of the narrow recovery: my work on everything else stands.
    assert working.get_disease_detail(mine)["definition"] == "my other edit"
    assert merge_service.upstream_edits(working, branch, {collided, mine}) == []


def test_taking_the_branchs_version_removes_an_item_i_added_to_that_disease(pair):
    working, branch = pair
    collided = _iris(working, 1)[0]
    branch.update_disease(collided, {"definition": "theirs"}, editor="bob")
    working.add_item(collided, "symptoms", {"name": "Symptom I added"}, editor="ada")
    assert any(s["name"] == "Symptom I added"
               for s in working.get_disease_detail(collided)["symptoms"])

    merge_service.graft_diseases(branch, working, {collided})
    working = _saved(working)

    assert not any(s["name"] == "Symptom I added"
                   for s in working.get_disease_detail(collided)["symptoms"])


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


# ------------------------------------------------- flags remove the id again
def test_flagging_a_cross_reference_removes_the_id_from_the_disease(service):
    iri = service.get_diseases_list()[0]["iri"]
    service.update_disease(iri, {"umls": "C0156147, C0010346"}, editor="setup")

    removed = service.remove_flagged_xrefs(
        [{"iri": iri, "db": "umls", "ids": ["umls:C0156147"]}], editor="linikujp")

    assert removed == 1
    detail = service.get_disease_detail(iri)
    assert detail["umls"] == ["C0010346"]        # the other id is left alone
    assert any("Removed flagged cross-reference" in c and "linikujp" in c
               for c in detail["changelog"])


def test_flagging_an_id_the_disease_does_not_hold_changes_nothing(service):
    iri = service.get_diseases_list()[0]["iri"]
    service.update_disease(iri, {"umls": "C0010346"}, editor="setup")
    before = service.get_disease_detail(iri)["changelog"]

    removed = service.remove_flagged_xrefs(
        [{"iri": iri, "db": "umls", "ids": ["C0156147"]}], editor="linikujp")

    assert removed == 0
    assert service.get_disease_detail(iri)["changelog"] == before
