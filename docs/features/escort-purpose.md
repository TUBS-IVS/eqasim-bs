# Escort activity purpose

Issue #201 (analog eqasim-france #495). Escort (MiD "Bringen/Holen/Begleiten",
W_ZWECK {6, 13}) is a dedicated plan-level activity purpose behind
`escort_purpose` (code default OFF = byte-identical; ON in configs/base_bs.yml).

## Data sources
- Donor extraction: MiD 2023 Wege, `ESCORT_W_ZWECK = {6, 13}`
  (braunschweig/popsim/trips.py; code 13 verified escort via MiD's own `zweck`
  and `hwzweck1` derivations, 2026-07-24).
- Location-type weights: SrV 2023 BS+RGB `V_ZWECK_BHOL` ("wohin gebracht/
  geholt"), GEWICHT_W-weighted, 98.8 % coverage --
  scripts/derive_escort_location_weights.py writes the pinned reference
  eqasim-data/data/braunschweig/srv/srv2023_escort_destination_types.csv.
  ASSUMPTION: work-type destinations fold into "other" (no work facilities in
  the secondary candidate universe).
- Validation references: mid2023_W1.csv `begleitung` (8.0 % ZGB) and
  mid2023_W12_triplength_by_purpose.csv `Begleitung` (10.1 km mittel).

## Mechanics
1. Purpose override in `map_purpose` (flag-gated); the `escort` distance layer
   emerges automatically in the by-purpose secondary distance distributions.
2. The chainsolver draws ONE location type per escort leg (dedicated RNG,
   `ESCORT_LOCATION_SEED_OFFSET`) over `escort_locations_activities` x
   `escort_locations_weights`: education by school type (Kita/Schule/Hochschule,
   candidates from `synthesis.locations.education`, potential = OSM capacity
   proxy), residential (leisure_visit building machinery), shop/leisure/other
   (existing candidates). All escort legs sample the single `escort` distance
   layer; fallbacks are rate-logged. With `escort_distance_by_type` (A3) each
   drawn type samples its own distance layer: the MiD escort layer scaled by the
   SrV between-type factor (`srv2023_escort_distance_factors.csv`; thin
   categories neutralized to 1.0); fallbacks are two-level and rate-logged
   (type -> escort -> other).
3. Phase 2 (`escort_household_link`): escorters with a child (<=
   `escort_household_link_max_child_age_years`) that has a realised education
   location get ALL their escort activities anchored at that school
   (`escort_linked` fixed purpose inside the chainsolver stage only); the link
   rate is logged; unlinked escorters keep the draw. Requires `escort_purpose`
   ON (the stage raises otherwise). The anchored activities reference PRIMARY
   education facility ids, which the facilities coverage check accepts via
   `validate_secondary_coverage(extra_valid_ids=...)`.
4. Facilities advertise `escort`; eqasim-java-bs registers the inert `escort`
   ActivityParams (documented OFF-path exception: config.xml gains one unused
   entry).

## Validation
The 10 % validation report (`scripts/validate_bs_10pct`) scores the trip-purpose
mix against MiD 2023 W1 presence-based: on a flag-ON population Begleitung
(8/99) is scored as its own `escort` row; on a flag-OFF population the escort
share is folded back into `other` (19/99) so the comparison stays
apples-to-apples (`metrics.purpose_mix_w1_baseline`, mirroring
`trip_coherence.scored_mid_purposes`).

Escort length-band fit vs MiD W12 is the A3 acceptance metric (baseline
2026-08-11: <2 km = 25.6%, band L1 = 27.8 pp).

## Follow-ups
#241 (MiD W_ZWECK 14-16/99 mapping gap), #242 (SrV subtype re-validation).
#243 was folded into this feature (education-type split).
