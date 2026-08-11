# Escort activity purpose

Issue #201 (analog eqasim-france #495). Escort (MiD "Bringen/Holen/Begleiten",
W_ZWECK {6, 13}) is a dedicated plan-level activity purpose behind
`escort_purpose` (code default OFF = byte-identical; ON in configs/base_bs.yml).

## Data sources
- Donor extraction: MiD 2023 Wege, `ESCORT_W_ZWECK = {6, 13}`
  (braunschweig/popsim/trips.py; code 13 verified escort via MiD's own `zweck`
  and `hwzweck1` derivations, 2026-07-24). Code 6 = active escort (Bringen/Holen);
  code 13 = passive escort, the escorted child's own leg (100% minors, issue #256).
- Location-type weights: SrV 2023 BS+RGB `V_ZWECK_BHOL` ("wohin gebracht/
  geholt"), GEWICHT_W-weighted, 98.8 % coverage --
  scripts/derive_escort_location_weights.py writes the pinned reference
  eqasim-data/data/braunschweig/srv/srv2023_escort_destination_types.csv.
  ASSUMPTION: work-type destinations fold into "other" (no work facilities in
  the secondary candidate universe).
- Distance-by-type factors (A3): SrV 2023 BS+RGB `V_ZWECK == 12`, length =
  `GIS_LAENGE_GUELTIG` (valid-only GIS route km; -7 sentinel = invalid;
  validity = value > 0), GEWICHT_W-weighted, GIS coverage 82.45 % of valid
  escort legs (n_valid=2602); min_obs=30 neutralizes edu_university/shop --
  scripts/derive_escort_location_weights.py writes the pinned reference
  eqasim-data/data/braunschweig/srv/srv2023_escort_distance_factors.csv.
  Coherence gate vs MiD W_ZWECK==6 wegkm_imp: PASS (band L1 9.29 pp, median
  ratio 0.929).
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
   (existing candidates). Without `escort_distance_by_type`, all escort legs
   sample the single `escort` distance layer (fallbacks rate-logged); with it ON
   (code default OFF, `true` in configs/base_bs.yml) each drawn type samples its
   own distance layer: the MiD escort layer scaled by the SrV between-type
   factor (`srv2023_escort_distance_factors.csv`; thin categories neutralized to
   1.0); fallbacks are two-level and rate-logged (type -> escort -> other).

   ASSUMPTION (A3): the between-type length ratios are treated as invariant
   across mode x travel-time strata (SrV provides only marginal medians).
   Because the type draw is independent of the leg's mode/travel time, the
   expected escort distance level shifts by sum(w_c x factor_c) = 1.0305
   (+3.1%) under the pinned draw weights and factors -- a known, accepted
   by-construction drift (well inside the +-20% mean criterion); the 5%
   validation run reports the realised shift.
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
5. Passive education (`escort_passive_education`, issue #256): MiD code 13
   legs (the escorted child's own leg, 100% minors) are relabeled from `escort`
   to the child's own education activity (using their realised education
   facility), anchoring the child's trip to their assigned Kita/school location.
   Active Bringen/Holen (code 6) remain `escort` with the drawn location. The
   mechanism applies `trip_coherence`'s logic: code-13 legs inherit the
   chainsolver's education-type anchor selection. Requires `escort_purpose` ON
   (the stage raises otherwise). The relabel rate and distance distribution
   shift (child's own trip vs escorter's view) are logged.

## Validation
The 10 % validation report (`scripts/validate_bs_10pct`) scores the trip-purpose
mix against MiD 2023 W1 presence-based: on a flag-ON population Begleitung
(8/99) is scored with active escort (code 6) in the `escort` row and passive
code-13 legs in their education subtype (e.g., `edu_kindergarten`); on a
flag-OFF population the escort share is folded back into `other` (19/99) so the
comparison stays apples-to-apples (`metrics.purpose_mix_w1_baseline`, mirroring
`trip_coherence.scored_mid_purposes`). The validation report is dual-labelled:
overall metrics, then code-6-only (active) vs code-13 (passive) submetrics.

Escort length-band fit vs MiD W12 is the A3 acceptance metric (baseline
2026-08-11, run output_bs_5pct_escort: <2 km = 25.6%, band L1 = 27.8 pp).
When `escort_passive_education` is ON, W12 active-only (code 6, parent's view)
is used as the active-escort reference; code 13 (child's own trip) distances
are validated against education-specific references.

## Follow-ups
#241 (MiD W_ZWECK 14-16/99 mapping gap), #242 (SrV subtype re-validation).
#243 was folded into this feature (education-type split).
