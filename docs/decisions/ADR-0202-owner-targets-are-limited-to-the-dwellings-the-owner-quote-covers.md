# ADR-0202 · 2026-11-19 · Owner targets are limited to the dwellings the owner quote covers (issue #657)

- **Status:** accepted (implemented on branch `fix/i657-popsim-tenure-dwellings`; key switched on by owner decision 2026-11-19 with a documented deviation from one pre-registered check, see "Evidence").
- **Numbering:** ADR-0202. The highest id on any local or remote branch on 2026-11-18 was ADR-0200; ADR-0201 is left to the pending decision record of issue #646, which its work notes reserve.
- **Issue:** #657. Pattern: [ADR-0198](ADR-0198-seed-records-are-resolved-into-the-census-control-categories-and-building-type-targets-count-households.md) (a dwelling-based census figure turned into a household target in the control build, mirrored in the control-fit validation).

## Context

- The tenure controls of `data.census.filtered` (`EigentuemerHH_Tenure_100m_Gitter`, `MieterHH_Tenure_100m_Gitter`) come from the census preparation upstream, which applies the Zensus owner quote of a cell (or of its 1 km square) to ALL households of the cell.
- The owner quote is defined on occupied dwellings in residential buildings, without dormitories and homes (Destatis, "Datensatzbeschreibung zur Tabelle 'Eigentümerquote in Gitterzellen'", 21.02.2025, sheet "Merkmalsdefinitionen", accessed 2026-11-18). Households outside such dwellings therefore inherit a quote that does not describe them.
- In batch_000 four 100 m cells asked for all their households as owner households, as one-person households and as building type "other" at the same time, a profile the survey seed barely offers. Their 1 km square carried the whole household excess of the batch, every solver-path defect and the G4 miss of ladder arm 3 (figures: [`popsim-ladder-arm3-2026-11-18`](../runs/popsim-ladder-arm3-2026-11-18.yml), last validation entry). Reading these cells as dormitories is an INTERPRETATION of their grid values (owners only, one building type); that record does not state it.

## Decision

1. **Rule** ([`braunschweig.popsim.tenure_targets.limit_owner_households_to_dwellings`](../../braunschweig/popsim/tenure_targets.py), formula in its module docstring): in a 100 m cell with at least the threshold more households than dwellings, the owner target keeps its owner share but is limited to the dwellings of the cell; the renter target is the rest of the households. All other cells are unchanged. The rule runs in the stage after the building-type rescale of ADR-0198 and before the integerisation within the 1 km squares; the 1 km tenure targets follow as sums.
2. **Dwelling count:** the rooms table, harmonised like the household total (`control_spec.OWNER_QUOTE_DWELLING_COLUMN`). It counts all dwellings in buildings with living space (Destatis, "Datensatzbeschreibung zur Tabelle 'Wohnungen nach Zahl der Räume in Gitterzellen'", version of 08.07.2025, accessed 2026-11-18), so it holds at least the dwellings of the quote. Households beyond it are outside every dwelling of the quote.
3. **Threshold:** the key `braunschweig.population.popsim.tenure_min_households_outside_dwellings` (value: `configs/base_bs.yml`), so that small cells with suppressed or perturbed dwelling counts are left as they are.
4. **Keys:** `braunschweig.population.popsim.tenure_owner_limited_to_dwellings` and the threshold; defaults, meaning and valid range in `braunschweig/popsim/stage/config_keys.py`, the production state in `configs/base_bs.yml`. They are not part of the batch config signature: a changed tenure column changes the batch's input fingerprint, so exactly the affected batches run again (ADR-0193).
5. **Report:** the stage writes `census_filtered_report.json` on every run, with the counts of the ZGB cell filter, the building-type rescale of ADR-0198 and this rule (status `disabled`, `no_tenure_control` or `applied`), and logs NaN dwelling or tenure counts as fallback rates.
6. **Validation mirror:** `braunschweig.analysis.popsim_validation.controls.tenure_target` applies the same function before it aggregates the tenure target, and `analysis_suite` passes both keys, so the control-fit validation compares against the same targets the stage built.

## Assumptions

- ASSUMPTION: households beyond the dwellings of the rooms table (dormitories, homes and similar) rent. No table of the census grid gives their tenure.
- ASSUMPTION: the threshold separates such cells from suppression noise; the measured sensitivity to other thresholds is in the run record.
- Limitation: the rooms table is a superset of the quote's dwellings (it adds vacant, holiday and commercially used dwellings and dwellings in non-residential buildings), so the rule corrects too little rather than too much. A tighter count exists (occupied dwellings of the vacancy table); it flags more cells, mostly cells whose excess comes from the vacancy figure rather than from households outside dwellings (measurement of both in the run record).

## Rejected options

- **The dwelling columns of the building-type table as the count:** they include dormitory flats partly inside their residential classes and flag mixed-use city cells; on the real cells they miss part of the high-owner cells the rooms table catches (scratch analysis of 2026-11-18, not committed; owner decision taken with it).
- **A rule on the share of "other building type" dwellings:** it describes the building, not whether the households live in dwellings of the quote.
- **Lowering the importance of tenure in such cells:** hides the contradiction instead of repairing it.
- **Repairing the census preparation upstream first:** cleaner (one corrected value for every user and both grid levels), but it needs a new prepared grid and data version before anything can be tested; owner decision 2026-11-18: repair here first. An upstream issue is proposed, not filed.

## Consequences

- The tenure targets of the cells the rule selects change; all other control columns and all other cells stay as they were (golden tables `tests/data/popsim_input_consistency/`, ladder arm 4 preparation logs).
- Every stage downstream of PopulationSim recomputes for the affected batches.
- If the census preparation is repaired upstream, the step reports no changed cell and stays as a check.

## Evidence

- Run record [`popsim-ladder-tenure-2026-11-18`](../runs/popsim-ladder-tenure-2026-11-18.yml): measurement of the rule on all model cells, ladder arm 4 on batch_000 and batch_032 with pre-registered checks, results and the owner decision. One pre-registered check is missed (the pooled tenure fit of batch_000, by a small margin stated there); the owner switched the key on with this deviation documented. The owner's reason for overriding the missed check (work session 2026-11-19): the two arms are fitted against different tenure targets (arm 4 against the corrected ones), and the miss is a tiny fraction of the pooled bound. The purpose of the repair (household excess, solver-path defects, G4 of batch_000) is met in the record; bound and result stay in the run record. Two batches and one draw; the full population is the full rerun (#648).
