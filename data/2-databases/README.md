# `data/2-databases` — reference database indexes for cross-reference prediction

These files back the **predicted** (yellow) cross-references on the reference-review
page (issue #42). For a disease whose target-database cell is still blank,
[`app/predict_service`](../../app/predict_service.py) exact-matches the disease's
label and synonyms against the terms in these indexes and proposes a candidate id —
which a curator then verifies and confirms.

## What's here

| File | Source | Built from | Committed? |
| --- | --- | --- | --- |
| `mondo.index.tsv` | [MONDO](https://mondo.monarchinitiative.org/) (CC BY 4.0) | `raw/mondo.obo` | yes |
| `doid.index.tsv`  | [Human Disease Ontology](https://disease-ontology.org/) (CC0) | `raw/doid.obo` | yes |
| `ncit.index.tsv`  | [NCI Thesaurus](https://ncithesaurus.nci.nih.gov/) (public domain) | `raw/ncit.obo` | yes |
| `mesh.index.tsv`  | [MeSH](https://www.nlm.nih.gov/mesh/) (NLM, public domain) | `raw/mesh_desc2026.xml` | yes |
| `orphanet.index.tsv` | [Orphanet](https://www.orphadata.com/) (CC BY 4.0) | `raw/orphanet_product1.xml` | yes |
| `<db>.details.tsv` | definitions + parents for each index above | same `raw/` dump | yes (sidecar, loaded on demand) |
| `<db>.subtypes.tsv` | direct `is_a` parent->child edges (OBO sources only) | same `raw/` dump | yes (read by the enrichment engine) |
| `raw/` | downloaded release dumps | — | no (git-ignored, large) |

`ncit`, `mesh` and `orphanet` are filtered to disease terms so the indexes stay
small: NCIt to its disease semantic types (NCIT:P106) — and its UMLS CUI (P207) is
harvested as a `umls` cross-reference; MeSH to the Diseases (`C*`) and Mental
Disorders (`F03*`) tree categories; Orphanet's cross-references are limited to its
*exact* mappings (ICD-10/OMIM/UMLS/MeSH/SNOMED). All five match a disease directly
on their own labels and synonyms — independent lexical sources, not just MONDO's
xref view.

Each `*.index.tsv` is one row per ontology term:

```
id	label	synonyms	<only the target columns this source actually fills>
```

`synonyms` is ` | `-joined (EXACT synonyms only); each database column holds the
`;`-joined ids that term cross-references there (its own column holds its own id).
This file is loaded on **every** prediction request, so it is kept lean — labels,
synonyms and cross-references only.

Two things are deliberately *not* in the file:

* **Columns this source never fills.** The full column vocabulary is
  `predict_service.INDEX_COLS`, but each source populates only a few of it — MeSH
  fills one, NCIt two — and `omop` is empty in all five (there is no freely
  redistributable OMOP source). `write_index` emits only the populated columns;
  `predict_service` reads columns by name, so an absent column reads as "this
  source cross-references nothing there", which is what it means. A hand-supplied
  index may still carry the full set.
* **The column repeating the term's own id.** A source's own column (`mondo` in
  `mondo.index.tsv`) held nothing but its `id` with the prefix stripped — one value
  per row, on every term in the file. `load_index` reconstructs it with
  `xref_registry.normalize_id`, so it is no longer written.
* **`omim`.** OMIM is no longer a review column or a prediction target (see
  `app/xref_registry`), so nothing reads it out of an index. `parse_obo` still
  harvests it, so restoring the column is a `review: True` flip plus a rebuild.
* **Synonyms that only echo the label.** Sources routinely repeat a term's own name
  as its first synonym — NCIt on nearly every row. Both the matcher and the
  enrichment engine walk `[label] + synonyms` and keep the first spelling of each
  key, so an echo can never match anything the label does not; it is only a
  duplicate chip in the compare pane. The comparison is casefold-only, so
  punctuation and accent variants ("Sjögren" vs "Sjogren") are kept as the distinct
  names they are.

### Detail sidecars — `<db>.details.tsv`

Definitions and parent terms live in a **sidecar** next to each index, not in the
index itself:

```
id	definition	parents
```

`definition` is a single de-tabbed string; `parents` is ` | `-joined **term labels**
(the `is_a`/hierarchy parents, resolved to labels in a second pass). A row is written
only when the term has a definition or parents. These back the reference-review
**compare pane** (`GET /api/v2/concept/{db}/{id}`), which `app/concept_service` loads
lazily — the prediction hot path never parses them.

The split is deliberate: folding definitions into the index would roughly **double**
its size (measured before the column/synonym pruning above: 13.9 MB → 32.2 MB) while slowing
every prediction load for data prediction never uses. As a sidecar the indexes stay
~10.9 MB and the ~18 MB of
details load only when a curator opens the compare pane — and only the sidecar for
the database being looked up (a DOID lookup costs ~8 MB resident, not the ~52 MB all
five would). This matters on the small hosted instance; see `DEPLOY.md`.
`concept_service` treats a
**missing sidecar** as "details not built for this database" (`details_available:
false`) and a **missing id within a present sidecar** as "this term genuinely has
none" — so a checkout that never regenerated still answers, with label + synonyms +
cross-reference provenance and `details_available: false`.

| Source | `definition` from | `parents` from |
| --- | --- | --- |
| MONDO, DOID, NCIt | OBO `def:` | OBO `is_a:` targets, resolved to labels in a second pass (a target filtered out of a disease-only index keeps its id) |
| MeSH | preferred concept `ScopeNote` | *(empty — MeSH hierarchy is tree numbers, a different mechanism)* |
| Orphanet | `SummaryInformation` text (en_product1) | *(empty — the classification lives in en_product3, which this script does not download)* |

| Detail sidecar | Size |
| --- | --- |
| `mondo.details.tsv` | ~5.9 MB |
| `ncit.details.tsv` | ~5.8 MB |
| `orphanet.details.tsv` | ~2.8 MB |
| `doid.details.tsv` | ~2.4 MB |
| `mesh.details.tsv` | ~1.3 MB |

### Subtype edges — `<db>.subtypes.tsv`

The OBO sources (MONDO, DOID, NCIt) also get an edge file, one row per direct
`is_a` parent->child link:

```
parent_id	child_id
```

Both are full CURIEs, so one flat map serves every database. This is the details
sidecar's hierarchy read the other way round: the sidecar answers "what is this term
a kind of?" for the one term a curator is looking at, while `app/enrich_service` asks
the inverse — "what are this term's children?" — across the whole ontology, to propose
them as clinical subtypes of a confirmed disease.

The edge carries **no label**. A child's name is already in that child's own index
row, so repeating it here cost 3.7 MB and let the two files disagree whenever a term
was renamed between rebuilds — they did, for 22 terms. `enrich_service` resolves the
name from the index instead, and skips a child the loaded indexes do not know.

## Coverage of the ten target databases

**MONDO is the hub.** A single MONDO term carries xrefs to SNOMED (`SCTID`), DOID,
NCI, ICD-10-CM, Orphanet, OMIM, UMLS and MeSH, so matching a disease name to MONDO
can fill **nine of the ten** columns at once. DOID, NCI, MeSH and Orphanet add four
more independent lexical sources — a disease whose name misses MONDO but hits one of
them still gets a prediction, and NCI/Orphanet contribute their own cross-references
(NCI→UMLS; Orphanet→ICD-10/OMIM/UMLS/MeSH/SNOMED). All five are freely
redistributable, so their indexes are committed here.

**OMOP is not covered.** OMOP concept ids are OHDSI-specific and are not carried by
these ontologies. Predicting OMOP requires the OHDSI **Athena** vocabulary bundle,
which is license-gated and cannot be redistributed here. SNOMED predictions come
only via MONDO's / Orphanet's xrefs — the primary SNOMED CT release is
license-restricted and is not stored in this repo.

To extend predictions with a licensed vocabulary you are entitled to use, drop a
compatible `<db>.index.tsv` (same columns) into this folder — `predict_service`
loads every `*.index.tsv` automatically. Do **not** commit license-restricted data
(the `.gitignore` here blocks the common cases).

## Regenerating

```bash
python scripts/fetch_databases.py              # download raw + rebuild indexes
python scripts/fetch_databases.py --offline    # rebuild from existing raw/ only
python scripts/fetch_databases.py --only mondo  # just one source
```

The predicted-SSSOM snapshot ([`mappings/ari.predicted.sssom.tsv`](../../mappings/ari.predicted.sssom.tsv))
is produced from these indexes plus the current ontology; see `predict_service.build_predicted_sssom`.
