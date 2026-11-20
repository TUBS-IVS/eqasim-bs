# ADR-0204 · 2026-11-19 · Household targets use the universes and definitions the seed can match (issue #659)

- **Status:** accepted (implemented on branch `fix/i659-popsim-household-universes`; `family_controls` switched on after its arm met every pre-registered check; the move of household-less persons switched on by owner decision 2026-11-19 with one pre-registered no-harm check not met, see "Evidence").
- **Numbering:** ADR-0204. Highest id on any local or remote branch on 2026-11-19: ADR-0203 (issue #658); ADR-0201 stays reserved for the pending decision record of issue #646.
- **Issue:** #659. Follows [ADR-0203](ADR-0203-kreis-controls-of-a-batch-follow-their-own-universe-and-the-finer-level-controls.md) (KREIS consistency, #658) and [ADR-0202](ADR-0202-owner-targets-are-limited-to-the-dwellings-the-owner-quote-covers.md) (tenure, #657) in the same repair series; Tier-1 household controls: [ADR-0059](ADR-0059-large-hh-6-validation-gap-is-donor-bound-not-weight-fixable.md) for the seed's six-member cap.

## Context

- The four Tier-1 family-type controls (`Typ_priv_HH_Familie`, 100 m) count a child of ANY age; the seed class `hh_type5` (`seed.derive_hh_type5`) can only see members under 18, because MiD 2023 has no kinship variable. Balancing the census classes with the seed's classes produced too many households with a minor, each with too few minors (arm-6 baseline in [`popsim-ladder-inputs-b-2026-11-19`](../runs/popsim-ladder-inputs-b-2026-11-19.yml)).
- The census also publishes the family type with the under-18 split at 1 km (`Typ_der_Kernfamilie_nach_Kindern`: couples and lone parents with at least one child under 18).
- Some 100 m cells have residents but a published household count of 0, most likely suppressed household tables read as 0 (data record `zensus2022_grid_cells`). PopulationSim drops a sub-zone without households but keeps its persons in the parent totals, so these persons cannot be placed in their cell.

## Decision

1. **Family controls by the under-18 definition, D-fam** (`braunschweig.population.popsim.family_controls: "minor_child_1km"`): the four 100 m family-type controls are replaced by two 1 km household controls, households with a member under 18 and exactly one adult, and with a member under 18 and two or more adults. Targets: the cell's household total times its 1 km square's share of lone parents / couples with a child under 18 (family table) among its private households (household-type table; method: the module docstring) (`braunschweig.popsim.minor_child_targets.per_cell_targets`); seed class `hh_minor_class` (`seed.derive_hh_minor_class`), on the PopulationSim seed only in this mode. A square without family-table households takes its Kreis's share (counted, raises above a rate). The mode `"none"` (no family control) was measured and not chosen.
2. **Persons of household-less cells, D-cells** (`move_persons_of_household_less_cells: true`, `household_less_cell_max_persons: 20`): before integerisation, the persons (22 age x sex bands, ten employed-grid columns) of a 100 m cell with 0 integer households and fewer than 20 persons move to the household cells of the same 1 km square in proportion to their integer households (`braunschweig.popsim.household_less_cells`, option of `mid.control_cells.build_control_totals`). Cells with 20 or more persons and donors in a square without any household cell stay and are counted. 1 km, KREIS and batch sums are unchanged by construction: the person columns are integerised to their rounded pre-move 1 km totals (reported as `max_parent_sum_change`, 0).
3. **Report and measurement:** both steps write their counts to `census_filtered_report.json` (sections `family_controls`, `household_less_cells`); the batch diagnostics write `household_composition.csv` against the census family table (`braunschweig.analysis.popsim_batch_diagnostics.composition`).
4. **Keys:** meaning and valid values in `braunschweig/popsim/stage/config_keys.py`; code defaults are the legacy values, which reproduce the control tables and the seed of the base commit; the keys enter the batch config signature only when off legacy.

## Consequences

- The income placement after PopulationSim swaps household slots only between donors with equal control signatures; those signatures are now built from the controls PopulationSim enforced, with the same family mode and the same 1 km ownership-grid flag (`braunschweig.popsim.stage._placement_income_catalog`). With `"minor_child_1km"` this changes the placement's production result (the swaps keep the two under-18 controls instead of the dropped 100 m family types). Adding the ownership grid leaves the result unchanged: its controls select the same households as the KREIS car/bicycle controls already in the signatures, which the grid requires to be active.

## Assumptions

- ASSUMPTION (D-fam): one family with a child under 18 is one household with a member under 18, and a lone parent is the one adult of that household. Blind spots: a household with two families, minors living without a parent, a lone parent living with a further adult (a two-adult household in the seed; one-sided).
- ASSUMPTION (D-cells): the households of a small populated cell with a published household count of 0 are suppressed and counted in the neighbouring cells of the same 1 km square by the harmonisation; cells with 20 or more such persons are more likely institutions and stay. The threshold 20 is a configurable judgement. A cell whose positive published households round to 0 within its square is treated like a suppressed one (counted separately in the report).
- ASSUMPTION: where a square's families with a minor exceed its family-table households, the two shares are scaled to sum 1 (counted).

## Rejected options

- An age-gap rule that imitates the census child concept in the seed (an adult household member far younger than the others counted as a child): it needs a relationship MiD does not observe.
- Keeping the four census family-type controls: measured distortion (arm-6 baseline of the run record).
- Dropping the family controls without a replacement (`"none"`): its arm missed the pre-registered composition check (too few one-adult households with a minor) and household conservation in batch_000.
- Moving the persons of every household-less cell, institutions included, or removing them from the person controls: changes who is in the model and needs an official count of the population outside private households (follow-up, not decided here).

## Considered afterwards and rejected (issue #660)

- Owner decision 2026-11-19: two ways of handing the census targets to PopulationSim were measured on top of this ADR's state and are NOT adopted: the 22 age x sex controls at the 1 km level instead of 100 m, and unrounded census targets per 100 m cell (household size, tenure, building type, age x sex). Neither met the pre-registered recommendation rule of the run record [`popsim-ladder-inputs-c-2026-11-19`](../runs/popsim-ladder-inputs-c-2026-11-19.yml), which holds the measures, the rule and the results. The controls keep their levels and their rounding; the age x sex bands stay at 100 m as [ADR-0088](ADR-0088-fine-teen-age-bands-in-the-tier0-backbone.md) decided. A variant with coarse age groups per 100 m cell and the fine bands at 1 km was proposed after the results and withdrawn without a run: it is a milder form of the layered design ADR-0088 rejected.

## Evidence

- Pre-registered run record [`popsim-ladder-inputs-b-2026-11-19`](../runs/popsim-ladder-inputs-b-2026-11-19.yml) (four batches, one draw): the `"minor_child_1km"` arm meets every check; its family-composition check (O-fam) is control fit that is not independent of the targets (target = households x the census share the check uses), not a behavioural validation; the `"none"` arm does not. The move arm meets its own checks and every no-harm check except household conservation in batch_000 at 100 m, where the deviation grows by one household. Owner decision 2026-11-19: switch the move on with this one pre-registered no-harm check not met; the bound and the result stay as recorded. Part A located the batch_000 conservation loss in one 1 km square whose 100 m targets ask for more households than persons ([`popsim-ladder-inputs-a-2026-11-18`](../runs/popsim-ladder-inputs-a-2026-11-18.yml), post hoc entry); the move keeps square sums and cannot remove such a contradiction. Whether the extra household of the move arm comes from that square is not measured.
- The record ran part of its arms with the run-time options of ADR-0200, with an identity check (execution notes of the record).

## Open

- Persons-vs-household-size noise per 100 m cell, institutional residents, and cells that ask for more households than persons stay (measured in the run records of #658; not repaired here). Whether that noise limits the 100 m fit is compared, post hoc and observationally, in the run record of #660 (fit of consistent and contradictory cells by cell size); the cause of the shortfall of persons is not known.
- The six-member cap of the seed (ADR-0059, #278).
- The synthetic population does not carry `hh_minor_class` (`assembly.py` unchanged); the control-fit validation derives it from the members' ages (`popsim_validation.controls`, row `household_minor`) and keeps `household_type` as a reference row only.
- The full rerun (#648) is the region-wide evidence.
