"""Build a human-readable summary of what changed between the current ontology
and a baseline (the source branch), for inclusion in a pull-request body.
"""

# field key -> display label (covers the editable + key display fields)
FIELDS = [
    ("name", "Label"), ("definition", "Definition"), ("synonyms", "Synonyms"),
    ("clinical_subtypes", "Clinical subtypes"),
    ("snomed", "SNOMED"), ("omop", "OMOP"), ("dxcode", "Concept code (DXCODE)"),
    ("doid", "DOID"), ("umls", "UMLS"), ("mondo", "MONDO"), ("icd10", "ICD-10"),
    ("mesh", "MeSH"), ("nci", "NCI"),
    ("def_source", "Definition source"), ("obsolete", "Obsolete"), ("version", "Version"),
    ("disease_category", "Category"), ("evidence_quality", "Evidence quality"),
    ("prevalence_per_100k", "Prevalence /100k"), ("prevalence_value", "Estimated cases"),
    ("incidence_rate", "Incidence rate"), ("demographic_bias", "Demographic bias"),
    ("age_range", "Age range"), ("prevalence_desc", "Prevalence description"),
]


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v)


def _cell(v):
    s = _fmt(v).replace("|", "\\|").replace("\n", " ")
    return (s[:300] + "…") if len(s) > 300 else (s or "_(empty)_")


def _rows(service):
    out = {}
    for it in service.get_diseases_list():
        d = service.get_disease_detail(it["iri"])
        key = (d.get("ari_id") or [None])[0] or d.get("iri")
        out[key] = d
    return out


def list_changes(current_service, baseline_service, touched_iris=None) -> list[dict]:
    """What changed, per disease, as data rather than markdown.

    The same comparison :func:`build_change_summary` renders, returned as
    ``[{iri, ari_id, name, is_new, fields: [{field, label, previous, new}]}]``.
    The publish dialog used to offer a free-text title box and nothing else, so a
    curator opening a pull request could not see which diseases it carried
    (issue #25); this is what it lists, and what the generated title is built
    from. Ordered by disease name, like the summary.
    """
    cur = _rows(current_service)
    base = _rows(baseline_service)

    def touched(d):
        return touched_iris is None or d.get("iri") in touched_iris

    out = []
    for key, d in sorted(cur.items(), key=lambda kv: _fmt(kv[1].get("name"))):
        if not touched(d):
            continue
        # A new disease's parent is reported here, on the child, because that is
        # where the relationship lives: the parent's own record gains only a
        # changelog entry, and no field diff reports those for any edit.
        parent = (d.get("parent_disease") or [{}])[0].get("name", "")
        entry = {"iri": d.get("iri"), "ari_id": key, "name": _fmt(d.get("name")) or key,
                 "is_new": key not in base, "removed": False,
                 "parent": _fmt(parent), "fields": []}
        if not entry["is_new"]:
            b = base[key]
            for fkey, flabel in FIELDS:
                ov, nv = _fmt(b.get(fkey)), _fmt(d.get(fkey))
                if ov != nv:
                    entry["fields"].append({"field": fkey, "label": flabel,
                                            "previous": ov, "new": nv})
            if not entry["fields"]:
                continue                      # touched but nothing actually differs
        out.append(entry)

    for key, b in sorted(base.items(), key=lambda kv: _fmt(kv[1].get("name"))):
        if key not in cur and touched(b):
            out.append({"iri": b.get("iri"), "ari_id": key,
                        "name": _fmt(b.get("name")) or key, "is_new": False,
                        "removed": True, "parent": "", "fields": []})
    return out


def title_for(changes: list[dict]) -> str:
    """A pull-request title that says what the pull request does.

    The default was "Update <whichever disease is open>", which named the record
    on screen rather than the work: publish three edits and the title still named
    one of them, and a brand-new disease still read as an update (issue #25).
    """
    added = [c["name"] for c in changes if c.get("is_new")]
    updated = [c["name"] for c in changes if not c.get("is_new")]
    if not added and not updated:
        return "Update ontology"

    def phrase(verb, names):
        if len(names) == 1:
            return f"{verb} {names[0]}"
        if len(names) == 2:
            return f"{verb} {names[0]} and {names[1]}"
        return f"{verb} {len(names)} diseases"

    if added and updated:
        # Only the verb drops its capital — lowercasing the whole phrase would
        # rewrite the disease names with it.
        return f"{phrase('Add', added)}; {phrase('update', updated)}"
    return phrase("Add", added) if added else phrase("Update", updated)


def render_summary(changes: list[dict]) -> str:
    """The pull-request body's Changes section, from :func:`list_changes`."""
    blocks = []
    for c in changes:
        head = f"### {c['name']} ({c['ari_id']})"
        if c.get("removed"):
            blocks.append(head + " — **removed**")
        elif c.get("is_new"):
            blocks.append(head + (f" — **new clinical subtype of {c['parent']}**"
                                  if c.get("parent") else " — **new disease**"))
        else:
            tbl = [head, "", "| Field | Previous | New |", "| --- | --- | --- |"]
            for f in c["fields"]:
                tbl.append(f"| {f['label']} | {_cell(f['previous'])} | {_cell(f['new'])} |")
            blocks.append("\n".join(tbl))
    if not blocks:
        return "_No field-level differences detected versus the source branch._"
    return "\n\n".join(blocks)


def build_change_summary(current_service, baseline_service, touched_iris=None) -> str:
    """``touched_iris``, when given, restricts the summary to diseases whose IRI
    is in that set — the ones the publishing curator actually edited this
    session — so a stale working copy doesn't surface other curators' changes
    as if they were this curator's own.

    One comparison, rendered: the dialog's disease list, the generated title and
    this summary all read the same :func:`list_changes` result, so a pull request
    cannot describe itself two different ways.
    """
    return render_summary(list_changes(current_service, baseline_service, touched_iris))
