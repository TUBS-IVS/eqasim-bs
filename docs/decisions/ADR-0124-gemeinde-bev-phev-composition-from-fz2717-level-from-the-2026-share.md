# ADR-0124 · 2026-09-16 · The per-Gemeinde BEV:PHEV composition comes from FZ 27.17 while the level stays with the 2026 combined share (issue #317)

- **Status:** active
- **Context:** ADR-0086 had to tilt BOTH electric powertrains by ONE factor,
  because the 2026.04 edition of the KBA per-Gemeinde EV export publishes a
  literal `0` in every BEV / plug-in-hybrid / fuel-cell column and only the
  combined `Pkw Elektro Anteil` carries information. That decision kept the
  *level* signal the source actually publishes (WHERE electric cars are) and
  refused to invent a split. The cost it accepted was the *structure* signal:
  within a Kreis, every Gemeinde received the Kreis's BEV:PHEV ratio, so the
  model could not say where BEV rather than PHEV dominates.
- **The data situation (measured, not assumed):** the older FZ 27.17 private-car
  table `kba_gemeinde_private_bev.csv` (Stichtag 2025-01-01) still carries both
  counts per Gemeinde. Measured on the committed table:
  * **105 of 113** ZGB Gemeinden carry a usable BEV:PHEV ratio (both counts
    present, positive electric stock);
  * the BEV fraction of the electric stock ranges **0.598 .. 1.000**, IQR
    **0.722 .. 0.804**, median **0.748** — real spatial signal, not noise;
  * the 8 without a usable ratio are **not** the set issue #317 described. Six
    have a suppressed PHEV count (Beierstedt, Jerxheim, Querenhorst in Kreis
    Helmstedt; Dahlum, Uehrde, Winnigstedt in Kreis Wolfenbüttel) and **two have
    a suppressed BEV count: Wolfsburg (03103) and Hedeper**. Wolfsburg is a
    kreisfreie Stadt and therefore the only Gemeinde of its Kreis, where the
    composition factor is 1.0 by construction anyway, so the only substantive
    loss is Hedeper plus the six.
  * Dorstadt and Roklum, which issue #317 counted among the eight, in fact carry
    a **measured zero** PHEV count and therefore a usable composition
    (`bev_frac = 1.0`).
- **Decision:**
  1. **Level from the 2026 combined share, structure from FZ 27.17**, both as
     relative factors, so the vintage gap only ever affects a ratio of ratios:
     `f_bev(g) = bev_frac(g) / bev_frac(kreis)` and
     `f_phev(g) = (1 - bev_frac(g)) / (1 - bev_frac(kreis))`.
  2. **The Kreis reference is weighted by each Gemeinde's ELECTRIC stock, not by
     its total car stock.** Summing the raw `private_bev` / `private_phev`
     counts IS that weighting, and only under it do the factors average to
     exactly 1.0 across a Kreis. This is load-bearing rather than cosmetic: the
     ADR-0085 post-mask rake targets the **tilted** mean pmf, so a composition
     whose Kreis mean were 1.02 would shift the per-Kreis BEV aggregate and the
     rake would then *preserve* the shift instead of correcting it.
  3. **The counts are used directly**, not reconstructed as
     `private_bev_share * private_total`; the committed table agrees with itself
     to within 1e-16, so the reconstruction step buys nothing.
  4. **Clip, then renormalise.** Factors are clipped to the same
     `GEMEINDE_TILT_CLIP` band `(0.2, 5.0)` the combined and grid tilts use, and
     each Kreis is then renormalised so the weighted mean returns to exactly
     1.0. The band matters only for a Gemeinde whose PHEV count is a measured
     zero: ADR-0086 decision 1 keeps such a zero as a measurement "which the
     documented 0.2 clip floor then handles", so it is floored rather than
     applied as a hard 0 that would wipe PHEV out of a ~14-car electric stock.
     Clipping otherwise injects mass the rake would lock in; the renormalisation
     ratio is logged and warns above 1 %.
  5. **The composition is rescaled per Gemeinde so the electric TOTAL is
     untouched.** This is the correction issue #317's design did not contain.
     The factors average to 1.0 under the FZ 27.17 *private* weighting, but they
     are applied to a pmf whose BEV:PHEV split comes from **46251-02 (all
     ownership)**. Where the two disagree, a raw multiplicative pair changes how
     much electric mass a Gemeinde has — the composition would leak into the
     level that is the combined share's job. The rescale
     `scale = (b0+p0) / (b0*f_bev + p0*f_phev)` removes that leak by
     construction, independently of any agreement between the two sources, and
     cancels against the combined factor so the level stays exactly where the
     combined tilt put it.
  6. **The composition applies only inside the combined-share branch.** If a
     future KBA edition restores the per-Gemeinde BEV/PHEV columns, that path
     takes precedence and already carries the structure; applying the
     composition again would double-count it.
  7. **Gemeinden without a usable ratio keep factor 1.0**, are excluded from the
     Kreis reference (including them would skew the mean the others are
     normalised against), and are counted, rate-logged and named, per the
     no-silent-fallback rule.
  8. **The aggregate is neutralised on the REALISED population, not on the FZ
     reference stock** — added after the PR review caught this. Decision 2
     normalises the factors against the FZ 27.17 electric stock, but the ADR-0085
     rake targets the **unweighted mean of the actual cars'** unmasked pmfs, and
     nothing constrains a synthetic population's cars-per-Gemeinde to follow the
     FZ stock. Where the two distributions differ, the composition moves the
     per-Kreis BEV:PHEV aggregate and the rake then PRESERVES that shift.
     `sample_fleet` therefore inverts the composition back out of each car's
     electric split (exact: the per-car rescale is common to both components and
     cancels in the ratio), measures the Kreis BEV mass with and without it, and
     shifts every car's BEV FRACTION by the single constant that closes the gap.
     A constant additive shift in the fraction preserves each car's electric
     TOTAL exactly and preserves the within-Kreis ordering, so the structure
     survives while the aggregate becomes exact on the population that actually
     exists. The shift is logged per Kreis; its `[0,1]` clip should never bind and
     warns if it does. Decision 2's normalisation is retained — it keeps the
     factors centred near 1.0 so the clip band stays meaningful — but it is no
     longer what guarantees the aggregate.
