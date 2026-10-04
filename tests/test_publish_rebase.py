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


# ----------------------------------------------------------------- file layout
def _block(text, iri):
    """The lines of ``iri``'s entity block in serialised OWL."""
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if f'rdf:about="{iri}"' in line)
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("</"))
    return lines[start:end + 1]


def test_a_grafted_disease_keeps_its_element_and_line_order(pair):
    working, branch = pair
    mine = _iris(working, 1)[0]
    before = _block(branch.path.read_text(encoding="utf-8"), mine)
    working.update_disease(mine, {"synonyms": "kept-in-place"}, editor="ada")

    merge_service.graft_diseases(working, branch, {mine})
    branch._save()

    after = _block(branch.path.read_text(encoding="utf-8"), mine)
    # Rewritten from sets, the type was picked at random: <AutoimmuneDisease>
    # half the time, which the data repo's validator read as a deletion (ARI#105).
    assert after[0].startswith("<owl:NamedIndividual ")
    # Every line both versions have is in the same order.
    assert [line for line in after if line in before] == [line for line in before if line in after]


def test_a_publish_keeps_the_branchs_file_order(pair):
    from app.ontology_service import OntologyService
    from app.owl_splice import splice
    working, branch = pair
    mine = _iris(working, 1)[0]
    # A branch last written by something other than owlready2 — a hand repair
    # moved this block to the end. Saved as-is, owlready2 would move it back.
    text = branch.path.read_text(encoding="utf-8")
    block = "\n".join(_block(text, mine)) + "\n\n"
    text = text.replace(block, "")
    text = text.replace("\n\n\n</rdf:RDF>", "\n\n" + block.rstrip("\n") + "\n\n\n</rdf:RDF>")
    branch.path.write_bytes(text.encode("utf-8"))   # LF, as GitHub serves it
    branch = OntologyService(str(branch.path))
    original = branch.path.read_bytes()
    working.update_disease(mine, {"synonyms": "only-change"}, editor="ada")
    working.add_item(mine, "symptoms", {"name": "Spliced symptom"}, editor="ada")

    merge_service.graft_diseases(working, branch, {mine})
    branch._save()
    content = splice(original, branch.path.read_bytes())

    old, new = original.decode().split("\n"), content.decode().split("\n")
    # Nothing moved: every line that survived is where it was. Only the
    # replaced synonym went.
    kept = [line for line in old if line in new]
    it = iter(new)
    assert all(line in it for line in kept)
    assert all("<ARI_Synonym " in line for line in old if line not in new)
    added = [line for line in new if line not in old]
    assert any("only-change" in line for line in added)
    assert any("Spliced symptom" in line for line in added)
    # And it is the same ontology owlready2 wrote.
    branch.path.write_bytes(content)
    assert "only-change" in OntologyService(str(branch.path)).get_disease_detail(mine)["synonyms"]
