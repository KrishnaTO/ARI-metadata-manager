"""Rebasing a curator's edits onto the current source branch before they are committed.

A working copy is created once, when the curator first edits something, and
lives for days. Publishing used to commit that whole file: every disease the
curator never opened went up exactly as it stood when the copy was made, so a
one-disease review reverted everything merged into the branch since — 208
synonyms, 57 clinical subtypes and ~100 review records over two weeks (issue
#146). The mapping files never lost a row because they were re-read from the
source branch at publish time and appended to; this does the same for the
ontology.

Only the diseases in ``workspace.touched()`` are carried across. Everything else
in the commit is the source branch's own bytes, so a publish can no longer
express an opinion about a record the curator did not edit.
"""
import logging

from .schema import CATEGORIES

log = logging.getLogger(__name__)

# Object properties that link a disease to the item individuals it owns. Those
# individuals carry their own triples (label, description, source), so grafting
# the disease alone would leave every symptom and treatment it names dangling.
ITEM_LINKS = tuple(dict.fromkeys(spec["link"] for spec in CATEGORIES.values()))

CHANGELOG = "ARI_ChangeLog"


class Conflict(RuntimeError):
    """A disease this curator edited also changed on the source branch.

    Silently winning is what caused the incident this module exists to prevent,
    so the publish stops and the curator is told which records collided.
    """

    def __init__(self, diseases: list):
        self.diseases = diseases
        names = ", ".join(d["name"] for d in diseases)
        super().__init__(f"changed on the source branch since this session began: {names}")


def _changelog(svc, iri):
    """``(entity, changelog entries)`` for ``iri``, or ``(None, [])`` if absent."""
    try:
        e = svc._entity(iri)
    except KeyError:
        return None, []
    return e, svc._get_annotation(e, svc.base + CHANGELOG)


def upstream_edits(working, baseline, iris) -> list:
    """Diseases in ``iris`` that someone else has edited on the source branch.

    Every write path appends to the disease's own ``ARI_ChangeLog``, so an entry
    present on the source branch and absent from the working copy is an edit made
    after this curator's copy was taken. Returns
    ``[{iri, name, entries}]`` — empty when the rebase is safe.
    """
    out = []
    for iri in sorted(iris):
        base_e, base_log = _changelog(baseline, iri)
        if base_e is None:
            continue                       # new here, or gone upstream: nothing to collide with
        _, mine = _changelog(working, iri)
        unseen = [x for x in base_log if x not in set(mine)]
        if unseen:
            out.append({"iri": iri, "name": baseline._get_label(base_e), "entries": unseen})
    return out


def _item_iris(svc, iri) -> set:
    """IRIs of the item individuals ``iri`` owns, in ``svc``."""
    w = svc.world
    s = w._abbreviate(iri, False)
    if s is None:
        return set()
    out = set()
    for link in ITEM_LINKS:
        p = w._abbreviate(svc.base + link, False)
        if p is None:
            continue
        for o in w._get_obj_triples_sp_o(s, p):
            if o < 0:
                raise ValueError(f"{iri} links to an anonymous node via {link}")
            out.add(w._unabbreviate(o))
    return out


def _ensure_property(src, dst, p_iri):
    """Declare ``p_iri`` in ``dst`` if the source branch has never seen it.

    A curator's session can be the first to use an annotation property —
    ``ARI_EnrichmentSource`` was introduced exactly that way — and a property
    used but never declared makes the published file fail its own schema check.
    """
    if not p_iri.startswith(src.base):
        return                             # rdf/rdfs/owl and friends need no declaration
    if dst.world._abbreviate(p_iri, False) is not None:
        return
    s_src = src.world._abbreviate(p_iri, False)
    s_dst = dst.world._abbreviate(p_iri)
    for p, o in list(src.world._get_obj_triples_s_po(s_src)):
        dst.onto._add_obj_triple_spo(s_dst, dst.world._abbreviate(src.world._unabbreviate(p)),
                                     dst.world._abbreviate(src.world._unabbreviate(o)))


def _graft(src, dst, iri):
    """Replace everything ``dst`` says about ``iri`` with what ``src`` says.

    An IRI absent from ``src`` is deleted from ``dst`` — that is a curator
    removing an item, and leaving the old triples behind would resurrect it.
    """
    sw, dw = src.world, dst.world
    s_src = sw._abbreviate(iri, False)
    s_dst = dw._abbreviate(iri)
    dst.onto._del_obj_triple_spo(s_dst, None, None)
    dst.onto._del_data_triple_spod(s_dst, None, None, None)
    if s_src is None:
        return

    for p, o in list(sw._get_obj_triples_s_po(s_src)):
        if o < 0:
            # Anonymous class expressions would need their whole subtree copied.
            # No disease or item in this ontology has one; refusing beats
            # committing a record with a piece of itself missing.
            raise ValueError(f"{iri} carries an anonymous node under {sw._unabbreviate(p)}")
        p_iri = sw._unabbreviate(p)
        _ensure_property(src, dst, p_iri)
        dst.onto._add_obj_triple_spo(s_dst, dw._abbreviate(p_iri),
                                     dw._abbreviate(sw._unabbreviate(o)))

    for p, o, d in list(sw._get_data_triples_s_pod(s_src)):
        p_iri = sw._unabbreviate(p)
        _ensure_property(src, dst, p_iri)
        # ``d`` is a datatype storid, or a language tag such as "@en".
        dtype = dw._abbreviate(sw._unabbreviate(d)) if isinstance(d, int) and d > 0 else d
        dst.onto._add_data_triple_spod(s_dst, dw._abbreviate(p_iri), o, dtype)


def graft_diseases(src, dst, iris) -> int:
    """Write ``src``'s version of each disease in ``iris`` over ``dst``'s.

    Mutates ``dst`` in place and returns the number of individuals grafted. It
    runs in both directions. Publishing grafts the working copy onto the source
    branch and commits *that*, so every untouched record in the commit is the
    branch's own; taking the branch's version of a disease the curator collided
    with grafts the other way, over their working copy.
    """
    grafted = 0
    for iri in sorted(iris):
        # Both sides' items: one added on one side exists only there, and one
        # deleted on one side must not come back from the other.
        for target in sorted({iri} | _item_iris(src, iri) | _item_iris(dst, iri)):
            _graft(src, dst, target)
            grafted += 1
    log.info("Grafted %d disease(s) (%d individuals) onto %s", len(iris), grafted, dst.path.name)
    return grafted
