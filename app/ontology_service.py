"""Service layer for reading and editing the ARI T1D ontology via owlready2."""
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from owlready2 import World, comment, destroy_entity, label, onto_path

from . import atomic_store, config, xref_registry
from .concepts import SYMPTOMS_ONTOLOGY, concept_index
from .errors import Invalid, NotFound
from .feedback_service import FeedbackStore
from .id_allocator import IdAllocator
from .schema import CATEGORIES, SEEALSO_IRI

log = logging.getLogger(__name__)

CORE_ONTOLOGY = "http://www.aurint.org/ontologies/ari_core"


def _pick_entries(entries, chosen):
    """The proposed entries a curator kept.

    ``entries`` are ``{"value", "source"}`` proposals; ``chosen`` is the list of
    values ticked in the preview, or ``None`` for "everything". Matching on the
    value rather than an index means a stale selection cannot silently apply the
    wrong proposal if the underlying indexes changed between preview and publish
    — anything no longer proposed is simply dropped.
    """
    entries = entries or []
    if chosen is None:
        return list(entries)
    keep = {str(v) for v in chosen}
    return [e for e in entries if e["value"] in keep]


def _split_csv(values):
    """Flatten cross-reference annotation values, splitting any that contain
    comma-separated ids into separate entries (e.g. OMOP "12345, 67890").
    Order preserved, duplicates removed."""
    out = []
    for v in (values or []):
        for part in str(v).split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def _multi_values(key, raw):
    """Split a multi-valued annotation input into the values to store.

    Cross-reference columns are canonicalised on the way in, so an id pasted as
    ``MONDO:0012345`` is stored as ``0012345``. Storing the prefixed form is what
    later produced ``MONDO:MONDO:0012345`` in the published SSSOM, since the
    exporter prepends the prefix again. ``key`` is None for the free-text
    multi-valued fields (synonyms, subtypes, definition sources), which are left
    exactly as typed.
    """
    if isinstance(raw, str):
        items = [s.strip() for s in raw.replace("\n", ",").split(",")]
    else:
        items = [str(s).strip() for s in (raw or [])]
    if key in xref_registry.XREF_SUFFIXES:
        items = [xref_registry.normalize_id(key, s) for s in items]
    return [s for s in items if s]


# Flags stored as "true"/"false" annotations. Absent reads as false, so a stored
# "false" and no value at all are the same flag.
_FLAG_FIELDS = ("obsolete", "is_grouping")


def _same_values(key, old, new) -> bool:
    """Whether ``new`` stores what ``old`` already holds. Order is ignored: it
    carries no meaning in the ontology."""
    if key in _FLAG_FIELDS:
        def is_true(vals):
            return bool(vals) and str(vals[0]).lower() == "true"
        return is_true(old) == is_true(new)
    return sorted(map(str, old)) == sorted(map(str, new))


