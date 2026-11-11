# ADR-0192 · 2026-11-11 · Donor household counts and person attributes with imputed codes are resolved once per donor and inherited by the clones (repository audit P3-F1, P3-F4)

- **Status:** active
- **Context:** Under `popsim_mid` a donor's MiD item-nonresponse codes were imputed TWICE with
  different random streams: once on the PopulationSim seed (`kreis_seed_rng`, only when the
  matching Kreis control is active) and again per expanded clone in
  `braunschweig.popsim.assembly.map_mid_person_attributes` (assembly rng, RegioStaR7 of the
  PLACED home cell). A clone could therefore differ from the seed person the balancer had
  counted. For household counts (cars, bicycles, e-bike) this was closed first by
  `braunschweig.popsim.stage._resolve_donor_household_counts`. This record covers the person
  attributes `employment_status` (from `P_BKAT`) and `pt_subscription_type` (from `P_FKARTE`;
  `has_pt_subscription` stays derived from it, ADR-0087). `map_employed` (`P_TAET`) and
  `map_has_license` are NOT part of this decision.
- **Measured exposure:** over all 420,979 persons of the MiD 2023 B1 persons file
  (MiD2023_Personen.csv), unfiltered, counted on 2026-11-11 with
  `python scripts/count_mid_person_nonresponse.py --persons-csv <path to that file>`:
  `P_BKAT` code 9 is 200 persons (0.05 %); the imputed `P_FKARTE` codes 99 (990), 202 (0) and 206
  (24,568) are 25,558 persons (6.07 %). No effect size on model results is claimed: no run
  exists for this change.
- **Decision:** (owner decision for persons, audit P3-F1: clones inherit the seed-resolved value)
  1. `braunschweig.popsim.attributes.resolve_person_attributes` resolves `employment_status` and
     `pt_subscription_type` ONCE on the completed donor persons, always (independent of which
     Kreis controls are active), with the seeded `kreis_seed_rng`, in the popsim stage next to
     the household-count resolution. RegioStaR7 is a household column, so the donor
     household's value is joined onto a working copy for the imputation pools only (the
     persons frame does not keep it; its RegioStaR7 is the placed home's downstream).
  2. The seed derivations (`_derive_employment_status_seed_column`,
     `_derive_pt_ticket_group_seed_column`) reuse the resolved columns when present.
  3. `expand_to_persons` copies the donor person columns onto every clone, so the donor person
     key is the completed-donor `(H_ID, P_ID)` (member-completion fillers carry their own
     `P_ID`); `map_mid_person_attributes` uses the columns verbatim. If absent (direct callers,
     legacy `complete_members=False` path) it resolves per clone as before and logs a WARNING.
- **Household decision (audit P3-F1, P3-F4):** `number_of_cars`, `number_of_bicycles` and
  `has_ebike` are resolved once on the donor household table (`resolve_household_counts`)
  and reused verbatim by the seed and by the clones. Bicycles and e-bikes are imputed jointly
  from one donor household draw (`map_household_bicycles_and_ebike`), restricted to
  consistent donors (pedelecs <= bicycles), so an e-bike never appears without a bicycle.
  The legacy path (`complete_members=False`) carries the seed counts onto its separately
  loaded donor table by `H_ID` (`carry_seed_household_counts`, fallback rate logged). Known
  limit there: if only one of the two bicycle controls is active, the seed carries only that
  column and the other comes from an independent joint draw, so an e-bike can meet zero
  bicycles; such donors are counted and logged as a WARNING (no config uses this path).
- **Rejected alternative (B):** keep the per-clone draw but restrict it to the seed's control
  group. It still spends a second draw, would not reproduce the seed person's exact category
  and would need the seed group to travel with the clone; the single draw is simpler and exact.
- **Consequences:** imputed persons are now conditioned on the DONOR's survey RegioStaR7, not on
  the placed home's RegioStaR7: the issue #131 conditioning is lost only for the imputed cases;
  valid codes are unaffected. The rng stream shifts, so the synthetic population is not
  byte-identical to earlier runs. On the legacy path (`complete_members=False`) the persons are
  still resolved separately for seed and donor table (open).
  Regression test: `tests/test_popsim_household_counts_resolved_once.py`.
