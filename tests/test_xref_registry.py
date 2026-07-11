"""The shared cross-reference registry is the single source of truth. These lock
in that the derived Python maps and the API endpoint stay consistent with it, so a
database can only be added/changed in one place."""
from fastapi.testclient import TestClient

from app import sssom_service, xref_registry
from app.main import app
from app.ontology_service import OntologyService


def test_every_entry_is_well_formed():
    seen_suffix = set()
    for d in xref_registry.XREF_DATABASES:
        assert d["ari_suffix"].startswith("ARI_")
        assert d["ari_suffix"] not in seen_suffix          # annotation suffixes are unique
        seen_suffix.add(d["ari_suffix"])
        if d["review"]:                                    # review columns must link out
            assert d["link"] and "{" in d["link"]
            assert d["search"] and "{name}" in d["search"]


def test_sssom_prefix_is_the_registry_prefix():
    assert sssom_service.PREFIX is xref_registry.PREFIX
    # SNOMED backs both snomed and dxcode, so PREFIX_TO_DBS groups them.
    assert set(sssom_service.PREFIX_TO_DBS["SNOMEDCT"]) == {"snomed", "dxcode"}


def test_curie_map_covers_every_object_prefix():
    for prefix in xref_registry.PREFIX.values():
        assert prefix in sssom_service.CURIE_MAP
    for meta in ("ARI", "skos", "semapv", "orcid"):       # SSSOM-specific, still present
        assert meta in sssom_service.CURIE_MAP


def test_ontology_suffixes_come_from_registry():
    assert OntologyService.XREF_SUFFIXES is xref_registry.XREF_SUFFIXES


def test_public_list_and_endpoint_agree():
    pub = xref_registry.public_list()
    assert {"snomed", "omop", "doid", "dxcode"} <= {d["key"] for d in pub}
    for d in pub:
        assert set(d) == {"key", "label", "prefix", "link", "search", "noframe", "review", "main_app"}
    with TestClient(app) as client:
        r = client.get("/api/v2/xref-databases")
        assert r.status_code == 200
        assert r.json() == pub