- **Rationale:** the alternative rejected up front in issue #317 — splitting the
  combined share with a national or per-Kreis BEV:PHEV ratio — is arithmetically
  trivial and scientifically indefensible, because it would present a modelled
  split as a per-Gemeinde measurement (CLAUDE.md, "no invented reference
  values"). FZ 27.17 is a year older and covers private cars only, but it is a
  *measurement* of exactly the quantity in question, and entering as a relative
  factor on top of a relative level factor limits the vintage exposure to a
  ratio of ratios.
- **What is guaranteed, and what is not:** two things are exact by construction —
  each car's electric TOTAL (decision 5) and, since decision 8, the per-Kreis
  BEV:PHEV aggregate on the drawn population. What stays approximate is the
  per-Gemeinde *magnitude* of the structure signal: the factors come from the FZ
  27.17 PRIVATE split but act on a pmf whose split comes from 46251-02 (ALL
  ownership). Measured on the committed tables that scope gap is **0.88 .. 5.04 pp**
  in the BEV fraction, so a Gemeinde's realised deviation is damped or amplified
  slightly against the reference; decision 8 removes the gap's effect on the Kreis
  TOTAL but cannot make a private-scope measurement describe an all-ownership
  fleet. Before decision 8 the uncorrected aggregate residual measured **at most
  0.0224 pp** of the all-car fleet (Kreis 03154; ZGB aggregate 0.0024 pp) against
  a BEV share of ~3–4 % — that is the magnitude decision 8 now removes, and it is
  what `scripts/measure_gemeinde_bev_composition.py` reports as the UNCORRECTED
  baseline. The electric TOTAL drift is **4.9e-17**, i.e. machine zero, as
  decision 5 guarantees. None of these are calibration
  targets. Note that the residual must be measured **segment-weighted**: the
  per-segment BEV:PHEV splits are far more dispersed than the Kreis aggregate
  (`minis` is ~100 % BEV, `gelaendewagen` ~33 %) and a Kreis-level approximation
  understates the worst case by about an order of magnitude (0.0026 vs 0.0224
  pp) — the first version of the diagnostic made exactly that mistake.
- **Consequences:** the spatial BEV-vs-PHEV differential is restored for 105 of
  113 ZGB Gemeinden. In Kreis Gifhorn (widest spread) the `gelaendewagen`
  segment moves from an untilted 0.0268 BEV / 0.0536 PHEV to **0.0459 /
  0.0345** in BEV-heavy Adenbüttel and to **0.0069 / 0.0259** in PHEV-heavy
  Steinhorst, with the electric total identical to 9 decimal places in both.
  Fleets change accordingly; emissions post-processing that distinguishes BEV
  from PHEV is affected, per-Kreis electric levels are not. The feature is
  flag-gated (`fleet_gemeinde_bev_composition_tilt`, default true); the tilt
  consumes no RNG, so the OFF path is byte-identical to the pre-#317 draw.
- **Evidence:** `tests/test_fleet_gemeinde_bev_composition.py` (35 tests, all
  green), including a `TestCompositionAggregateNeutrality` group that builds a
  population deliberately skewed AGAINST the FZ weights (9 cars in the BEV-heavy
  Gemeinde, 1 in the PHEV-heavy one), asserts the uncorrected aggregate really
  does move, and then asserts decision 8 restores it to the untilted value
  exactly while leaving each car's electric total and the within-Kreis ordering
  intact. Also `tests/test_execute_context_config_contract.py::
  test_fleet_stage_stub_covers_every_execute_config_key`, a static (ast-only)
  guard added because the stage test that would have caught the missing stub key
  cannot even be COLLECTED where the local `matsim-tools` install shadows the
  repository's `matsim` namespace package — six flags in a row had hit that gap.
  Further: the electric-stock-weighting invariance, an explicit test
  that the total-car-stock weighting breaks it, the measured-zero flooring, the
  clip renormalisation, the level guarantee under a deliberately widened source
  gap, and the committed table's 105/113 coverage with the eight excluded
  Gemeinden named. Diagnostic:
  `scripts/measure_gemeinde_bev_composition.py` (output quoted above), which
  measures the residual by running the real `PowertrainModel` with the tilt ON
  and OFF rather than approximating it — an independent second implementation
  that reproduces the figures measured during development to all printed digits.
  Regression: `tests/test_fleet_sampling_de.py`, `test_fleet_gemeinde_ev_2026.py`,
  `test_fleet_grid_tilt*.py`, `test_fleet_ev_income_tilt.py`,
  `test_gemeinde_normalize.py` — 118 passed, 3 skipped.
- **Alternatives rejected:** (a) A national or per-Kreis BEV:PHEV ratio to split
  the combined share — rejected up front in issue #317 as an invented reference.
  (b) Weighting the Kreis composition by total car stock — measured to break the
  mean-1.0 invariance, which the ADR-0085 rake would then preserve as a level
  shift; pinned by a test. (c) Applying the raw factors without the
  electric-total rescale (issue #317's literal design) — leaks the composition
  into the electric level per Gemeinde whenever the private and all-ownership
  splits differ, which they measurably do by up to 5 pp. (d) Applying the factor
  as a hard 0 for a measured-zero-PHEV Gemeinde — inconsistent with ADR-0086
  decision 1 and unsupportable on a ~14-car electric stock. (e) Reverting the
  whole Gemeinde tilt to FZ 27.17 — already rejected in ADR-0086 alternative
  (a); the 2026 combined share remains the better level signal.
  (f) Relying on the FZ-stock normalisation alone for aggregate neutrality (this
  record's own first version) — it normalises against the wrong population, as
  the PR review pointed out: the rake targets the unweighted mean of the ACTUAL
  cars, so any mismatch between the synthetic cars-per-Gemeinde and the FZ
  electric stock survived as a per-Kreis shift. Superseded by decision 8.
  (g) Leaving that shift in place and merely DOCUMENTING it as a measured
  limitation — rejected: it is an artefact of which reference table the factors
  happened to be centred on, not a modelling position, and ADR-0085's rake turns
  it into a standing bias rather than noise. A defect that is cheap to remove is
  removed, not disclosed.
- **Generalised rule:** the PR review also found the composition counters reporting a
  deliberate OFF run as a 100 % missing-reference fallback (and warning about it).
  That is not specific to this feature, so the rule it implies — a fallback counter
  must separate DISABLED / NOT APPLICABLE from MISSING REFERENCE, and a disabled
  feature must still be logged as disabled rather than silently — is written up once
  in [`docs/codebase/notes/fallback-counter-states.md`](../codebase/notes/fallback-counter-states.md)
  instead of being restated per feature.
- **Issue / PR:** #317
