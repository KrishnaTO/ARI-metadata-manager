"""Which shared ARI class (ARIS symptom, ARIB biomarker) a per-disease entry's label names.

The ARIS classes live in ``ari_symptoms.owl`` and the ARIB classes in
``ari_biomarkers.owl``; the disease module imports both. An entry's
``sharedConcept`` is the class whose ``rdfs:label`` or ``skos:altLabel`` is the
entry's own label (KrishnaTO/ARI#152). A biomarker entry looks only among the
classes of its own kind: an antibody entry never links an antigen class.
"""
from owlready2 import World, label

SYMPTOMS_ONTOLOGY = "http://www.aurint.org/ontologies/ari_symptoms"
BIOMARKERS_ONTOLOGY = "http://www.aurint.org/ontologies/ari_biomarkers"
# The core class each biomarker category's ARIB classes sit under.
KIND_CLASSES = {
    "antibodies": "http://www.aurint.org/ontologies/ari-disease#Autoantibody",
    "antigens": "http://www.aurint.org/ontologies/ari-disease#Antigen",
    "biomarkers": "http://www.aurint.org/ontologies/ari-disease#ClinicalBiomarker",
}
ALT_LABEL = "http://www.w3.org/2004/02/skos/core#altLabel"


def loaded_ontology(world: World, iri: str):
    """The ontology ``iri`` names in ``world``. Raises when it is not loaded."""
    for onto in world.ontologies.values():
        if onto.base_iri.rstrip("#/") == iri:
            return onto
    raise RuntimeError(f"{iri} is not loaded")


def concept_index(world: World, ontology_iri: str, under: str | None = None) -> dict[str, str]:
    """``{stripped rdfs:label or skos:altLabel: class IRI}`` for the classes the
    ontology ``ontology_iri`` declares, the same module the data repo's
    validator reads. With ``under``, only those that are subclasses
    (transitively) of the class ``under``. One string naming two classes is an
    error: the link it would make is ambiguous."""
    alt_label = world[ALT_LABEL]
    if alt_label is None:
        raise RuntimeError("skos:altLabel is not declared in ari_core.owl")
    index: dict[str, str] = {}
    for cls in loaded_ontology(world, ontology_iri).classes():
        if under and not any(a.iri == under for a in cls.ancestors()):
            continue
        for text in [*label[cls], *alt_label[cls]]:
            text = str(text).strip()
            if index.setdefault(text, cls.iri) != cls.iri:
                raise ValueError(f"{text!r} names both {index[text]} and {cls.iri}")
    return index