class OntologyService:
    def __init__(self, path: str):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Ontology file not found: {self.path}")
        self.feedback = FeedbackStore(self.path.parent.parent / "feedback")
        # Shared with every other working copy: ARI numbers are the registry's
        # public identifier and must never be minted twice. Same grandparent
        # convention as the feedback store, so `.user-data/<login>.owl` and the
        # base `ontologies/ari_disease.owl` resolve to the one `provenance/` dir.
        self.ids = IdAllocator(self.path.parent.parent / "provenance")
        self._predict_cache = None      # (ontology mtime, index dir) -> predicted cells
        self._load()

    def _load(self):
        # The disease ontology owl:imports its concept modules (core, symptoms,
        # anatomy, biomarkers, and through them the SYMP and UBERON slices) by
        # IRIs that do not resolve on the web, so owlready2 must find them on
        # onto_path. That is the base ontology's folder, not this file's: working
        # copies live in .user-data/ and baselines in temp files. onto_path is
        # process-global; owlready2 has no per-World search path.
        ontologies = str(Path(config.ONTOLOGY_FILE).parent)
        if ontologies not in onto_path:
            onto_path.append(ontologies)
        # Load via a file object so Windows paths don't break owlready2's
        # file:// URI handling. The ontology's real base IRI is read from the file.
        self.world = World()
        placeholder = self.world.get_ontology("http://ari.local/ontology")
        with open(self.path, "rb") as f:
            self.onto = placeholder.load(fileobj=f)
        if not any(o.base_iri.rstrip("#/") == CORE_ONTOLOGY for o in self.onto.imported_ontologies):
            raise RuntimeError(
                f"{self.path} does not import ari_core.owl, so it is a pre-split ontology "
                "(a single ari_t1d.owl). Archive this working copy, and see the cutover notes "
                "in DEPLOY.md.")
        self._symptom_concepts = concept_index(self.world, SYMPTOMS_ONTOLOGY)

    @property
    def base(self) -> str:
        return self.onto.base_iri

    # --------------------------------------------------------- helpers
    def _entity(self, iri: str):
        e = self.world[iri]
        if e is None:
            raise NotFound(f"No entity with IRI: {iri}")
        return e

    def _get_label(self, e) -> str:
        vals = label[e] if hasattr(e, 'storid') else []
        return str(vals[0]) if vals else (e.name if hasattr(e, 'name') else str(e))

    def _get_comment(self, e) -> str:
        vals = comment[e] if hasattr(e, 'storid') else []
        return str(vals[0]) if vals else ""

    def _get_annotation(self, e, prop_name: str) -> list:
        prop = self.world[prop_name]
        if prop is None:
            return []
        vals = prop[e] if hasattr(e, 'storid') else []
        return [str(v) for v in vals]

    def _annotation_property(self, suffix: str):
        """A property ari_core.owl declares. The app never creates one."""
        prop = self.world[self.base + suffix]
        if prop is None:
            raise RuntimeError(f"{suffix} is not declared in ari_core.owl")
        return prop

    def _get_data(self, e, prop_name: str) -> list:
        prop = self.world[prop_name]
        if prop is None:
            return []
        vals = prop[e] if hasattr(e, 'storid') else []
        return list(vals)

    def _get_objects(self, e, prop_name: str) -> list:
        prop = self.world[prop_name]
        if prop is None:
            return []
        vals = prop[e] if hasattr(e, 'storid') else []
        return list(vals)

    def _is_obsolete(self, e) -> bool:
        vals = self._get_annotation(e, self.base + "ARI_Obsolete")
        return bool(vals) and str(vals[0]).lower() == "true"

    def _is_grouping(self, e) -> bool:
        """A grouping / umbrella category collects related diseases and carries no
        disease-specific clinical metadata of its own."""
        vals = self._get_annotation(e, self.base + "ARI_IsGrouping")
        return bool(vals) and str(vals[0]).lower() == "true"

    def _autoimmune_status(self, e) -> str | None:
        """Where a disease stands on being autoimmune, from its diseaseCategory:
        "confirmed" ("Autoimmune"), "unconfirmed" ("Unconfirmed"), "not" (any
        other category, e.g. "Immune Deficiency"), or None when no category is
        recorded. The disease list marks each row with it (issue #231)."""
        vals = [str(v).lower() for v in self._get_data(e, self.base + "diseaseCategory")]
        if any("autoimmune" in v for v in vals):
            return "confirmed"
        if any("unconfirmed" in v for v in vals):
            return "unconfirmed"
        return "not" if vals else None

    def _is_autoimmune(self, e) -> bool:
        """True for diseases confirmed autoimmune (diseaseCategory "Autoimmune")."""
        return self._autoimmune_status(e) == "confirmed"

    def _parse_subtype(self, raw: str) -> dict:
        """Parse one clinical-subtype annotation into its display parts.

        Stored form is ``"Name - description"`` with an optional ``" | <iri>"``
        suffix that links the subtype to an existing disease individual. The link
        is optional, so entries without it stay plain (unlinked) subtypes. When a
        link is present its target label is resolved for display; a link pointing
        at a missing entity is flagged as broken."""
        s = str(raw)
        link_iri = ""
        if " | " in s:
            head, tail = s.rsplit(" | ", 1)
            tail = tail.strip()
            if tail.startswith("http"):
                s, link_iri = head, tail
        name, _, desc = s.partition(" - ")
        out = {
            "name": name.strip(),
            "description": desc.strip(),
            "link_iri": link_iri,
            "link_name": "",
            "link_obsolete": False,
        }
        if link_iri:
            target = self.world[link_iri]
            if target is not None:
                out["link_name"] = self._get_label(target)
                out["link_obsolete"] = self._is_obsolete(target)
        return out

    def _ref(self, e) -> dict:
        return {
            "iri": e.iri,
            "name": self._get_label(e),
            "local_name": e.name,
            "obsolete": self._is_obsolete(e),
        }

    def _search_terms(self, d) -> list:
        """A disease's ARI ids and synonyms, which the disease list filters on
        alongside its name, as the header search does."""
        base = self.base
        return (self._get_annotation(d, base + "ARI_ID")
                + self._get_annotation(d, base + "ARI_Synonym"))

    def _disease_class(self):
        return self.world[self.base + "AutoimmuneDisease"]

    def _all_diseases(self) -> list:
        cls = self._disease_class()
        return list(cls.instances(world=self.world)) if cls else []

    # --------------------------------------------------------- READ API
    def overview(self) -> dict:
        return {
            "iri": self.base,
            "file": str(self.path),
            "disease_count": len(self._all_diseases()),
            "individuals": len(list(self.onto.individuals())),
            "classes": len(list(self.onto.classes())),
            "version": self._current_version(),
        }

    def get_diseases_list(self) -> list:
        """Flat list of all disease individuals."""
        results = []
        for ind in self._all_diseases():
            results.append({
                "iri": ind.iri,
                "name": self._get_label(ind),
                "local_name": ind.name,
                "obsolete": self._is_obsolete(ind),
                "synonyms": self._get_annotation(ind, self.base + "ARI_Synonym"),
            })
        return sorted(results, key=lambda x: x["name"])

    def get_alphabetical_tree(self) -> list:
        """Diseases grouped under their parent disease; children expand from parent."""
        base = self.base
        diseases = self._all_diseases()
        # Map each disease -> its parent diseases
        parent_of = {}
        for d in diseases:
            parents = self._get_objects(d, base + "hasParentDisease")
            parent_of[d.iri] = [p.iri for p in parents]
        # children index
        children_idx = {}
        for d in diseases:
            for piri in parent_of[d.iri]:
                children_idx.setdefault(piri, []).append(d)

        def node(d, seen):
            # `seen` tracks the current ancestor path so a parent cycle (e.g. a
            # disease listing itself as its own parent) can't recurse forever.
            # It is path-scoped, so a disease with several parents still shows
            # under each of them.
            kids = sorted(children_idx.get(d.iri, []), key=lambda x: self._get_label(x))
            branch = seen | {d.iri}
            return {
                "iri": d.iri,
                "name": self._get_label(d),
                "local_name": d.name,
                "obsolete": self._is_obsolete(d),
                "ai_status": self._autoimmune_status(d),
                "search_terms": self._search_terms(d),
                "children": [node(k, branch) for k in kids if k.iri not in branch],
            }

        roots = [d for d in diseases if not parent_of[d.iri]]
        roots = sorted(roots, key=lambda x: self._get_label(x))
        return [node(d, set()) for d in roots]

    def get_tissue_hierarchy(self) -> list:
        """Anatomical-structure (UBERON_0010000) hierarchy with diseases attached
        under each tissue they target."""
        base = self.base
        root = self.world[base + "MulticellularAnatomicalStructure"]
        if root is None:
            return []
        diseases = self._all_diseases()
        # disease -> set of tissue classes it directly targets
        dis_classes = {}
        for d in diseases:
            classes = set()
            for tis in self._get_objects(d, base + "targetsTissue"):
                for c in getattr(tis, "is_a", []):
                    if hasattr(c, "iri"):
                        classes.add(c.iri)
            dis_classes[d.iri] = classes

        def walk(cls, seen):
            diseases_here = [
                {**self._ref(d), "ai_status": self._autoimmune_status(d),
                 "search_terms": self._search_terms(d)}
                for d in diseases if cls.iri in dis_classes[d.iri]
            ]
            diseases_here.sort(key=lambda x: x["name"])
            branch = seen | {cls.iri}
            children = []
            for sub in sorted(cls.subclasses(), key=lambda c: c.name if hasattr(c, 'name') else ""):
                # ARI classes, whichever module declares them; guard against subclass cycles
                if sub.iri.startswith(base) and sub.iri not in branch:
                    children.append(walk(sub, branch))
            ari_id = self._get_annotation(cls, base + "ARI_ID")
            return {
                "iri": cls.iri,
                "name": self._get_label(cls),
                "local_name": cls.name,
                "ari_id": ari_id[0] if ari_id else "",
                "diseases": diseases_here,
                "children": children,
            }

        return [walk(root, set())]

    def get_symptoms_index(self) -> list:
        """Flat list of all symptom individuals across diseases, for the symptom search.

        Each names the diseases that carry it as ``{"iri", "name"}``, so the search
        opens a disease by its IRI rather than matching its name against the disease
        list. The symptom's own IRI is left out: nothing reads it."""
        base = self.base
        cls = self.world[base + "Symptom"]
        if cls is None:
            return []
        # map symptom -> diseases that have it
        diseases = self._all_diseases()
        owner = {}
        for d in diseases:
            for s in self._get_objects(d, base + "hasSymptom"):
                owner.setdefault(s.iri, []).append({"iri": d.iri, "name": self._get_label(d)})
        results = []
        for s in cls.instances(world=self.world):
            results.append({
                "name": self._get_label(s),
                "likelihood": (self._get_data(s, base + "likelihood") or [""])[0],
                "obsolete": self._is_obsolete(s),
                "diseases": owner.get(s.iri, []),
            })
        return sorted(results, key=lambda x: x["name"])

    # database key -> cross-reference annotation-property suffix (for the review
    # grid). Sourced from the shared xref registry so it can't drift from the SSSOM
    # prefixes or the frontend link-outs.
    XREF_SUFFIXES = xref_registry.XREF_SUFFIXES

    def get_xref_rows(self) -> list:
        """Disease + cross-reference identifiers for the reference-review grid.

        Reads only the id fields the review page needs, so it stays O(diseases)
        rather than building the full get_disease_detail() (symptoms, pathway,
        every immune component, and a re-scan of all diseases for subtypes) for
        each of N diseases — which made the /api/v2/xrefs endpoint O(N^2)."""
        base = self.base
        rows = []
        for ind in self._all_diseases():
            ari = self._get_annotation(ind, base + "ARI_ID")
            row = {"iri": ind.iri, "name": self._get_label(ind),
                   "ari_id": ari[0] if ari else None,
                   "autoimmune": self._is_autoimmune(ind),
                   "obsolete": self._is_obsolete(ind),
                   # The review panel puts this opposite the candidate concept's
                   # own definition, so judging a mapping is a side-by-side read
                   # rather than a memory test (issue #96). One extra annotation
                   # per disease keeps this O(diseases).
                   "definition": self._get_comment(ind),
                   "synonyms": self._get_annotation(ind, base + "ARI_Synonym")}
            for key, suffix in self.XREF_SUFFIXES.items():
                row[key] = _split_csv(self._get_annotation(ind, base + suffix))
            rows.append(row)
        # Same ordering as get_diseases_list (sort by label) so the review grid
        # is unchanged versus the previous per-disease-detail implementation.
        return sorted(rows, key=lambda r: r["name"] or "")

    def get_mapping_subject(self, iri: str) -> dict:
        """The disease as a mapping row's subject: ``{ari_id, name}``."""
        e = self._entity(iri)
        ari = self._get_annotation(e, self.base + "ARI_ID")
        return {"ari_id": ari[0] if ari else None, "name": self._get_label(e)}

    def get_xrefs(self, iri: str) -> dict:
        """One disease's cross-reference ids, keyed by database.

        Cheap enough to call on both sides of an edit — which is what the
        id-authorship ledger (``app/id_provenance.py``) diffs — unlike
        ``get_disease_detail``."""
        e = self._entity(iri)
        return {key: _split_csv(self._get_annotation(e, self.base + suffix))
                for key, suffix in self.XREF_SUFFIXES.items()}

    def predict_xrefs(self, index_dir=None) -> list:
        """Predicted cross-references for the review grid's blank cells.

        For every disease, exact-matches its label and synonyms against the
        downloaded reference-database indexes (``data/2-databases``) and returns a
        candidate id for each currently-empty target-database cell — the yellow
        "predicted" highlights of issue #42. Read-only: nothing is written to the
        ontology. Returns the compact per-cell shape ``to_cells`` produces.

        Cached per (ontology mtime, index directory, blocklist mtime) — the
        blocklist is refreshed from the data repo while the app runs. The index
        files were already cached with an mtime signature, but this rebuilt the
        disease list and re-ran the whole matching pass on every call — and the
        review page calls it on every load."""
        from . import predict_service as ps
        index_dir = index_dir or ps.DEFAULT_INDEX_DIR
        try:
            sig = (self.path.stat().st_mtime_ns, str(index_dir),
                   ps.blocklist_path().stat().st_mtime_ns)
        except OSError:
            sig = None
        if sig is not None and self._predict_cache and self._predict_cache[0] == sig:
            return self._predict_cache[1]
        base = self.base
        diseases = []
        for ind in self._all_diseases():
            ari = self._get_annotation(ind, base + "ARI_ID")
            diseases.append({
                "ari_id": ari[0] if ari else None,
                "iri": ind.iri,
                "name": self._get_label(ind),
                "synonyms": self._get_annotation(ind, base + "ARI_Synonym"),
                "existing": {db: _split_csv(self._get_annotation(ind, base + suffix))
                             for db, suffix in self.XREF_SUFFIXES.items()},
            })
        cells = ps.to_cells(ps.predict_matches(diseases, index_dir=index_dir))
        if sig is not None:
            self._predict_cache = (sig, cells)
        return cells

    def get_disease_records(self) -> list:
        """Every disease's own fields, sorted by label: each entry is
        :meth:`get_disease_detail` without the item lists and ``subtypes``.

        For whole-catalogue views such as the export, which carry only these.
        Building the full detail for every disease was O(N²): each one scans
        every other disease for its subtypes, on top of reading all its items."""
        return sorted((self._record_fields(d) for d in self._all_diseases()),
                      key=lambda r: r["name"])

    def _record_fields(self, e) -> dict:
        """A disease's own annotations, data properties and direct links.

        Reads only ``e`` (and the labels of what it links to), never another
        disease, so it stays cheap. :meth:`get_disease_detail` adds the subtypes
        and item lists to it, so the two cannot disagree about a field."""
        base = self.base

        d = {
            "iri": e.iri,
            "name": self._get_label(e),
            "local_name": e.name,
            "ari_id": self._get_annotation(e, base + "ARI_ID"),
            "definition": self._get_comment(e),
            "obsolete": self._is_obsolete(e),
            "is_grouping": self._is_grouping(e),
            "synonyms": self._get_annotation(e, base + "ARI_Synonym"),
            "snomed": _split_csv(self._get_annotation(e, base + "ARI_SNOMED")),
            "doid": _split_csv(self._get_annotation(e, base + "ARI_DOID")),
            "umls": _split_csv(self._get_annotation(e, base + "ARI_UMLS")),
            "mondo": _split_csv(self._get_annotation(e, base + "ARI_MONDO")),
            "icd10": _split_csv(self._get_annotation(e, base + "ARI_ICD10")),
            "mesh": _split_csv(self._get_annotation(e, base + "ARI_MESH")),
            "nci": _split_csv(self._get_annotation(e, base + "ARI_NCI")),
            "omop": _split_csv(self._get_annotation(e, base + "ARI_OMOP")),
            "orphanet": _split_csv(self._get_annotation(e, base + "ARI_ORPHANET")),
            "omim": _split_csv(self._get_annotation(e, base + "ARI_OMIM")),
            "dxcode": _split_csv(self._get_annotation(e, base + "ARI_DXCODE")),
            "version": self._get_annotation(e, base + "ARI_Version"),
            "prevalence_desc": self._get_annotation(e, base + "ARI_PrevalenceDesc"),
            "pubmed": self._get_annotation(e, base + "ARI_Pubmed"),
            "def_source": self._get_annotation(e, base + "ARI_DefSource"),
            "ref_links": self._get_annotation(e, base + "ARI_RefLink"),
            "clinical_subtypes": self._get_annotation(e, base + "ARI_ClinicalSubtype"),
            "clinical_subtypes_parsed": [
                self._parse_subtype(s)
                for s in self._get_annotation(e, base + "ARI_ClinicalSubtype")
            ],
            "authors": self._get_annotation(e, base + "ARI_Author"),
            "author_date": self._get_annotation(e, base + "ARI_AuthorDate"),
            "survey_code": self._get_annotation(e, base + "ARI_SurveyCode"),
            "changelog": self._get_annotation(e, base + "ARI_ChangeLog"),
        }

        # Data properties
        d["evidence_quality"] = self._get_data(e, base + "evidenceQuality")
        d["disease_category"] = self._get_data(e, base + "diseaseCategory")
        # Prevalence and incidence figures are items (get_disease_detail's
        # "epidemiology"); the estimated case count stays on the disease.
        d["prevalence_value"] = self._get_data(e, base + "prevalenceValue")
        d["demographic_bias"] = self._get_data(e, base + "demographicBias")
        d["age_range"] = self._get_data(e, base + "ageRange")

        # Object property links
        d["tissue_targets"] = [self._ref(o) for o in self._get_objects(e, base + "targetsTissue")]
        d["parent_categories"] = [self._ref(o) for o in self._get_objects(e, base + "hasParentCategory")]
        d["parent_disease"] = [self._ref(o) for o in self._get_objects(e, base + "hasParentDisease")]
        return d

    def get_disease_detail(self, iri: str) -> dict:
        """Return full detail about a disease individual and all its associations."""
        e = self._entity(iri)
        base = self.base
        d = self._record_fields(e)

        # Subtypes (diseases that name this one as parent)
        d["subtypes"] = []
        for other in self._all_diseases():
            parents = self._get_objects(other, base + "hasParentDisease")
            if any(p.iri == e.iri for p in parents):
                d["subtypes"].append(self._ref(other))

        # Prevalence and incidence figures (#229), one per measure, region,
        # year and sex.
        d["epidemiology"] = []
        for o in self._get_objects(e, base + "hasEpidemiologyProfile"):
            d["epidemiology"].append({
                "iri": o.iri,
                "name": self._get_label(o),
                "measure": self._get_data(o, base + "epiMeasure"),
                "region": self._get_data(o, base + "epiRegion"),
                "year": self._get_data(o, base + "epiYear"),
                "sex": self._get_data(o, base + "epiSex"),
                "rate": self._get_data(o, base + "epiRatePer100k"),
                "source": self._get_data(o, base + "sourcePMID"),
            })

        # Symptoms
        d["symptoms"] = []
        for o in self._get_objects(e, base + "hasSymptom"):
            d["symptoms"].append({
                "iri": o.iri,
                "name": self._get_label(o),
                "obsolete": self._is_obsolete(o),
                "likelihood": self._get_data(o, base + "likelihood"),
                "description": self._get_data(o, base + "symptomDescription"),
                "seeAlso": self._get_data(o, "http://www.w3.org/2000/01/rdf-schema#seeAlso"),
                "source": self._get_data(o, base + "sourcePMID"),
            })

        # Environmental triggers
        d["environmental_factors"] = []
        for o in self._get_objects(e, base + "hasEnvironmentalTrigger"):
            d["environmental_factors"].append({
                "iri": o.iri,
                "name": self._get_label(o),
                "obsolete": self._is_obsolete(o),
                "description": self._get_data(o, base + "triggerDescription"),
                "likelihood": self._get_data(o, base + "likelihood"),
                "source": self._get_data(o, base + "sourcePMID"),
            })

        # Antibodies
        d["antibodies"] = []
        for o in self._get_objects(e, base + "hasAntibody"):
            d["antibodies"].append({
                "iri": o.iri,
                "name": self._get_label(o),
                "obsolete": self._is_obsolete(o),
                "frequency": self._get_data(o, base + "antibodyFrequency"),
                "diagnostic_value": self._get_data(o, base + "antibodyDiagnosticValue"),
                "source": self._get_data(o, base + "sourcePMID"),
            })

        # Genetic associations
        d["genetic"] = []
        for o in self._get_objects(e, base + "hasGeneticAssociation"):
            d["genetic"].append({
                "iri": o.iri,
                "name": self._get_label(o),
                "obsolete": self._is_obsolete(o),
                "locus": self._get_data(o, base + "chromosomalLocus"),
                "product": self._get_data(o, base + "geneticProduct"),
                "risk_effect": self._get_data(o, base + "geneticRiskEffect"),
                "odds_ratio": self._get_data(o, base + "geneticOddsRatio"),
                "hla_effect": self._get_data(o, base + "hlaEffect"),
                "hla_mechanism": self._get_data(o, base + "hlaMechanism"),
                "source": self._get_data(o, base + "sourcePMID"),
            })

        # Treatments
        d["treatments"] = []
        for o in self._get_objects(e, base + "hasTreatment"):
            d["treatments"].append({
                "iri": o.iri,
                "name": self._get_label(o),
                "obsolete": self._is_obsolete(o),
                "type": self._get_data(o, base + "treatmentType"),
                "description": self._get_data(o, base + "treatmentDescription"),
                "fda_status": self._get_data(o, base + "treatmentFdaStatus"),
                "source": self._get_data(o, base + "sourcePMID"),
            })

        # Etiology
        d["etiology"] = []
        for o in self._get_objects(e, base + "hasEtiologyFactor"):
            d["etiology"].append({
                "iri": o.iri,
                "name": self._get_label(o),
                "obsolete": self._is_obsolete(o),
                "origin_type": self._get_data(o, base + "etiologyOriginType"),
                "description": self._get_data(o, base + "etiologyDescription"),
                "excerpt": self._get_data(o, base + "etiologyExcerpt"),
                "source": self._get_data(o, base + "sourcePMID"),
            })

        # Clinical biomarkers: tests a physician can order to diagnose the disease
        d["biomarkers"] = []
        for o in self._get_objects(e, base + "hasBiomarker"):
            d["biomarkers"].append({
                "iri": o.iri,
                "name": self._get_label(o),
                "obsolete": self._is_obsolete(o),
                "type": self._get_data(o, base + "markerType"),
                "description": self._get_comment(o),
                "diagnostic_use": self._get_data(o, base + "markerDiagnosticUse"),
                "sensitivity": self._get_data(o, base + "markerSensitivity"),
                "specificity": self._get_data(o, base + "markerSpecificity"),
                "loinc": self._get_data(o, base + "loincCode"),
                "source": self._get_data(o, base + "sourcePMID"),
            })

        # Pathophysiology pathway steps (ordered)
        d["pathway"] = []
        for o in self._get_objects(e, base + "hasPathwayStep"):
            order = self._get_data(o, base + "stepOrder")
            d["pathway"].append({
                "iri": o.iri,
                "name": self._get_label(o),
                "order": order[0] if order else 0,
                "category": (self._get_data(o, base + "stepCategory") or [""])[0],
                "description": (self._get_data(o, base + "stepDescription") or [""])[0],
                "source": self._get_data(o, base + "sourcePMID"),
            })
        d["pathway"].sort(key=lambda x: x["order"])

        # Immune / molecular components
        def component_list(prop, desc_key="mediatorDescription"):
            out = []
            for o in self._get_objects(e, base + prop):
                out.append({
                    "iri": o.iri,
                    "name": self._get_label(o),
                    "obsolete": self._is_obsolete(o),
                    "description": self._get_data(o, base + desc_key),
                    "relevance": self._get_data(o, base + "componentRelevance"),
                    "source": self._get_data(o, base + "sourcePMID"),
                })
            return out

        d["cytokines"] = component_list("involvesCytokine")
        d["tcells"] = component_list("involvesTCell", desc_key="tCellRole")
        d["apcs"] = component_list("involvesAPC")
        d["transcription_factors"] = component_list("involvesTranscriptionFactor")
        d["innate_components"] = component_list("involvesInnateComponent")
        d["complement"] = component_list("involvesComplement")
        d["receptors"] = component_list("involvesReceptor")
        d["netosis"] = component_list("involvesNETosis")
        d["inflammasome"] = component_list("involvesInflammasome")
        d["acute_phase_reactants"] = component_list("involvesAcutePhaseReactant")
        d["antigens"] = component_list("involvesAntigen")
        d["target_cells"] = component_list("affectsCellTissueOrOrgan")

        return d

    def get_tissues(self) -> list:
        """All tissue-target individuals available for new-disease creation."""
        base = self.base
        root = self.world[base + "MulticellularAnatomicalStructure"]
        if root is None:
            return []
        tissue_classes = set()

        def _collect(cls):
            if cls in tissue_classes:      # already visited — also breaks any subclass cycle
                return
            tissue_classes.add(cls)
            for sub in cls.subclasses(world=self.world):
                _collect(sub)

        _collect(root)
        results, seen = [], set()
        for cls in tissue_classes:
            for ind in cls.instances(world=self.world):
                if ind.iri in seen:
                    continue
                seen.add(ind.iri)
                results.append({
                    "iri": ind.iri,
                    "name": self._get_label(ind),
                    "local_name": ind.name if hasattr(ind, "name") else "",
                })
        return sorted(results, key=lambda x: x["name"])

    def search(self, query: str) -> list:
        q = query.lower()
        base = self.base
        diseases = self._all_diseases()
        disease_iris = {d.iri for d in diseases}
        results = []
        seen = set()

        def add(ind, is_disease, match):
            if ind.iri in seen:
                return
            seen.add(ind.iri)
            results.append({
                "iri": ind.iri,
                "name": self._get_label(ind),
                "local_name": ind.name,
                "obsolete": self._is_obsolete(ind),
                "is_disease": is_disease,
                "match": match,
            })

        def matches(text):
            return text and q in text.lower()

        # Diseases: match on label / local name, then ARI id, then synonyms, then
        # target tissue.
        for d in diseases:
            if matches(self._get_label(d)) or (hasattr(d, "name") and matches(d.name)):
                add(d, True, "name")
                continue
            ari_id = next((a for a in self._get_annotation(d, base + "ARI_ID") if matches(a)), None)
            if ari_id:
                add(d, True, ari_id)
                continue
            syn = next((s for s in self._get_annotation(d, base + "ARI_Synonym") if matches(s)), None)
            if syn:
                add(d, True, f"synonym: {syn}")
                continue
            tis = next((self._get_label(t) for t in self._get_objects(d, base + "targetsTissue")
                        if matches(self._get_label(t))), None)
            if tis:
                add(d, True, f"tissue: {tis}")

        # Other individuals: match on label / local name.
        for ind in self.world.individuals():
            if ind.iri in disease_iris:
                continue
            if matches(self._get_label(ind)) or (hasattr(ind, "name") and matches(ind.name)):
                add(ind, False, "name")

        # diseases first, then by name
        results.sort(key=lambda r: (not r["is_disease"], r["name"].lower()))
        return results[:100]

    # --------------------------------------------------------- WRITE API
    # Editable disease fields -> (kind, property-iri-suffix, caster)
    EDITABLE = {
        "name":              ("label", None, str),
        "definition":        ("comment", None, str),
        "synonyms":          ("multi_ann", "ARI_Synonym", str),
        "clinical_subtypes": ("multi_ann", "ARI_ClinicalSubtype", str),
        "snomed":            ("multi_ann", "ARI_SNOMED", str),
        "doid":              ("multi_ann", "ARI_DOID", str),
        "umls":              ("multi_ann", "ARI_UMLS", str),
        "mondo":             ("multi_ann", "ARI_MONDO", str),
        "icd10":             ("multi_ann", "ARI_ICD10", str),
        "mesh":              ("multi_ann", "ARI_MESH", str),
        "nci":               ("multi_ann", "ARI_NCI", str),
        "omop":              ("multi_ann", "ARI_OMOP", str),
        "orphanet":          ("multi_ann", "ARI_ORPHANET", str),
        "omim":              ("multi_ann", "ARI_OMIM", str),
        "dxcode":            ("multi_ann", "ARI_DXCODE", str),
        "def_source":        ("multi_ann", "ARI_DefSource", str),
        "pubmed":            ("ann", "ARI_Pubmed", str),
        "prevalence_desc":   ("ann", "ARI_PrevalenceDesc", str),
        "obsolete":          ("ann", "ARI_Obsolete", str),
        "is_grouping":       ("ann", "ARI_IsGrouping", str),
        "evidence_quality":  ("data", "evidenceQuality", str),
        "disease_category":  ("data", "diseaseCategory", str),
        "demographic_bias":  ("data", "demographicBias", str),
        "age_range":         ("data", "ageRange", str),
        "prevalence_value":  ("data", "prevalenceValue", float),
    }

    def apply_xref_op(self, iri: str, db: str, op: str, value: str = "",
                      replaces: str = "", editor: str = "user") -> dict:
        """Apply one cross-reference id change against the *current* stored ids.

        The review page used to read the ids it had in memory, splice the new
        value in, and PUT the joined list back as the field's whole value — so
        anything another window or another curator had added to that cell since
        the page loaded was erased, with no conflict and no message. The client
        sends the single id and what to do with it now, and the list is rebuilt
        here from what is actually on file.

        ``replace`` refuses when the id it is replacing is no longer there: that
        means someone else changed it, and quietly adding the new value instead
        would hide the collision this method exists to surface. ``remove`` is
        idempotent — an id that is already gone is the outcome asked for.
        """
        if db not in self.XREF_SUFFIXES:
            raise Invalid(f"{db} is not a cross-reference database")
        current = _split_csv(self._get_annotation(self._entity(iri), self.base + self.XREF_SUFFIXES[db]))
        value, replaces = str(value).strip(), str(replaces).strip()

        if op == "add":
            if not value:
                raise Invalid("add needs an id")
            new = current if value in current else current + [value]
        elif op == "replace":
            if not value:
                raise Invalid("replace needs an id")
            if replaces not in current:
                raise Invalid(
                    f"{replaces} is no longer on file for {db} — it was changed or removed "
                    "somewhere else. Reload the page to see the current ids.")
            new = [value if x == replaces else x for x in current]
        elif op == "remove":
            new = [x for x in current if x != value]
        else:
            raise Invalid(f"{op} is not one of add, replace, remove")

        return self.update_disease(iri, {db: ", ".join(new)}, editor=editor)

    def update_disease(self, iri: str, changes: dict, editor: str = "user") -> dict:
        """Apply ``changes``, reporting anything that could not be stored.

        A value that failed to cast, or a key that is not editable, used to be
        skipped silently — so a curator typing "about 30 per 100k" into a numeric
        prevalence field got a successful save, a changelog entry, and no value
        stored, with nothing on screen saying otherwise. Rejections come back on
        the detail as ``rejected`` so the form can say what went wrong and where.
        """
        e = self._entity(iri)
        base = self.base
        changed = []
        rejected = []

        for key, raw in changes.items():
            spec = self.EDITABLE.get(key)
            if not spec:
                rejected.append({"field": key, "value": str(raw),
                                 "reason": "is not an editable field"})
                continue
            kind, suffix, caster = spec

            if kind == "label":
                prop, new = label, [str(raw)] if str(raw).strip() else []
            elif kind == "comment":
                prop, new = comment, [str(raw)] if str(raw).strip() else []
            elif kind == "multi_ann":
                prop, new = self._annotation_property(suffix), _multi_values(key, raw)
            elif kind == "ann":
                prop = self._annotation_property(suffix)
                new = [str(raw)] if str(raw).strip() != "" else []
            elif kind == "data":
                prop = self.world[base + suffix]
                if prop is None:
                    rejected.append({"field": key, "value": str(raw),
                                     "reason": f"the ontology declares no {suffix} property"})
                    continue
                if str(raw).strip() == "":
                    new = []
                else:
                    try:
                        new = [caster(raw)]
                    except (ValueError, TypeError):
                        wanted = "a number" if caster in (int, float) else caster.__name__
                        rejected.append({"field": key, "value": str(raw),
                                         "reason": f"expected {wanted}"})
                        continue
            elif kind == "data_list":
                prop = self.world[base + suffix]
                if prop is None:
                    rejected.append({"field": key, "value": str(raw),
                                     "reason": f"the ontology declares no {suffix} property"})
                    continue
                new = str(raw).split()
            # The editor form sends every field on each save, so most arrive
            # unchanged. Writing and logging them all put every field in each
            # "Edited:" entry and stored an ARI_IsGrouping "false" on records
            # that never had one.
            if _same_values(key, prop[e], new):
                continue
            prop[e] = new
            changed.append(key)

        if changed:
            self._append_changelog(e, editor, f"Edited: {', '.join(sorted(changed))}")
            self._save()

        detail = self.get_disease_detail(iri)
        detail["rejected"] = rejected
        return detail

    # Width of the zero-padded numeric part of an ARI id (e.g. ARI:0001211).
    ARI_ID_WIDTH = 7

    # Registry namespace that disease IRIs live under, e.g.
    # https://diseases.autoimmuneregistry.org/disease/ARI_0001211. This is
    # deliberately *not* the ontology's own aurint.org base namespace: curated
    # diseases are minted under the registry namespace, and new ones must match.
    DISEASE_NS = "https://diseases.autoimmuneregistry.org/disease/"

    def _highest_ari_number(self) -> int:
        """The highest ARI number this ontology already contains.

        Scans both the ARI_ID annotation (e.g. ``ARI:0001211``) and the IRI local
        name (e.g. ``ARI_0001080``) of every disease."""
        base = self.base
        mx = 0
        for d in self._all_diseases():
            for v in self._get_annotation(d, base + "ARI_ID"):
                m = re.search(r"(\d+)", str(v))
                if m:
                    mx = max(mx, int(m.group(1)))
            m = re.match(r"ARI_0*(\d+)$", d.name or "")
            if m:
                mx = max(mx, int(m.group(1)))
        return mx

    def _next_ari_number(self, editor: str = "") -> int:
        """Reserve the next number in the ARI:00NNNNN sequence.

        Taking max + 1 from the ontology in hand is not enough: each curator
        edits a private working copy, so two curators creating a disease the
        same afternoon both minted the same number — and the colliding IRI made
        RDF treat two unrelated diseases as one individual once both pull
        requests merged. The number comes from one server-side counter, which
        this ontology's own highest number can only push forward."""
        return self.ids.allocate(self._highest_ari_number(), editor=editor)

    def create_disease(self, data: dict, editor: str = "user") -> dict:
        """Create a new AutoimmuneDisease individual with the next sequential ARI id.

        Required keys in data: label, definition, def_source, tissue_iris (list[str]).
        Optional: parent_iri, synonyms, authors, author_date, clinical_subtypes,
                  and any key from EDITABLE (disease_category, icd10, etc.).
        """
        base = self.base
        dis_cls = self._disease_class()
        if dis_cls is None:
            raise NotFound("AutoimmuneDisease class not found")

        lbl = str(data.get("label", "")).strip()
        if not lbl:
            raise ValueError("label is required")

        # Assign the next sequential ARI id (issue #28): the IRI local name and the
        # ARI_ID annotation share one number, matching the ARI:00NNNNN convention
        # used by the curated diseases, instead of a random ARI_new_<hex> id. The
        # individual is minted under the registry namespace so its IRI is
        # https://diseases.autoimmuneregistry.org/disease/ARI_00NNNNN, matching the
        # curated diseases, rather than the ontology's own aurint.org namespace.
        num = self._next_ari_number(editor=editor)
        ari_id = f"ARI:{num:0{self.ARI_ID_WIDTH}d}"
        local = f"ARI_{num:0{self.ARI_ID_WIDTH}d}"
        disease_ns = self.onto.get_namespace(self.DISEASE_NS)
        with self.onto:
            new_d = dis_cls(local, namespace=disease_ns)

        label[new_d] = [lbl]
        self._annotation_property("ARI_ID")[new_d] = [ari_id]

        defn = str(data.get("definition", "")).strip()
        if defn:
            comment[new_d] = [defn]

        def _ann(suffix, val):
            prop = self._annotation_property(suffix)
            v = str(val).strip() if val else ""
            if v:
                prop[new_d] = [v]

        def_src = data.get("def_source", "")
        def_src_prop = self.world[base + "ARI_DefSource"]
        if def_src_prop and def_src:
            items = def_src if isinstance(def_src, list) else [str(def_src).strip()]
            def_src_prop[new_d] = [s for s in items if s]
        _ann("ARI_Author", data.get("authors", ""))
        _ann("ARI_AuthorDate", data.get("author_date", ""))

        # Tissue targets (object property)
        tis_prop = self.world[base + "targetsTissue"]
        if tis_prop:
            for tiri in (data.get("tissue_iris") or []):
                tis = self.world[tiri]
                if tis is not None:
                    tis_prop[new_d].append(tis)

        # Parent disease (object property)
        parent_iri = str(data.get("parent_iri", "")).strip()
        parent = None
        if parent_iri:
            par_prop = self.world[base + "hasParentDisease"]
            parent = self.world[parent_iri]
            if par_prop and parent is not None:
                par_prop[new_d] = [parent]

        def _multi(suffix, val, key=None):
            prop = self._annotation_property(suffix)
            prop[new_d] = _multi_values(key, val)

        _multi("ARI_Synonym", data.get("synonyms", ""))
        _multi("ARI_ClinicalSubtype", data.get("clinical_subtypes", ""))

        # Other EDITABLE fields delegated generically
        skip = {"name", "definition", "synonyms", "clinical_subtypes", "def_source", "obsolete"}
        for key, raw in data.items():
            if key in skip or key not in self.EDITABLE:
                continue
            kind, suffix, caster = self.EDITABLE[key]
            if kind in ("label", "comment"):
                continue
            elif kind == "multi_ann":
                _multi(suffix, raw, key)
            elif kind == "ann":
                prop = self._annotation_property(suffix)
                prop[new_d] = [str(raw)] if str(raw).strip() else []
            elif kind == "data":
                prop = self.world[base + suffix]
                if prop:
                    sv = str(raw).strip()
                    if not sv:
                        prop[new_d] = []
                    else:
                        try:
                            prop[new_d] = [caster(raw)]
                        except (ValueError, TypeError):
                            pass

        # Name the disease this was started from, on both records. "Created: X"
        # alone left no trace of where a subtype came from — not on the child, and
        # nothing at all on the parent, so a curator opening the parent could not
        # see that a subtype had been split out of it (issue #24).
        parent_label = self._get_label(parent) if parent is not None else ""
        self._append_changelog(new_d, editor, f"Created: {lbl}" +
                               (f" — clinical subtype of {parent_label}" if parent_label else ""))
        if parent is not None:
            self._append_changelog(parent, editor, f"Added clinical subtype: {lbl} ({ari_id})")
        self._save()
        return self.get_disease_detail(new_d.iri)

    def _save(self):
        """Serialise the ontology, atomically.

        This runs on every field edit, item edit, review log and enrichment, and
        the file is ~1.7MB of RDF/XML. Saving in place meant a crash, a full
        disk, or the ten-minute deploy restart landing mid-serialise left a
        truncated OWL file with no previous version to fall back to. Writing to
        a sibling temp file and renaming means a reader sees either the whole
        old file or the whole new one.
        """
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        try:
            self.onto.save(file=str(tmp), format="rdfxml")
            os.replace(tmp, self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _append_changelog(self, disease_e, editor: str, msg: str):
        """Record an edit on the disease's own changelog annotation.

        This looked the property up and returned silently when it was absent, so
        on any ontology where ``ARI_ChangeLog`` had not been declared every edit
        was recorded nowhere while the caller still reported success. It is
        declared in ari_core.owl, and a missing declaration raises, as it does
        for every other annotation writer here.
        """
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        clog = self._annotation_property("ARI_ChangeLog")
        clog[disease_e] = list(clog[disease_e]) + [f"{ts} | {editor} | {msg}"]

    def log_xref_review(self, confirmed=None, flagged=None, editor: str = "curator",
                        absent=None) -> int:
        """Record a reference-review session in the affected diseases' changelogs.

        Confirming / flagging a cross-reference on the review page only writes to
        the SSSOM + equivalency mapping files; it does not otherwise touch the
        ontology. This appends a per-disease changelog entry so the judgment is
        also visible in the disease's own history. ``confirmed`` / ``flagged`` are
        lists of ``{iri, db, ids, ...}`` (as the publish endpoint receives them);
        ``absent`` is ``{iri, db, ...}`` for a database the disease has no term in.
        Returns the number of diseases whose changelog was updated."""
        # iri -> {"confirmed": {db: [ids]}, "flagged": ..., "no term in": ...}
        groups: dict = {}
        for verdict, items in (("confirmed", confirmed or []), ("flagged", flagged or []),
                               ("no term in", absent or [])):
            for c in items:
                iri = c.get("iri")
                ids = [str(i) for i in (c.get("ids") or []) if str(i).strip()]
                if not iri or (not ids and verdict != "no term in"):
                    continue
                db = str(c.get("db", "")).strip()
                groups.setdefault(iri, {}).setdefault(verdict, {}).setdefault(db, []).extend(ids)

        updated = 0
        for iri, verdicts in groups.items():
            try:
                e = self._entity(iri)
            except KeyError:
                continue
            parts = []
            for verdict in ("confirmed", "flagged", "no term in"):
                for db, ids in (verdicts.get(verdict) or {}).items():
                    label = (db or "xref").upper()
                    parts.append(f"{verdict} {label} {', '.join(ids)}".rstrip())
            if parts:
                self._append_changelog(e, editor, "Cross-reference review: " + "; ".join(parts))
                updated += 1
        if updated:
            self._save()
        return updated

    def store_confirmed_xrefs(self, confirmed, editor: str = "curator") -> int:
        """Store confirmed cross-reference ids on the disease record itself.

        Confirming a cross-reference used to write a SSSOM row and a changelog
        line and nothing else, so where the disease did not already hold the id
        — which is most of MONDO and Orphanet, neither of which the original ARI
        import carried — the curator confirmed a term and nothing a user can see
        changed. Thirty confirmations across sixteen diseases were stranded that
        way (issue #146); the only thing that had ever stored them was a curator
        separately hand-editing the field.

        Ids already on file are left alone, so this is idempotent and a
        re-confirmation adds neither a value nor a changelog entry. Returns the
        number of ids newly stored.
        """
        added = 0
        for iri, groups in self._confirmed_by_iri(confirmed).items():
            try:
                e = self._entity(iri)
            except KeyError:
                continue
            notes = []
            for g in groups:
                db = g["db"]
                suffix = xref_registry.XREF_SUFFIXES.get(db)
                if not suffix:
                    log.warning("Confirmed id for unknown database %r on %s — not stored", db, iri)
                    continue
                current = _split_csv(self._get_annotation(e, self.base + suffix))
                fresh = [i for i in (xref_registry.normalize_id(db, x) for x in g["ids"])
                         if i and i not in current]
                if not fresh:
                    continue
                self._annotation_property(suffix)[e] = current + fresh
                notes.append(f"{db.upper()} {', '.join(fresh)}")
                added += len(fresh)
            if notes:
                self._append_changelog(e, editor,
                                       "Stored confirmed cross-reference: " + "; ".join(notes))
        if added:
            self._save()
        return added

    def remove_flagged_xrefs(self, flagged, editor: str = "curator") -> int:
        """Drop flagged (wrong) cross-reference ids from the disease record itself.

        The counterpart of :meth:`store_confirmed_xrefs`. Flagging a cell wrote a
        negative SSSOM row and a changelog line, but left the id on the disease,
        so an id a curator had judged wrong stayed in the ontology and kept being
        served to users and re-shown in the review grid as if it were unreviewed.

        Ids that are not on the record are ignored, so this is idempotent.
        Returns the number of ids removed.
        """
        removed = 0
        for iri, groups in self._confirmed_by_iri(flagged).items():
            try:
                e = self._entity(iri)
            except KeyError:
                continue
            notes = []
            for g in groups:
                db = g["db"]
                if db not in xref_registry.XREF_SUFFIXES:
                    log.warning("Flagged id for unknown database %r on %s — not removed", db, iri)
                    continue
                drop = {i for i in (xref_registry.normalize_id(db, x) for x in g["ids"]) if i}
                # Every property holding this vocabulary's ids: ARI_DXCODE stores
                # SNOMED CT codes as well, and a flagged code left there was still
                # on the record (issue #171).
                prefix = xref_registry.PREFIX[db]
                for key, suffix in xref_registry.XREF_SUFFIXES.items():
                    if xref_registry.PREFIX[key] != prefix:
                        continue
                    current = _split_csv(self._get_annotation(e, self.base + suffix))
                    kept = [i for i in current
                            if xref_registry.normalize_id(key, i) not in drop]
                    gone = [i for i in current if i not in kept]
                    if not gone:
                        continue
                    self._annotation_property(suffix)[e] = kept
                    notes.append(f"{key.upper()} {', '.join(gone)}")
                    removed += len(gone)
            if notes:
                self._append_changelog(e, editor,
                                       "Removed flagged cross-reference: " + "; ".join(notes))
        if removed:
            self._save()
        return removed

    # --------------------------------------------------------- ENRICHMENT
    def _enrich_disease_dict(self, e) -> dict:
        """Current values an enrichment pass needs for one disease individual."""
        base = self.base
        ari = self._get_annotation(e, base + "ARI_ID")
        return {
            "iri": e.iri,
            "ari_id": ari[0] if ari else None,
            "name": self._get_label(e),
            "synonyms": self._get_annotation(e, base + "ARI_Synonym"),
            "clinical_subtypes": self._get_annotation(e, base + "ARI_ClinicalSubtype"),
        }

    @staticmethod
    def _confirmed_by_iri(confirmed) -> dict:
        """Group reviewed cross-references (``{iri, db, ids}``) by disease iri.

        Shared by the confirmed (store) and flagged (remove) paths — both
        receive the same shape from the publish endpoint."""
        out: dict = {}
        for c in (confirmed or []):
            iri = c.get("iri")
            ids = [str(i).strip() for i in (c.get("ids") or []) if str(i).strip()]
            if iri and ids:
                out.setdefault(iri, []).append({"db": str(c.get("db", "")).strip(), "ids": ids})
        return out

    def enrichment_preview(self, confirmed, index_dir=None) -> dict:
        """Proposed synonym + clinical-subtype additions for confirmed mappings.

        Read-only companion to :meth:`apply_enrichment`: given a review session's
        confirmed (positive) cross-references, return, per disease iri,
        ``{"synonyms": [...], "subtypes": [...]}`` of the *new* values the engine
        would add. Empty when nothing new is proposed (or no indexes are present)."""
        from . import enrich_service
        by_iri = self._confirmed_by_iri(confirmed)
        diseases = []
        for iri in by_iri:
            try:
                diseases.append(self._enrich_disease_dict(self._entity(iri)))
            except KeyError:
                continue
        kw = {"index_dir": index_dir} if index_dir else {}
        return enrich_service.enrich_many(diseases, by_iri, **kw)

    def apply_enrichment(self, confirmed, editor: str = "curator", index_dir=None,
                         selected=None) -> dict:
        """Fold confirmed cross-references' synonyms + subtypes into the ontology.

        For each disease with confirmed (positive) mappings, appends the external
        terms' synonyms to ``ARI_Synonym`` and their direct children to
        ``ARI_ClinicalSubtype`` (requirements 1 & 2), de-duplicated and
        blocklist-filtered by :func:`app.enrich_service.enrich`. Records a per-disease
        changelog entry and saves. Returns a summary
        ``{diseases, synonyms_added, subtypes_added}``.

        ``selected`` narrows what is applied, as
        ``{iri: {"synonyms": [value, ...], "subtypes": [value, ...]}}``. The
        preview offered a single all-or-nothing checkbox, so a curator who wanted
        eight of eleven proposed synonyms had to decline all eleven (issue #117).
        ``None`` keeps the previous behaviour of applying everything proposed.

        Every applied value also gets a line in ``ARI_EnrichmentSource`` naming
        the term it came from. The PR body recorded only counts, so six months
        later nobody could tell whether a synonym came from MONDO, from DOID or
        from a human. The annotation travels with the ontology, so the lineage
        lands in the published artefact rather than in a sidecar file.
        """
        proposals = self.enrichment_preview(confirmed, index_dir=index_dir)
        base = self.base
        syn_total = sub_total = applied_to = 0
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for iri, add in proposals.items():
            try:
                e = self._entity(iri)
            except NotFound:
                continue
            pick = (selected or {}).get(iri) if selected is not None else None
            new_syn = _pick_entries(add.get("synonyms"),
                                    None if pick is None else pick.get("synonyms"))
            new_sub = _pick_entries(add.get("subtypes"),
                                    None if pick is None else pick.get("subtypes"))
            if not new_syn and not new_sub:
                continue
            applied_to += 1
            lineage = []
            if new_syn:
                prop = self._annotation_property("ARI_Synonym")
                prop[e] = self._get_annotation(e, base + "ARI_Synonym") + [x["value"] for x in new_syn]
                syn_total += len(new_syn)
                lineage += [f"ARI_Synonym | {x['value']} | from {x['source']} | {stamp} | {editor}"
                            for x in new_syn]
            if new_sub:
                prop = self._annotation_property("ARI_ClinicalSubtype")
                prop[e] = self._get_annotation(e, base + "ARI_ClinicalSubtype") + [x["value"] for x in new_sub]
                sub_total += len(new_sub)
                lineage += [f"ARI_ClinicalSubtype | {x['value']} | from {x['source']} | {stamp} | {editor}"
                            for x in new_sub]
            prop = self._annotation_property("ARI_EnrichmentSource")
            prop[e] = self._get_annotation(e, base + "ARI_EnrichmentSource") + lineage
            note = []
            if new_syn:
                note.append(f"+{len(new_syn)} synonym(s)")
            if new_sub:
                note.append(f"+{len(new_sub)} clinical subtype(s)")
            self._append_changelog(e, editor,
                                   "Enrichment from confirmed cross-references: " + ", ".join(note))
        if applied_to:
            self._save()
        return {"diseases": applied_to, "synonyms_added": syn_total, "subtypes_added": sub_total}

    # --------------------------------------------------------- ITEM CRUD
    def get_schema(self) -> dict:
        """Field schema for every editable disease-data category."""
        return CATEGORIES

    def _check_item_values(self, category: str, values: dict, adding: bool):
        """Refuse (``Invalid``) item values that cannot be stored as given: a
        required field left empty (or, when adding, missing), a value outside a
        select's options, a number that does not parse, or a property the
        ontology does not declare. Checked before anything is written, so a
        refused request changes nothing."""
        for f in CATEGORIES[category]["fields"]:
            if f["key"] not in values:
                if adding and f.get("required"):
                    raise Invalid(f"{f['label']} is required")
                continue
            sval = "" if values[f["key"]] is None else str(values[f["key"]]).strip()
            if not sval:
                if f.get("required"):
                    raise Invalid(f"{f['label']} is required")
                continue
            if f.get("options") and sval not in f["options"]:
                raise Invalid(f"{f['label']} must be one of {', '.join(f['options'])}, not {sval!r}")
            if f["kind"] in ("intdata", "decdata"):
                try:
                    int(sval) if f["kind"] == "intdata" else float(sval)
                except ValueError:
                    raise Invalid(f"{f['label']} must be a number, not {sval!r}") from None
            if f["kind"] in ("data", "intdata", "decdata") and self.world[self.base + f["key"]] is None:
                raise Invalid(f"the ontology declares no {f['key']} property")

    def _set_item_field(self, e, field: dict, raw):
        """Write one field onto an individual based on its declared kind.
        Values are checked by :meth:`_check_item_values` first."""
        kind = field["kind"]
        base = self.base
        sval = "" if raw is None else str(raw)

        if kind == "label":
            label[e] = [sval] if sval.strip() else []
        elif kind == "comment":
            comment[e] = [sval] if sval.strip() else []
        elif kind == "obsolete":
            prop = self.world[base + "ARI_Obsolete"]
            if prop is not None:
                on = raw is True or sval.strip().lower() in ("true", "1", "yes", "on")
                prop[e] = ["true" if on else "false"]
        elif kind == "seeAlso":
            prop = self.world[SEEALSO_IRI]
            if prop is not None:
                prop[e] = [sval] if sval.strip() else []
        elif kind in ("intdata", "decdata"):
            prop = self.world[base + field["key"]]
            num = sval.strip()
            prop[e] = [int(num) if kind == "intdata" else float(num)] if num else []
        else:  # "data" or "ann"
            prop = self.world[base + field["key"]]
            if prop is None:
                return
            prop[e] = [sval] if sval.strip() != "" else []

    def _apply_item_fields(self, e, category: str, values: dict) -> list:
        applied = []
        for f in CATEGORIES[category]["fields"]:
            if f["key"] in values:
                self._set_item_field(e, f, values[f["key"]])
                applied.append(f["key"])
        self._name_from_fields(e, CATEGORIES[category])
        return applied

    def _name_from_fields(self, e, spec: dict):
        """Label an item of a category that has no label field from the values
        named in its ``label_from``: "Prevalence · US · 2019 · Female"."""
        if "label_from" not in spec:
            return
        parts = [str(v) for key in spec["label_from"]
                 for v in self._get_data(e, self.base + key)]
        label[e] = [" · ".join(parts)]

    def _symptom_concept(self, category: str, values: dict) -> str | None:
        """The ARIS class IRI a Symptom's new label names, or None."""
        if category != "symptoms" or "name" not in values:
            return None
        return self._symptom_concepts.get(str(values["name"] or "").strip())

    def _set_symptom_concept(self, e, concept: str | None):
        """Link a Symptom to the shared ARIS class its label names (ARI#152), or
        to none, and drop any ARI_NoConcept decision: the label changed, so that
        decision was about a different string. Its label is the key, so adding
        or renaming one is what leaves the link missing or stale.

        Written as triples: the value is an IRI, and owlready2's property setter
        stores a string literal for one and cannot delete the old resource."""
        prop = self._annotation_property("sharedConcept")
        no_concept = self._annotation_property("ARI_NoConcept")
        self.onto._del_obj_triple_spo(e.storid, prop.storid, None)
        self.onto._del_data_triple_spod(e.storid, prop.storid, None, None)
        self.onto._del_data_triple_spod(e.storid, no_concept.storid, None, None)
        if concept:
            self.onto._add_obj_triple_spo(e.storid, prop.storid, self.world._abbreviate(concept))
        e.__dict__.pop(prop.python_name, None)     # owlready2's cached values
        e.__dict__.pop(no_concept.python_name, None)

    def add_item(self, disease_iri: str, category: str, values: dict, editor: str = "user") -> dict:
        if category not in CATEGORIES:
            raise NotFound(f"Unknown category: {category}")
        spec = CATEGORIES[category]
        de = self._entity(disease_iri)
        cls = self.world[self.base + spec["cls"]]
        if cls is None:
            raise NotFound(f"Class not found: {spec['cls']}")
        self._check_item_values(category, values, adding=True)
        concept = self._symptom_concept(category, values)
        local = f"{spec['id_prefix']}_{uuid.uuid4().hex[:8]}"
        with self.onto:
            new = cls(local)
        self._apply_item_fields(new, category, values)
        if not (label[new] if hasattr(new, 'storid') else []):
            label[new] = [str(values.get("name") or local)]
        if category == "symptoms":
            self._set_symptom_concept(new, concept)
        link = self.world[self.base + spec["link"]]
        link[de].append(new)
        self._append_changelog(de, editor, f"Added {spec['label']}: {self._get_label(new)}")
        self._save()
        return self.get_disease_detail(disease_iri)

    def update_item(self, item_iri: str, category: str, changes: dict,
                    disease_iri: str = "", editor: str = "user") -> dict:
        if category not in CATEGORIES:
            raise NotFound(f"Unknown category: {category}")
        e = self._entity(item_iri)
        self._check_item_values(category, changes, adding=False)
        concept = self._symptom_concept(category, changes)
        old_label = self._get_label(e).strip()
        applied = self._apply_item_fields(e, category, changes)
        # The editor sends every field on every save, so "name" is always in
        # changes: relink only when the label itself changed.
        if category == "symptoms" and "name" in changes and self._get_label(e).strip() != old_label:
            self._set_symptom_concept(e, concept)
        if disease_iri and applied:
            de = self._entity(disease_iri)
            self._append_changelog(
                de, editor,
                f"Edited {CATEGORIES[category]['label']} '{self._get_label(e)}': {', '.join(applied)}")
        self._save()
        return self.get_disease_detail(disease_iri) if disease_iri else {"ok": True}

    def delete_item(self, item_iri: str, category: str, disease_iri: str,
                    editor: str = "user") -> dict:
        e = self._entity(item_iri)
        de = self._entity(disease_iri)
        name = self._get_label(e)
        spec = CATEGORIES.get(category)
        if spec:
            link = self.world[self.base + spec["link"]]
            if link is not None:
                try:
                    link[de].remove(e)
                except (ValueError, KeyError):
                    pass
        destroy_entity(e)
        self._append_changelog(de, editor, f"Deleted {spec['label'] if spec else 'item'}: {name}")
        self._save()
        return self.get_disease_detail(disease_iri)

    # --------------------------------------------------------- RELEASES
    @property
    def _releases_dir(self) -> Path:
        d = self.path.parent.parent / "releases"
        d.mkdir(exist_ok=True)
        return d

    @property
    def _manifest_path(self) -> Path:
        return self._releases_dir / "releases.json"

    def list_releases(self) -> list:
        return atomic_store.read_json(self._manifest_path, [])

    def _current_version(self) -> str:
        releases = self.list_releases()
        if releases:
            return releases[-1].get("version", "1.0")
        # fall back to a disease version annotation
        for d in self._all_diseases():
            v = self._get_annotation(d, self.base + "ARI_Version")
            if v:
                return v[0]
        return "1.0"

    def create_release(self, version: str = "", notes: str = "", editor: str = "admin") -> dict:
        """Snapshot the current ontology into releases/, stamp the version onto every
        disease, and record a per-disease changelog entry."""
        # Read the manifest first: a corrupt one stops the release before anything is stamped.
        records = self.list_releases()
        version = version.strip() or f"1.{len(records) + 1}"
        ts_human = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ts_file = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # Stamp version + changelog onto every disease before snapshotting
        base = self.base
        ver_prop = self.world[base + "ARI_Version"]
        clog = self.world[base + "ARI_ChangeLog"]
        for d in self._all_diseases():
            if ver_prop is not None:
                ver_prop[d] = [version]
            if clog is not None:
                note = f" - {notes}" if notes else ""
                entry = f"{ts_human} | {editor} | Released v{version}{note}"
                clog[d] = list(clog[d]) + [entry]
        self._save()

        fname = f"ari_t1d_v{version}_{ts_file}.owl"
        dest = self._releases_dir / fname
        shutil.copy2(self.path, dest)

        record = {
            "version": version,
            "file": fname,
            "timestamp": ts_human,
            "notes": notes,
            "released_by": editor,
            "diseases": len(self._all_diseases()),
            "individuals": len(list(self.onto.individuals())),
        }
        records.append(record)
        atomic_store.write_json(self._manifest_path, records, indent=2)

        # Expire per-term feedback (entries flagged "keep" are retained).
        fb = self.feedback.archive_on_release(version)
        record["feedback_archived"] = fb["archived"]
        record["feedback_retained"] = fb["retained"]
        return record
