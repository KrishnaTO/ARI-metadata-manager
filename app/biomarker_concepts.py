"""What a curator sees on expanding an antibody, antigen or clinical-biomarker row.

Each such entry is one disease's record of a biomarker. Its ``sharedConcept``
names the ARIB class in ``ari_biomarkers.owl`` that every disease's entry for
the same biomarker shares (or several, for a bundle entry), and the concept is
where the relatives live: external terms, parent and child concepts, the
antigen an antibody targets, body parts and symptoms. Which other diseases have
an entry for the concept, and which concepts turn up in the same diseases, are
worked out here from the disease module on every request.

Read-only. Links to IRIs (``sharedConcept``, ``targetsAntigen``, the SKOS
matches) are read as triples: owlready2 cannot resolve an HPO IRI to an
entity, since HPO has no slice.
"""
from collections import defaultdict

from owlready2 import Thing, ThingClass, label

from .concepts import ALT_LABEL, KIND_CLASSES
from .errors import NotFound
from .schema import CATEGORIES

ARIB = "http://www.aurint.org/ontologies/ari-biomarkers#"
DISEASE = "http://www.aurint.org/ontologies/ari-disease#"
OBO_ID = "http://www.geneontology.org/formats/oboInOwl#id"
SKOS = "http://www.w3.org/2004/02/skos/core#"
SHARED_CONCEPT = DISEASE + "sharedConcept"
NO_CONCEPT = DISEASE + "ARI_NoConcept"
TARGETS_ANTIGEN = DISEASE + "targetsAntigen"
EXPRESSED_IN = DISEASE + "expressedIn"
ASSOCIATED_SYMPTOM = DISEASE + "associatedSymptom"
MATCHES = ("exactMatch", "closeMatch", "broadMatch", "narrowMatch", "relatedMatch")
# The data repo's kind name for each app category: a clinical biomarker is a test.
KINDS = {"antibodies": "antibodies", "antigens": "antigens", "biomarkers": "tests"}
APPEARS_WITH_LIMIT = 10
# External IRI forms (KrishnaTO/ARI biomarker-concepts global constraints).
_CURIE_PREFIXES = (
    ("http://purl.obolibrary.org/obo/HP_", "HP:"),
    ("http://purl.obolibrary.org/obo/PR_", "PR:"),
    ("http://purl.obolibrary.org/obo/CHEBI_", "CHEBI:"),
    ("http://identifiers.org/hgnc/", "HGNC:"),
    ("https://loinc.org/", "LOINC:"),
)


def curie(iri: str) -> str:
    """``PR:Q05329`` for ``http://purl.obolibrary.org/obo/PR_Q05329``, and so on."""
    for prefix, short in _CURIE_PREFIXES:
        if iri.startswith(prefix):
            return short + iri[len(prefix):]
    raise RuntimeError(f"{iri} is not an HPO, PR, ChEBI, HGNC or LOINC IRI")


def _objects(world, subject: str, prop: str) -> list[str]:
    """The distinct IRIs ``subject`` points to by ``prop``, sorted."""
    s, p = world._abbreviate(subject, False), world._abbreviate(prop, False)
    if s is None or p is None:
        return []
    return sorted({world._unabbreviate(o) for o in world._get_obj_triples_sp_o(s, p)})


def _subjects(world, prop: str, obj: str) -> list[str]:
    """The IRIs that point to ``obj`` by ``prop``, sorted."""
    p, o = world._abbreviate(prop, False), world._abbreviate(obj, False)
    if p is None or o is None:
        return []
    return sorted(world._unabbreviate(s) for s in world._get_obj_triples_po_s(p, o))


def _label(svc, iri: str) -> str:
    e = svc.world[iri]
    if e is None:
        raise RuntimeError(f"{iri} is linked but is not in the loaded ontology")
    return svc._get_label(e)


def _by_label(ref: dict) -> tuple:
    return ref["label"].casefold(), ref["iri"]


def _ref(svc, iri: str) -> dict:
    return {"iri": iri, "label": _label(svc, iri)}


def _refs(svc, iris) -> list[dict]:
    return sorted((_ref(svc, i) for i in iris), key=_by_label)


def _external(svc, iri: str) -> list[dict]:
    """The concept's SKOS matches. The label is the slice class's; an HPO term
    has no slice, so its label is None."""
    out = []
    for name in MATCHES:
        for target in _objects(svc.world, iri, SKOS + name):
            e = svc.world[target]
            labels = label[e] if e is not None else []
            out.append({"predicate": "skos:" + name, "iri": target, "curie": curie(target),
                        "label": str(labels[0]) if labels else None})
    return out


def _arib_parents(cls) -> list:
    return [p for p in cls.is_a if isinstance(p, ThingClass) and p.iri.startswith(ARIB)]


def _targets(svc, cls) -> list[str]:
    """The antigen IRIs an antibody concept targets. A child concept (an
    isotype) inherits its parent's target implicitly, so with no
    ``targetsAntigen`` of its own this is the nearest ancestor's."""
    level, seen = [cls], {cls.iri}
    while level:
        found = sorted({t for c in level for t in _objects(svc.world, c.iri, TARGETS_ANTIGEN)})
        if found:
            return found
        level = [p for c in level for p in _arib_parents(c) if p.iri not in seen]
        seen.update(p.iri for p in level)
    return []


