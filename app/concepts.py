"""Which shared ARI symptom (ARIS) class a per-disease Symptom's label names.

The ARIS classes live in ``ari_symptoms.owl``, which the disease module imports.
A Symptom's ``sharedConcept`` is the class whose ``rdfs:label`` or
``skos:altLabel`` is the Symptom's own label (KrishnaTO/ARI#152).
"""
from owlready2 import World, label

SYMPTOMS_ONTOLOGY = "http://www.aurint.org/ontologies/ari_symptoms"
ALT_LABEL = "http://www.w3.org/2004/02/skos/core#altLabel"


def loaded_ontology(world: World, iri: str):
    """The ontology ``iri`` names in ``world``. Raises when it is not loaded."""
    for onto in world.ontologies.values():
        if onto.base_iri.rstrip("#/") == iri:
            return onto
    raise RuntimeError(f"{iri} is not loaded")


def concept_index(world: World, ontology_iri: str) -> dict[str, str]:
    """``{stripped rdfs:label or skos:altLabel: class IRI}`` for the classes the
    ontology ``ontology_iri`` declares, the same module the data repo's
    validator reads. One string naming two classes is an error: the link it
    would make is ambiguous."""
    alt_label = world[ALT_LABEL]
    if alt_label is None:
        raise RuntimeError("skos:altLabel is not declared in ari_core.owl")
    index: dict[str, str] = {}
    for cls in loaded_ontology(world, ontology_iri).classes():
        for text in [*label[cls], *alt_label[cls]]:
            text = str(text).strip()
            if index.setdefault(text, cls.iri) != cls.iri:
                raise ValueError(f"{text!r} names both {index[text]} and {cls.iri}")
    return index
