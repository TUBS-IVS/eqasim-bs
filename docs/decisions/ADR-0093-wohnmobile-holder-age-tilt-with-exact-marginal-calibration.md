# ADR-0093 · 2026-08-20 · Wohnmobile segment tilted by holder age, with an exact-marginal calibration scalar

- **Status:** active
- **Context:** The per-car segment draw `P(segment | economic_status, raumtyp)`
  (`braunschweig.synthesis.vehicles.segment.SegmentModel`) has no holder-age
  dimension, so motorhomes could land with implausibly young households. The KBA
  publishes the complete wohnmobile holder-age distribution (Stichtag
  2025-04-01, total 1,002,562; infographic "Eine Million Wohnmobile" and PM
  23/2025, identical figures, verified 2026-08-20) -- a citable full-register
  reference for exactly one segment. Issue #315; design spec
  `docs/superpowers/specs/2026-08-18-wohnmobile-holder-age-tilt-design.md`.
- **Decision:** Tilt only the `wohnmobile` component of the per-car segment pmf
  by the ASSIGNED OWNER's age class `a` (the licensed adult the car is assigned
  to -- the closest analogue of the KBA "Halter"):
  `p_wm' = base_wm * c * r(a)` with `r(a) = P_ref(a | wohnmobile) / P_pop(a)`,
  `P_pop` the holder-age distribution of the sampled car frame itself, and the
  non-wohnmobile mass rescaled proportionally. Implemented in
  `braunschweig.synthesis.vehicles.wohnmobile_age.WohnmobileHolderAgeTilt`,
  applied in `sample_fleet` PASS 1 (consistency_v2 only), flag
  `fleet_wohnmobile_age_tilt` (default true).
  - **Plain Bayes does NOT preserve the marginal by itself** (issue #315's
    "preserved by construction" is too strong): `E_pop[r] = 1`, but `base_wm`
    varies with (status, raumtyp) and holder age correlates with both, leaving
    a residual of exactly `Cov_cars(base_wm, r)`. A single global calibration
    scalar `c = E_cars[base_wm] / E_cars[base_wm * r]`, fitted per frame,
    removes it: the expected national wohnmobile share is preserved EXACTLY,
    while the renormalised holder-age COMPOSITION is mathematically invariant
    to `c` (a scalar cancels), so the calibration spends no age signal.
  - **No clipping band on `r`** (contrast the EV-income tilt's [0.2, 5.0]): the
    reference is a full register count, not a sparse survey cross-tab; the
    extreme low ratio of the youngest class is real signal, and a clip would
    break `E_pop[r] = 1`. Numerical guards only; guard hits are counted and
    logged next to `c` because each one breaks the exactness claim for that car.
  - **Denominator:** no open national `P(age class | any car)` table exists
    (FZ 23 has holder groups without age; the KBA Halter page only a
    Pkw-density-per-age PDF), so `P_pop` comes from the synthetic frame. The
    validated quantity is therefore the holder-age COMPOSITION of motorhomes,
    `P(a | wohnmobile)` (±2 pp band, MC-floored), plus the preserved aggregate
    (4-sigma MC band) -- see
    `braunschweig.synthesis.vehicles.fleet_validation.validate_wohnmobile_holder_age`.
  - **ASSUMPTION (renormalisation):** the eight published classes cover
    963,086 of 1,002,562 vehicles (96.06%); the residual 39,476 (3.94%) is
    attributed to no age class on either source page, recorded as
    `not_attributed`, and NEVER asserted to be commercial. `P_ref` renormalises
    over the eight classes, i.e. assumes the residual shares their composition.
  - **Absent committed input raises:** unlike the server-generated MiD tables,
    `kba_wohnmobile_holder_age.csv` is committed; with the flag ON its absence
    raises instead of silently disabling (the absent-input-hides-features
    failure class). The raw hand-transcription is committed too (no
    downloadable table exists behind the KBA pages).
- **Rejected:**
  - Per-(status, raumtyp)-cell normalisation of `r`: also fixes the aggregate
    but erases the age signal confounded with status/raumtyp; the global scalar
    achieves exactness with one degree of freedom. Revisit ONLY if the
    composition band is violated.
  - Only measuring the covariance drift (the 2026-08-18 draft): made the
    headline property a tolerance instead of a construction.
  - National denominator from the Pkw-density PDF: second vintage + second
    geography for a 1.99% segment.
  - A 3D segment x status x age rake: the reference covers ONE segment's age
    profile; the other segments' age dimension would be invented.
  - Clipping `r` to [0.2, 5.0]: see above.
- **Explicit non-claims:** vehicle age is NOT coupled to holder age (motorhomes
  keep drawing age from the status pool; the vehicle-age gap in
  `docs/registry/data/kba_fleet_derived.yml` stays open, measured bound
  +0.06/+0.11/+0.17 yr fleet-mean for a 3/5/8 yr true offset). Any indirect
  motorhome-age improvement via status composition is measured, never claimed.
  Reproducing the holder-age composition is NOT a validation of motorhome
  ownership against local observed data; no ZGB reference exists.
- **Consequences:** `sample_fleet` gains the flag + an `owner_age` input column
  (household frame: assigned owner's age; in-commuters: donor age with the
  pre-existing constant-40 substitution). One more committed derived table
  (19). The sonstige-redistribution redraw leaks an age-independent,
  second-order wohnmobile mass past the tilt; the aggregate flag in
  `validate_wohnmobile_holder_age` compares the realised share against the
  EFFECTIVE (redistribution-inclusive) expectation actually fed into the draw,
  so this leak can no longer bias that flag itself, while the unflagged
  `dev_untilted_pp` reported alongside it carries the leak and quantifies the
  tilt's neutrality against the untilted baseline.
