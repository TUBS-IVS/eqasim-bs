# ADR-0198 · 2026-11-17 · Seed records are resolved into the census control categories, and building-type targets count households (issue #647)

- **Status:** active
- **Context:** The PopulationSim seed (MiD donors) and the census controls did not use the same
  categories, so some seed records matched no control category and one target was infeasible.
  Three defects, each closed here; the pattern follows ADR-0192 (resolve once per donor, the
  clones inherit):
  1. **Sex.** MiD `HP_SEX` carries 3 "divers" and 9 "keine Angabe". The census sex controls
     are binary, so such a person counted in no sex category, and the old per-copy draw after
     expansion made the output disagree with what the balancer had counted.
  2. **Tenure and building type.** MiD `H_MIETE` (codeplan: MiD 2023 Codepläne B1 v1.1,
     sheet Haushalte) is 1 Miete, 2 Eigentum, 3 anderes, 9 keine Angabe, 309 kein Privat-HH
     (CATI/CAWI). The census renter target `MieterHH` is the household total minus the owner
     households (the census preparation; external private repository, not cited by file; the
     definition was checked against that code on 2026-11-17), so renters are
     "not owner-occupied". `haustyp` is "Gebäudetyp (Raumbezug: Siedlungsblock)"
     with 95 "nicht zuzuordnen" (same codeplan).
  3. **Building-type targets.** The per-100 m building-type targets were census DWELLING
     counts, whose sum exceeds the household total of the same cell, so the target set was
     infeasible for the household-weighted seed.
- **Decision:**
  1. **Sex** (`braunschweig.popsim.attributes.resolve_binary_sex`, tier rule
     `braunschweig.popsim.missing.impute_binary_sex`): codes 3 and 9 are resolved ONCE per
     donor person, drawn unweighted from the valid persons of the same `alter_gr1` band, else
     from all valid persons. Seeded stream `random_seed + 97531` (the stage's seed-fill
     stream); the clones inherit the value. `sex_raw` keeps the reported category internally;
     NO reported-sex output attribute is added (owner decision 2026-11-16). Reasoning source:
     Statistisches Bundesamt, page "Daten zur Änderung des Geschlechtseintrags für die Jahre
     2024 und 2025 und vorläufige Ergebnisse für 2026",
     https://www.destatis.de/DE/Themen/Gesellschaft-Umwelt/Bevoelkerung/Geburten/daten-zur-aenderung-geschlechtseintrag.html
     (accessed 2026-11-17): for analyses by characteristics, "divers" and "ohne Angabe" are
     "über ein definiertes Umschlüsselungsverfahren auf die Geschlechtsausprägungen „männlich"
     und „weiblich" verteilt"; on the Zensus reference date 969 persons were registered as
     diverse and 1 259 had the entry "ohne Angabe" (same page). ASSUMPTION: the Zensus 2022
     grid tables used for the controls follow that procedure (the page does not name the grid
     tables). ASSUMPTION: the band rule is ours, not the Destatis procedure.
  2. **Tenure** (`attributes.resolve_tenure_and_building_type`): codes 3 and 309 are re-coded
     to 1 (renter class) WITHOUT a draw (owner decision 2026-11-17; ASSUMPTION: "anderes" is
     not owner-occupation). Code 9 is drawn by an `H_GEW`-weighted hot deck
     (`missing.weighted_hot_deck`) whose donor pool includes the re-coded households, since
     they belong to the census renter class (controller ruling).
  3. **Building type:** haustyp 95 is drawn by the same hot deck (raw code 1-4). ASSUMPTION:
     missing at random within the cell (RegioStaR7, `hhgr_gr`, `hh_type5`), with the tier
     fallbacks of `HOUSEHOLD_FILL_TIERS`, each counted and logged. The attribute describes the
     settlement block, not the dwelling.
  4. **Flags:** `housing_tenure_imputed` and `building_type_imputed` are true for every
     non-reported code (re-coded and drawn). The income spatial tilt keeps
     imputed-tenure households neutral exactly as "unknown" households were before (controller
     ruling: this work does not change the income tilt).
  5. **Building-type targets** (`braunschweig.popsim.building_type_targets.
     rescale_building_type_to_households`): per 100 m cell the float target is
     `household_total * dwellings_k / sum(dwellings)`, before the existing per-column 1 km
     integerisation. Cells with households but no dwellings take the shares of their 1 km
     cell, then of their Kreis. The popsim validation targets call the same function.
  6. **Contract check** (`braunschweig.popsim.seed_contract`, mode key
     `braunschweig.population.popsim.seed_contract_mode`): before any PopulationSim batch it
     checks that each seed record of an exhaustive control group matches exactly one category
     and that the household group targets sum to the household total, per Kreis, within the
     tolerance shared with the WP1 batch-diagnostics tool (`control_groups`). `error` raises,
     `warn` logs. The legacy path (`complete_members` False) and the ENTD source run in
     `warn` only. The per-Kreis ratios on the real cells are NOT yet measured; they are to be
     measured before the arm-2 run.
  7. **Legacy path** (`complete_members` False) is unchanged.
  8. **Order:** member completion and the weekend plan match run (in the cached
     `completed_donor` stage) BEFORE the resolution and still match on the reported sex code.
  9. **Keys** (base config, all four on): `seed_resolve_binary_sex`,
     `seed_resolve_tenure_building_type`, `building_type_targets_to_households`,
     `seed_contract_mode`. The draw order is sex, tenure, building type on ONE stream, so
     switching the sex key off shifts the later draws (accepted; an OFF run is not
     draw-identical for tenure).
- **Rejected alternatives:**
  - A per-copy sex draw after expansion (the old behaviour): controls and output disagree.
  - A third control category for divers / no entry: the census tables are binary.
  - Dropping the records: loses survey weight.
  - A hot deck for tenure codes 3 / 309: would turn non-owners into owners.
  - Keeping dwelling targets: infeasible, their sum exceeds the households.
  - Re-tuning the control importances: out of scope here.
- **Consequences:** every stage downstream of PopulationSim recomputes. No result claims are
  made: the arm-2 run manifest will hold the measurement. The building-type importances were
  tuned against the infeasible dwelling targets and are NOT retuned here. The stage
  validation tokens now cover the new helper modules (the tuples are in code), so edits to
  them invalidate those caches (intended). `home_match_validation` type matches now include drawn haustyp-95 types (open,
  split by `building_type_imputed` is a possible follow-up).
- **Regression tests:** `tests/test_popsim_seed_fills.py`, `tests/test_building_type_targets.py`,
  `tests/test_popsim_seed_contract.py`, `tests/test_popsim_income_spatial_tilt.py`,
  `tests/test_participation_universe_controls.py`.
