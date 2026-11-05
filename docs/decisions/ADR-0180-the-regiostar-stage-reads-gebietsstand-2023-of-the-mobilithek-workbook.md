# ADR-0180 · 2026-11-05 · The RegioStaR stage reads Gebietsstand 2023 of the mobilithek workbook

- **Status:** accepted (implemented on branch `claude/langelsheim-regiostar-gebietsstand-978a55`)
- **Numbering:** 0180 checked free across all local and remote branches and open PRs on 2026-11-05.
- **Issue:** none; owner decision 2026-11-05: fix directly, without an issue.
- **Owner decision (2026-11-05):** pin the official workbook from mobilithek.info, not the owner's earlier download.

## Context

- `braunschweig.data.bbsr.regiostar` read the sheet `ReferenzGebietsstand2020` of the BMV bundle
  `regiostar_referenzdatei.xlsx` and fills every VG250 Gemeinde that the sheet lacks with the RegioStaR-7 code of the
  nearest known Gemeinde, without a name, RegioStaR17 or RegioStaRGem7. VG250 is Gebietsstand 31.12.2024.
- Langelsheim merged with Lutter am Barenberge, Hahausen and Wallmoden on 1 Nov 2021 under the new code `03153019`, so
  it was the one filled ZGB Gemeinde. Its cars carried the fleet label "NAN" (`astype(str)` of the missing name), missed
  the Gemeinde EV tilt and had RegioStaR-7 74 instead of BBSR's 77; 74 is also urban, 77 rural, in the RegioStaR-2
  collapse that the IPF and popsim keys use. Every reader of the stage's `regiostar7` saw the filled 74: the
  population RegioStaR join, the enriched economic-status, car-count, income and tenure draws, popsim's tenure input,
  the IPF urban/rural key, gravity, education gravity and the fleet. Only the fleet reads `name`. Size of the effect: run manifest
  `prod100-fleet-composition-accounting-2026-11-04`.
- BMV now publishes the reference files on mobilithek.info (offer linked from the BMV RegioStaR page) as one workbook
  with sheets for Gebietsstand 2015 to 2024. The sheet-by-sheet comparison for the ZGB scope is run manifest
  `regiostar-gebietsstand-comparison-2026-11-05`.

## Decision

1. The stage reads `ReferenzGebietsstand2023` (`SHEET`, columns `gem_23`/`name_23`) of the workbook that
   `scripts/download_regiostar.py` fetches and SHA-pins (`regiostar/regiostar_referenzdateien_2024.xlsx`).
   Its ZGB Gemeinde set equals the VG250 set, and it changes no value of any other ZGB Gemeinde, so Langelsheim is the
   only row that changes (it now takes the primary path with BBSR's own assignment).
2. The nearest-neighbour fill stays as the logged fallback for a future Gebietsstand gap. Any filled Gemeinde is now a
   WARNING that names each code with its VG250 name and says what it lacks.
3. `household.py` keeps a missing Gemeinde name missing; a car without a name is counted and logged as a Gemeinde-tilt
   fallback and never gets a text label.
4. `braunschweig.analysis.spatial` reads the same sheet as the stage. The legacy gravity calibration and
   `scripts/measure_gemeinde_join_coverage.py` read the new file but keep its 2020 sheet, which equals the bundle's, so
   they reproduce their historical figures.

## Rejected options

- **Gebietsstand 2024** (matches the VG250 vintage): 7 of the 122 other ZGB names are damaged in that sheet (encoding
  defects, a dropped space); they would break the name join to the KBA Gemeinde tables. Revisit when BBSR corrects it.
- **A hand-kept crosswalk** `03153019 -> 03153007` on the 2020 sheet: duplicates an assignment BBSR already publishes and
  needs a new entry at every merger.
- **Masking the name downstream only:** leaves the wrong type and the missing tilt (decision 3 is kept as a guard, not
  as the fix).

## Consequences

- The stage output changes for Langelsheim only (RegioStaR-7 77, rural; name, so its cars get their own Gemeinde
  tilt). Seeded draws downstream can still shift other households, because their random streams see a changed input
  (not measured). Landing it
  re-executes 46 of the 108 stages of the production DAG snapshot (`docs/registry/dag/production.json`): the
  RegioStaR stage and its descendants (the census filter, the fleet and the MATSim run included) plus the stages whose
  source token covers a changed module (`scripts/audit_synpp_helper_hash.py`: `braunschweig.data.hts.mid_donor` and
  four analysis stages).
- `GEMEINDE_GEBIETSSTAND_CROSSWALK` in `fleet_sampling_de.py` (ADR-0083) maps the predecessors' names to Langelsheim.
  Residents no longer carry those names, so it no longer fires for them; it is left in place.
