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
from dataclasses import dataclass, field

from .diff_service import FIELDS
from .ontology_service import OntologyService
from .schema import CATEGORIES

log = logging.getLogger(__name__)

# Object properties that link a disease to the item individuals it owns. Those
# individuals carry their own triples (label, description, source), so grafting
# the disease alone would leave every symptom and treatment it names dangling.
ITEM_LINKS = tuple(dict.fromkeys(spec["link"] for spec in CATEGORIES.values()))

CHANGELOG = "ARI_ChangeLog"

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"


def _multi_valued(base: str) -> frozenset:
    """Predicates whose values are a set, merged value by value.

    Everything else is single-valued: a label, a definition, an item's
    description. Named rather than guessed from the data, because a synonym
    list that happens to hold one value is still a list.
    """
    suffixes = {suffix for kind, suffix, _ in OntologyService.EDITABLE.values()
                if kind == "multi_ann"}
    suffixes |= {CHANGELOG, "ARI_EnrichmentSource", "hasParentDisease",
                 "hasParentCategory", *ITEM_LINKS}
    return frozenset({base + s for s in suffixes} | {RDF_TYPE, RDFS + "seeAlso"})


def _field_labels(base: str) -> dict:
    """Display label for each predicate a curator can edit."""
    names = dict(FIELDS)
    out = {RDFS + "label": "Label", RDFS + "comment": "Definition", RDFS + "seeAlso": "HPO id"}
    for key, (_, suffix, _) in OntologyService.EDITABLE.items():
        if suffix:
            out[base + suffix] = names.get(key, key)
    for spec in CATEGORIES.values():
        for f in spec["fields"]:
            if f["kind"] == "data":
                out.setdefault(base + f["key"], f["label"])
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


def _ensure_property(dst, p_iri, sources):
    """Declare ``p_iri`` in ``dst`` if it has never seen it.

    A curator's session can be the first to use an annotation property —
    ``ARI_EnrichmentSource`` was introduced exactly that way — and a property
    used but never declared makes the published file fail its own schema check.
    """
    if not p_iri.startswith(dst.base) or dst.world._abbreviate(p_iri, False) is not None:
        return                             # rdf/rdfs/owl need no declaration; or already known
    src = next(s for s in sources if s.world._abbreviate(p_iri, False) is not None)
    s_src = src.world._abbreviate(p_iri, False)
    s_dst = dst.world._abbreviate(p_iri)
    for p, o in list(src.world._get_obj_triples_s_po(s_src)):
        dst.onto._add_obj_triple_spo(s_dst, dst.world._abbreviate(src.world._unabbreviate(p)),
                                     dst.world._abbreviate(src.world._unabbreviate(o)))


def _triples(svc, iri):
    """Everything ``svc`` says about ``iri`` as ``{predicate: {value}}``, or None.

    Values are world-independent: object values are IRIs, data values carry
    their datatype IRI (or language tag), so two services compare directly.
    """
    w = svc.world
    s = w._abbreviate(iri, False)
    if s is None:
        return None
    out = {}
    for p, o in w._get_obj_triples_s_po(s):
        if o < 0:
            # Anonymous class expressions would need their whole subtree copied.
            # No disease or item in this ontology has one; refusing beats
            # committing a record with a piece of itself missing.
            raise ValueError(f"{iri} carries an anonymous node under {w._unabbreviate(p)}")
        out.setdefault(w._unabbreviate(p), set()).add(("o", w._unabbreviate(o)))
    for p, o, d in w._get_data_triples_s_pod(s):
        dt = w._unabbreviate(d) if isinstance(d, int) and d > 0 else d
        out.setdefault(w._unabbreviate(p), set()).add(("d", o, dt))
    return out or None


def _write(dst, iri, props, sources):
    """Replace everything ``dst`` says about ``iri`` with ``props``.

    ``None`` deletes the subject — a curator removing an item, where leaving the
    old triples behind would resurrect it.
    """
    dw = dst.world
    s = dw._abbreviate(iri)
    dst.onto._del_obj_triple_spo(s, None, None)
    dst.onto._del_data_triple_spod(s, None, None, None)
    for p_iri, values in (props or {}).items():
        _ensure_property(dst, p_iri, sources)
        p = dw._abbreviate(p_iri)
        for v in values:
            if v[0] == "o":
                dst.onto._add_obj_triple_spo(s, p, dw._abbreviate(v[1]))
            else:
                _, o, dt = v
                d = dw._abbreviate(dt) if isinstance(dt, str) and not dt.startswith("@") else dt
                dst.onto._add_data_triple_spod(s, p, o, d)


def _graft(src, dst, iri):
    """Replace everything ``dst`` says about ``iri`` with what ``src`` says."""
    _write(dst, iri, _triples(src, iri), (src,))


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


@dataclass
class Merge:
    """One disease merged: the triples to write per subject, and what is left to decide."""
    name: str
    subjects: dict = field(default_factory=dict)      # subject iri -> props, None = delete
    conflicts: list = field(default_factory=list)
    upstream_log: list = field(default_factory=list)


def _show(values) -> str:
    return ", ".join(sorted(str(v[1]) for v in values)) if values else "(empty)"


def _name(*props) -> str:
    for p in props:
        for v in sorted((p or {}).get(RDFS + "label", ()), key=str):
            return str(v[1])
    return ""


