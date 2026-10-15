# Structured epidemiology records (#229)

**Goal.** Put prevalence and incidence in one table that can hold several rows per disease, split by
measure (prevalence or incidence), region (US or worldwide), year and sex.

**What we have today.** Each disease has 12 flat fields: a rate, a year and a source list for each
combination of measure and region. That gives one row per combination, one year, and no sex split.
Sex appears only in the free-text `demographicBias`, which one disease uses. In the data repo the
master-list import (`notebook/master-list-comparison/import_prevalence.py`) fills the four rates.
The years and sources are empty.

## Design

**Each row is an item, like a symptom.** We add a new item category, `epidemiology`, to
`app/schema.py`. It uses the class and link that the data model
(`KrishnaTO/ARI: data_model/immunological_data_model.owl`) already declares: `EpidemiologyProfile`
and `hasEpidemiologyProfile`. Because rows are items, they get the existing add, edit and delete
API, the item editor, publishing (`ITEM_LINKS`) and the working-copy resync without new code.
Changing `ITEM_LINKS` already forces each copy to do a full re-merge once (#225).

| Field | Property | Kind | Values |
|---|---|---|---|
| Measure | `epiMeasure` | select | Prevalence, Incidence |
| Region | `epiRegion` | select | US, Worldwide |
| Year | `epiYear` | intdata | e.g. 2019 |
| Sex | `epiSex` | select | All, Female, Male |
| Rate /100k | `epiRatePer100k` | **decdata** (new) | Number. Incidence is per 100k per year |
| Source URL | `sourcePMID` | data | Existing shared field |

- **No Label input.** The server builds the label from the fields (for example, `Prevalence · US ·
  2019 · Female`) when it adds or edits the row, so the label cannot drift from the fields.
- **New `decdata` kind.** It stores a typed decimal and rejects anything that is not a number, with
  a 400 error. Today the generic `data` kind would store "about 30" as a string without complaint.
- **Fields that stay on the disease:** Estimated US cases (`prevalenceValue`), age range,
  prevalence description, and demographic bias (relabelled *Sex / demographic notes*, for ratios
  such as "F:M 3:1").
- **Fields that are removed** from reading, writing, the editor, the new-disease form, the diff
  labels, the build script and the test fixture: the 12 flat `prevalence_*` and `incidence_*` rate,
  year and source fields.

**Read panel.** The stat cards and the bar chart (which plotted a rate next to a case count) are
replaced by one table:

```
Measure     Region     Year  Sex     Rate /100k  Source
Prevalence  US         2019  All     12.3        cdc.gov
Prevalence  US         2019  Female  18.0        cdc.gov
Incidence   Worldwide  2018  All     1.1         who.int
```

Rows are sorted by measure, then region, then year (newest first), then sex (All, Female, Male).
Below the table: estimated US cases, sex / demographic notes, age range and description. The story
box count becomes the number of rows.

**Migration.** A new script, `scripts/migrate_epidemiology.py <owl>`, works on an OWL file. For
each non-empty flat rate it creates one record: that measure and region, sex All, the year and
sources if present, and a changelog line. It then deletes the flat values and declares the class
and properties if they are missing. It runs twice:
1. On this repo's test fixture, in this PR. (`ontologies/ari_t1d.owl` here is gitignored, so it is
   a local copy only.)
2. On `KrishnaTO/ARI`'s `ontologies/ari_t1d.owl`, in a separate data-repo PR. That PR must merge
   before or together with the app deploy. Otherwise the deployed app reads no rows, and the flat
   values stay in the file unused.

**Tests.** Replace the flat-incidence test with three tests:
- Adding a record and reading it back.
- A rate that is not a number gets a 400 and is not stored.
- The migration turns flat fields into records and leaves no flat values behind.

## Open points (my default in bold; all four defaults approved 2026-10-15)

1. **Region:** **a fixed choice of US or Worldwide**, as in the issue. A free-text country field
   could come later.
2. **Sex:** **All, Female or Male**.
3. **Data-repo PR:** **I open it on KrishnaTO/ARI with the migrated `ari_t1d.owl`**, after this PR
   is reviewed. The file is 4.6 MB, so the diff is large but mechanical.
4. **Curators' unpublished flat-field edits:** working copies get re-merged once. A curator with an
   unpublished edit to a flat rate would lose it. **Accept this risk**: the flat fields are nearly
   empty apart from the imported rates.