def _kind(cls) -> str:
    ancestors = {a.iri for a in cls.ancestors()}
    for category, kind_class in KIND_CLASSES.items():
        if kind_class in ancestors:
            return KINDS[category]
    raise RuntimeError(f"{cls.iri} is under none of Autoantibody, Antigen, ClinicalBiomarker")


def _entry_category(svc, e) -> str:
    """The app category of a biomarker entry, or NotFound for anything else."""
    if isinstance(e, Thing):
        for category in KIND_CLASSES:
            if svc.world[svc.base + CATEGORIES[category]["cls"]] in e.is_a:
                return category
    raise NotFound(f"{e.iri} is not an antibody, antigen or clinical biomarker entry")


def _usage(svc) -> dict[str, set[str]]:
    """``{concept IRI: {disease IRI}}``: the live (not obsolete) diseases with a
    live antibody, antigen or clinical-biomarker entry linked to each concept."""
    usage = defaultdict(set)
    for disease in svc._all_diseases():
        if svc._is_obsolete(disease):
            continue
        for category in KIND_CLASSES:
            for entry in svc._get_objects(disease, svc.base + CATEGORIES[category]["link"]):
                if svc._is_obsolete(entry):
                    continue
                for concept in _objects(svc.world, entry.iri, SHARED_CONCEPT):
                    usage[concept].add(disease.iri)
    return usage


def _concept(svc, iri: str, own_diseases: set[str], usage: dict[str, set[str]],
             antibodies: list) -> dict:
    cls = svc.world[iri]
    if not isinstance(cls, ThingClass):
        raise RuntimeError(f"sharedConcept {iri} is not a class in the loaded ontology")
    kind = _kind(cls)
    targets = _targets(svc, cls) if kind == "antibodies" else []
    target_of = {ab.iri: set(_targets(svc, ab)) for ab in antibodies}
    # Parents and children already show under their own headings.
    family = {c.iri for c in [*cls.ancestors(), *cls.descendants(world=svc.world)]}
    diseases = usage.get(iri, set())
    shared = {other: len(diseases & where) for other, where in usage.items() if other != iri}
    appears_with = sorted(({**_ref(svc, other), "shared": n} for other, n in shared.items() if n),
                          key=lambda r: (-r["shared"], *_by_label(r)))
    return {
        "iri": iri,
        "id": next(iter(svc._get_annotation(cls, OBO_ID)), None),
        "label": svc._get_label(cls),
        "kind": kind,
        "alt_labels": svc._get_annotation(cls, ALT_LABEL),
        "external": _external(svc, iri),
        "parents": _refs(svc, (p.iri for p in _arib_parents(cls))),
        "children": _refs(svc, (c.iri for c in cls.subclasses(world=svc.world)
                                if c.iri.startswith(ARIB))),
        "targets": [{**_ref(svc, t), "external": _external(svc, t)} for t in targets],
        "targeted_by": _refs(svc, (ab for ab, ts in target_of.items() if iri in ts)),
        "same_target": _refs(svc, (ab for ab, ts in target_of.items()
                                   if ab not in family and ts & set(targets))),
        "appears_with": appears_with[:APPEARS_WITH_LIMIT],
        "also_in": _refs(svc, diseases - own_diseases),
        "body_parts": _refs(svc, _objects(svc.world, iri, EXPRESSED_IN)),
        "symptoms": _refs(svc, _objects(svc.world, iri, ASSOCIATED_SYMPTOM)),
    }


def concept_detail(svc, entry_iri: str) -> dict:
    """The expanded row for one biomarker entry: its status, and each concept
    it links with that concept's relatives.

    ``status`` is ``linked`` (one concept), ``bundle`` (several), ``no_concept``
    (``ARI_NoConcept``, whose value is ``reason``) or ``unlinked``. ``also_in``
    leaves out the entry's own disease. ``appears_with`` counts, for every
    other concept, the diseases it shares with this one (across all three
    kinds), and keeps the ten that share most. Obsolete entries and obsolete
    diseases count nowhere. ``targets`` is an antibody's own
    ``targetsAntigen``, or else its nearest ancestor's; ``targeted_by`` and
    ``same_target`` use the same inherited targets. ``same_target`` holds the
    other antibody concepts with a target in common, leaving out the concept's
    own ancestors and descendants (they are under ``parents`` and
    ``children``). Raises NotFound for an IRI that is not a biomarker entry."""
    e = svc._entity(entry_iri)
    category = _entry_category(svc, e)
    concepts = _objects(svc.world, entry_iri, SHARED_CONCEPT)
    if not concepts:
        reason = next(iter(svc._get_annotation(e, NO_CONCEPT)), None)
        return {"entry": entry_iri, "status": "unlinked" if reason is None else "no_concept",
                "reason": reason, "concepts": []}
    own = set(_subjects(svc.world, svc.base + CATEGORIES[category]["link"], entry_iri))
    usage = _usage(svc)
    antibody_root = svc.world[KIND_CLASSES["antibodies"]]
    antibodies = [c for c in antibody_root.descendants(include_self=False, world=svc.world)
                  if c.iri.startswith(ARIB)]
    details = [_concept(svc, c, own, usage, antibodies) for c in concepts]
    return {"entry": entry_iri, "status": "bundle" if len(concepts) > 1 else "linked", "reason": None,
            "concepts": sorted(details, key=_by_label)}