def _merge_subject(s, b, m, t, has_base, multi, labels, choices, conflicts):
    """The merged props for one subject; appends to ``conflicts`` what it cannot decide."""
    if m == t:
        return m
    if has_base and m == b:
        return t
    if has_base and t == b:
        return m
    if m is None or t is None:
        if not has_base:
            return m or t                  # present on one side only: without a base, keep it
        key = f"{s}|"                      # one side deleted it, the other edited it
        pick = choices.get(key)
        if pick is None:
            conflicts.append({"key": key, "label": "Whole record", "subject": _name(m, t, b),
                              "base": "present",
                              "mine": "removed" if m is None else "edited",
                              "theirs": "removed" if t is None else "edited"})
            return m
        return m if pick == "mine" else t
    props = {}
    for p in sorted(set(m) | set(t) | set(b or {})):
        B, M, T = (b or {}).get(p, set()), m.get(p, set()), t.get(p, set())
        if p in multi:
            if not has_base:
                B = M & T
            v = (B & M & T) | (M - B) | (T - B)
        elif M == T:
            v = M
        elif has_base and M == B:
            v = T
        elif has_base and T == B:
            v = M
        else:
            key = f"{s}|{p}"
            pick = choices.get(key)
            if pick is None:
                conflicts.append({"key": key, "label": labels.get(p, p.rsplit("#", 1)[-1].rsplit("/", 1)[-1]),
                                  "subject": _name(m, t, b),
                                  "base": _show(B) if has_base else None,
                                  "mine": _show(M), "theirs": _show(T)})
                v = M
            else:
                v = M if pick == "mine" else T
        if v:
            props[p] = v
    return props or None


def merge_disease(base, mine, theirs, iri, choices=None) -> Merge:
    """Three-way merge of one disease and every item individual hanging off it.

    ``base`` is the ancestor both copies descend from, or None for a working copy
    made before ancestors were recorded. Nothing is written; see :func:`apply`.
    """
    choices = choices or {}
    has_base = base is not None
    multi, labels = _multi_valued(mine.base), _field_labels(mine.base)
    sides = [x for x in (base, mine, theirs) if x is not None]
    subjects = sorted({iri}.union(*(_item_iris(x, iri) for x in sides)))
    b0, m0, t0 = ((_triples(x, iri) if x is not None else None) for x in (base, mine, theirs))
    out = Merge(name=_name(t0, m0, b0))
    clog = mine.base + CHANGELOG
    mine_log = (m0 or {}).get(clog, set())
    out.upstream_log = sorted(str(v[1]) for v in (t0 or {}).get(clog, set()) - mine_log)

    for s in subjects:
        b = _triples(base, s) if has_base else None
        out.subjects[s] = _merge_subject(s, b, _triples(mine, s), _triples(theirs, s),
                                         has_base, multi, labels, choices, out.conflicts)

    # An item's link follows the item: a conflict settled in the item's favour
    # keeps its link even where one side dropped it, and a deleted item takes
    # its link with it.
    disease = out.subjects.get(iri)
    if disease is not None:
        link_preds = {mine.base + link for link in ITEM_LINKS}
        linked = {}
        for x in (b0, m0, t0):
            for p in link_preds:
                for v in (x or {}).get(p, ()):
                    linked[v[1]] = p
        for p in link_preds:
            vals = {("o", i) for i, lp in linked.items()
                    if lp == p and out.subjects.get(i) is not None}
            if vals:
                disease[p] = vals
            else:
                disease.pop(p, None)
    return out


def apply(dst, merge: Merge, sources) -> None:
    """Write a conflict-free merge into ``dst``."""
    if merge.conflicts:
        raise ValueError(f"{merge.name} still has {len(merge.conflicts)} unresolved conflict(s)")
    for s, props in merge.subjects.items():
        _write(dst, s, props, sources)


def _record(svc, iri) -> dict:
    """A disease and its items as triple snapshots, for equality checks."""
    return {s: _triples(svc, s) for s in sorted({iri} | _item_iris(svc, iri))}


def merge_into(working, ancestor, theirs, iris, touched, choices=None) -> dict:
    """Merge ``theirs`` into ``working`` for each disease in ``iris``.

    Every disease that merges without an open question is written into
    ``working``, and its ancestor moves up to ``theirs`` so the next merge sees
    only newer changes. A disease with a conflict is left exactly as it was, on
    both. Without an ancestor, a disease the curator has not touched simply
    takes the branch's version — there is no work of theirs in it to protect.

    ``merged`` lists the diseases written into ``working`` and ``advanced`` those
    whose ancestor moved, so the caller saves only what actually changed.
    """
    choices = choices or {}
    merged, conflicts, clean = [], [], []
    for iri in sorted(iris):
        if ancestor is None and iri not in touched:
            if _record(working, iri) != _record(theirs, iri):
                graft_diseases(theirs, working, {iri})
                merged.append(iri)
            continue
        m = merge_disease(ancestor, working, theirs, iri, choices.get(iri))
        if m.conflicts:
            conflicts.append({"iri": iri, "name": m.name,
                              "upstream_log": m.upstream_log, "fields": m.conflicts})
            continue
        clean.append(iri)
        # The merge's subjects are a superset of the working copy's, so
        # comparing over them is exact.
        current = _record(working, iri)
        if any(m.subjects[s] != current.get(s) for s in m.subjects):
            apply(working, m, (working, theirs))
            merged.append(iri)
    advanced = []
    if ancestor is not None:
        advanced = [i for i in clean if _record(ancestor, i) != _record(theirs, i)]
        if advanced:
            graft_diseases(theirs, ancestor, advanced)
    return {"merged": merged, "conflicts": conflicts, "advanced": advanced}
