"""A publish carries the curator's own diseases and nothing else (issue #146).

The working copy is days old by the time it is published. Committing it whole
reverted every record merged into the source branch since it was made — 208
synonyms, 57 clinical subtypes and ~100 review records over two weeks. These
tests pin the shape of the fix: the source branch is the base of the commit, and
only the diseases in the curator's touched set are written over it.
"""
import types

import pytest
from owlready2 import AnnotationProperty

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


def test_grafting_a_property_core_does_not_declare_is_refused(pair):
    working, branch = pair
    mine = _iris(working, 1)[0]
    # Only ari_core.owl declares properties. A property the destination has never
    # seen must stop the publish, not be copied into the disease file.
    with working.onto:
        types.new_class("ARI_BrandNew", (AnnotationProperty,))
    working._annotation_property("ARI_BrandNew")[working._entity(mine)] = ["hello"]
    working._save()

    with pytest.raises(ValueError, match="ARI_BrandNew is not declared in ari_core.owl"):
        merge_service.graft_diseases(working, branch, {mine})


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


# ----------------------------------------------------------------- file layout
def test_a_publish_diffs_only_on_the_curators_change(pair):
    """The committed file is ROBOT's canonical layout, so a one-synonym publish
    is a one-synonym diff however owlready2 ordered its save (ARI#105)."""
    import difflib

    from app.ontology_service import OntologyService
    from app.owl_format import format_owl
    working, branch = pair
    mine = _iris(working, 1)[0]
    before = format_owl(branch.path.read_bytes()).decode().split("\n")
    working.update_disease(mine, {"synonyms": "only-change"}, editor="ada")

    merge_service.graft_diseases(working, branch, {mine})
    branch._save()
    content = format_owl(branch.path.read_bytes())

    changed = [line for line in difflib.unified_diff(before, content.decode().split("\n"), n=0)
               if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    # The replaced synonyms and the change-log entry the edit records.
    assert changed and all("<ARI_Synonym" in line or "<ARI_ChangeLog" in line for line in changed)
    assert any("only-change" in line for line in changed)
    # Formatting is idempotent, and it is the same ontology owlready2 wrote.
    assert format_owl(content) == content
    branch.path.write_bytes(content)
    assert "only-change" in OntologyService(str(branch.path)).get_disease_detail(mine)["synonyms"]


def _subjects(root):
    return {e.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about") for e in root} - {None}


def test_a_publish_writes_only_disease_module_statements(pair):
    import xml.etree.ElementTree as ET
    from pathlib import Path

    from app import config
    from app.owl_format import format_owl
    working, branch = pair
    mine = _iris(working, 1)[0]
    working.update_disease(mine, {"synonyms": "only-change"}, editor="ada")
    working.add_item(mine, "symptoms", {"name": "Polyuria"}, editor="ada")
    merge_service.graft_diseases(working, branch, {mine})
    branch._save()
    published = _subjects(ET.fromstring(format_owl(branch.path.read_bytes())))
    fixtures = Path(config.ONTOLOGY_FILE).parent
    elsewhere = set().union(*(_subjects(ET.parse(fixtures / f).getroot())
                              for f in ("ari_core.owl", "ari_anatomy.owl", "ari_symptoms.owl")))
    assert published and not published & elsewhere
