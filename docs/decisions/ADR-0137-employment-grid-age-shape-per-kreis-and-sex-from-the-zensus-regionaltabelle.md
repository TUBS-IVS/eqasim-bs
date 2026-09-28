# ADR-0137 · 2026-09-28 · Employment grid age shape per Kreis and sex from the Zensus 2022 Regionaltabelle

- **Status:** active
- **Numbering:** ADR-0137 is the next free id. Checked on 2026-09-28 across ALL local and remote
  branches (`git ls-tree docs/decisions/` for every ref) and the local worktrees:
  - `main` holds up to ADR-0133.
  - `fix/parallel-memory-robustness` holds ADR-0135.
  - `chore/i434-test-suite-followup` holds ADR-0134 and ADR-0136.
- **Issue:** none; the change was asked for directly in the session of 2026-09-28.
- **Supersedes / amends:** amends ADR-0016 (the employment grid control), whose age shape it replaces.

## Context

The employment grid control (ADR-0016) makes PopulationSim reproduce employed persons per 100 m cell
by sex and five age groups (16–29, 30–39, 40–49, 50–59, 60+). Per Kreis and sex it takes the census
level of employed persons (cleancensus `kreis_erwerbsstatus`, `ERWERBSTAT_KURZ_STP__11_{M,W}`),
splits it across the age groups with an age SHAPE, and distributes each part to the cells by their
residents of that sex and age group.

The shape came from `zensus2022_employment_by_age_ref.csv`, an extract of Zensus 2022 table
2000S-2001:

- It had exact rows only for the three kreisfreie Städte, so the five Landkreise (Gifhorn, Goslar,
  Helmstedt, Peine, Wolfenbüttel) took the national shape of large municipalities.
- It gives one shape for both sexes.
- The stage logged this as a fallback by design ("3/8 Kreise exact, 5 used the national
  DE_large_gemeinden fallback").

Two facts made it replaceable:

1. **The 100 m grid carries no employment.** The harmonised cleancensus cell tables (100 m and 1 km)
   have no employment column. The Zensus collects employment status in its sample survey and does not
   publish it on the grid. So the per-cell targets have to be built from Kreis figures, as the control
   does.
2. **The exact Kreis figures exist offline.** The Zensus 2022 Regionaltabelle "Bildung und
   Erwerbstätigkeit", whose copy the cleancensus repository keeps, has a sheet `CSV-ET_Alter`:
   - employed persons in seven age classes (15–19, 20–29, 30–39, 40–49, 50–59, 60–67, 68+) by sex;
   - for Germany, every Land, every Kreis and every Gemeinde;
   - reference date 15 May 2022.

   It comes from the same survey and date as the census levels the shape is applied to. For the
   three kreisfreie Städte it agrees with the previous extract (Braunschweig 15–19: 2,950 employed in
   both).

## Decision

1. **`scripts/extract_zensus2022_employed_by_age.py` writes the reference.** It reads sheet
   `CSV-ET_Alter` for the eight ZGB Kreise and Germany into
   `eqasim-data/data/braunschweig/popsim/zensus2022_employed_by_age_kreis.csv` (126 rows).
   - It checks the class order against the readable sheet `ET Alter`.
   - It fails on a missing region or a withheld ("/") count instead of writing a shape with a hole.
   - It records the source file's SHA-256 in the header.
2. **The employment grid takes its age shape per Kreis AND sex from that table**
   (`zensus_employment_age.load_kreis_sex_age_shares`, default of the new key
   `braunschweig.population.popsim.employment_grid_age_shape_source: zensus2022_kreis_by_sex`).
   - 15–19 joins 20–29 in the 16–29 group, as the previous extract's 10–19 band did (the grid's
     population denominator starts at 16).
   - 60–67 and 68+ form 60+.
   - `employment_grid.per_cell_employment_targets` accepts one shape per sex; a single shape still
     works unchanged.
3. **A Kreis missing from the table takes Germany's shape and the stage logs a WARNING naming it.**
   Under this source a missing Kreis is a data gap, not a design choice. All eight ZGB Kreise are in
   the table, so production logs "8/8 Kreise exact, 0 fell back".
4. **The previous source stays selectable and exact:** `employment_grid_age_shape_source:
   zensus2022_2000S_2001` reproduces the previous targets frame for frame (tested).
5. **The table is local-only, not committed.** The data-protection policy in `.gitignore` admits
   only the listed aggregate tables. The committed script, the Data Registry record
   (`zensus2022_employed_by_age_kreis`, verifier entry B13), `DOWNLOAD_CHECKLIST_BS.md` and the README
   make it reproducible.

## Effect on the targets

The effect was measured with the script's comparison mode on the real table:
`--compare-with eqasim-data/data/braunschweig/popsim/zensus2022_employment_by_age_ref.csv`, which
calls `zensus_employment_age.compare_age_shapes`. The figures below are the new share per sex minus
the previous sex-pooled share, in percentage points.

- **The three kreisfreie Städte move by at most 1.2 points.** Their shape was already exact; what
  changes is the split by sex.
- **The five Landkreise move by up to 5.0 points, all in the same direction.** Their employed
  persons are older than the national shape of large municipalities:

  | Age group | Change in the Landkreise |
  |---|---|
  | 16–29 | −1.4 to −4.3 |
  | 30–39 | −0.9 to −4.0 |
  | 50–59 | +2.7 to +5.0 |

  For example, women aged 50–59 in Peine: +5.0.

## Rejected alternatives

- **Keeping the national fallback for the Landkreise.** The exact figures exist in the same census
  release; a documented fallback is not a reason to keep a known gap.
- **Deriving the Landkreis shape from the BA employee statistics** (GENESIS 13111-06-02-4, employees
  subject to social insurance at residence, by Kreis and age). The table is already in the data set
  and covers every Kreis, but it counts no civil servants, self-employed or marginally employed
  persons, so its age structure differs by definition. It also merges 30–49 and dates from
  30 June 2025, not the census date. Any shape derived from it would be an approximation next to the
  exact census figure.
- **Survey-based shapes** (SrV 2023, MiD 2023). Their samples per Landkreis are far smaller than the
  census count, so a five-group split from them would be noisier, and the census figure exists.
- **Committing the table like the previous extract.** The previous `zensus2022_employment_by_age_ref.csv`
  is committed, but the data-protection policy in `.gitignore` does not list census tables among the
  committable aggregates. Admitting one would be a policy change for the maintainer, not a side effect
  of this record.

## Consequences

- **PopulationSim recomputes.** The shape code (`zensus_employment_age`, `employment_grid`) and the
  stage's configuration changed, and everything downstream recomputes with it.
- **Results change as intended.** In the five Landkreise the synthetic employed persons become older
  (more 50–59, fewer under 40). Everywhere, each sex keeps its own age structure. Whether the realised
  population then matches the census per Kreis, sex and age group is the open validation of the
  feature record `employment_grid_control`. PopulationSim fitting these targets is convergence, not
  validation.
- **Setup changes.** A new run needs the table in place:
  - regenerate it with the script from the Regionaltabelle;
  - or set the previous source.

  The stage raises a FileNotFoundError naming both options.

## Evidence

- `scripts/extract_zensus2022_employed_by_age.py`
- Tests:
  - `tests/test_extract_zensus2022_employed_by_age.py`
  - `tests/test_zensus_employment_age.py`
  - `tests/test_popsim_employment_grid.py`
  - `tests/test_popsim_employment_grid_age_shape_source.py`
- Registry records:
  - `docs/registry/data/zensus2022_employed_by_age_kreis.yml`
  - `docs/registry/features/employment_grid_control.yml`
