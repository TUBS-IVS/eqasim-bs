# ADR-0092 · 2026-08-19 · Car/bike ownership gets a 1 km MiD-shaped control, raked exactly to the blended Kreis anchors (issue #240)

- **Status:** active
- **Context:** Issue #240 measured car/bike ownership as the loosest-fitting `popsim_mid` control
  family on the 100 % run: car-free households target 18.1 %, realised 14.5 % (**-3.6 pp**);
  bicycle-free households target 26.4 %, realised 21.7 % (**-4.7 pp**); every other control family
  fits within ~2.7 pp and the census backbone within ~1.6 pp. Root cause per the issue: ownership
  sits at KREIS (the SrV survey scale) while the census backbone (age x sex, household size/type,
  tenure, building type) sits at ZENSUS 100 m and dominates the per-cell allocation -- a coarse
  KREIS control only constrains the Kreis aggregate, not where inside it the ownership category
  lands. The deficit is spatially concentrated in the urban core (Braunschweig-Stadt -3.0 pp,
  Goslar -2.4 pp, Gifhorn -2.0 pp; rural Kreise within +/-1.4 pp) and the donor pool's H_GEW-weighted
  car-free share (19.8 %) exceeds the target, so the realised value collapsed toward the donor's
  unweighted composition (13.1 %) rather than a donor shortage.
  A 2026-08-19 pre-implementation prototype (a throwaway exploratory script, NOT committed --
  recorded only in the gitignored design spec that preceded this branch, so the following figures
  are motivating evidence rather than an independently reproducible reference; the built module's
  own behaviour is what the pending A/B in Consequences will measure and record in a run manifest)
  found, on the ZGB's 2,410 ZENSUS1km cells: every per-Kreis rake converges (<= 6 iterations at the
  tolerance the shipped code uses, `tol=1e-9`), RS7-only fallback 0.09 %, Kreis-straddling 1 km
  cells 1.0 %, RS7-mixing cells 1.9 %. Within-Kreis household-weighted P10-P90 spread of the
  car-free share: Braunschweig-Stadt 13.1 pp, Wolfsburg 11.9 pp, Goslar 17.1 pp, rural Kreise
  ~6 pp (bikes 3-11 pp) -- variation a KREIS-level control cannot see or steer at all.
  Separately, and independently verified here directly from the two committed per-Kreis tables
  (`eqasim-data/data/braunschweig/mid/mid2023_H7_cars_by_kreis.csv` x
  `eqasim-data/data/braunschweig/srv/srv2023_cars_by_kreis.csv`): the raw MiD H7 and SrV car-free
  shares disagree by up to **9.3 pp** per Kreis where both cover it (Goslar 22.0 % MiD vs 12.7 %
  SrV), and Wolfsburg has no SrV coverage at all. Neither raw survey table is an uncontested
  per-Kreis anchor on its own.
- **Decision:**
  1. **Two-part construction, shape and level from different sources.** SHAPE comes from the
     committed MiD B1 RS7 x haustyp conditionals (`mid2023_{cars,bikes}_by_rs7_haustyp.csv`) -- a
     MODELLED prior, stated explicitly as an ASSUMPTION: the national MiD ownership <-> RS7 x
     building-type relationship transfers spatially within the ZGB (issue #240 already flagged
     that residual, unexplained spatial variation is not captured by any such proxy). LEVEL comes
     from the blended `target2026_{number_of_cars,number_of_bicycles}` KREIS tables -- the SAME
     committed anchors the existing KREIS ownership controls already consume, never a second
     anchor truth beside them.
  2. **Rake, do not scale.** `ownership_grid.rake_ownership_targets` (per-Kreis IPF) reweights the
     dwelling-mixed cell priors to hit the `target2026` category shares times the pipeline's own
     per-Kreis household total, so the 1 km layer cannot silently drift from the KREIS anchor it
     is supposed to refine (see the honesty note below for exactly what "hit" means numerically).
  3. **9 controls at `GEO_1KM`** (4 car categories + 5 bike categories), reusing the SAME category
     expressions (`households.number_of_cars ...` / `households.number_of_bicycles ...`) as the
     KREIS registry entries (`kreis_attribute_control.REGISTRY`), so both layers select identical
     seed universes by construction and can never define "car-free" or "bike-free" differently
     from one another.
  4. **New importance group `grid_shape`, default 500** -- below the census backbone (1000) and
     the `kreis_hard` KREIS anchors (2000): shape must yield to both. An initial value, re-examined
     once the A/B (Consequences) shows whether it is doing enough or too much.
  5. **Flag `braunschweig.population.popsim.ownership_grid_1km`**, declared ONLY in
     `configs/base_bs.yml` (overlays stay scale-only), default `"on"`. It requires the
     `number_of_cars` + `number_of_bicycles` KREIS controls to be active -- the grid reuses their
     seed columns and their `target2026` anchors -- and raises rather than silently degrading if
     they are toggled off while the grid is on. `"off"` reproduces today's control set
     byte-identically. MiD-only (its catalog entries carry `seed_expressions["entd"] = None`, so
     `controls_for_seed` drops them under `popsim_open`); an ENTD run with the default-on key logs
     a no-op instead of crashing or silently doing nothing unexplained.
  6. **Bikes included alongside cars in v1**, despite a visibly weaker spatial signal: bikes are
     driven more by building type than by urbanity (RS7 72, EFH vs Geschosswohnungsbau: bike-free
     23.7 % vs 32.8 %, against cars 15.9 % vs 44.3 % for the identical cell contrast -- both figures
     read directly off the committed
     `eqasim-data/data/braunschweig/mid/mid2023_{cars,bikes}_by_rs7_haustyp.csv`). Recorded as
     expectation management in the Feature Registry, not treated as a reason to defer bikes to a
     later iteration.
- **Honesty on precision (why the identity is not literally "bit-for-bit" end to end):**
  - The 100 m -> 1 km ROW identity IS structurally exact: `add_ownership_grid_columns`
    back-distributes each raked 1 km-parent target to its member 100 m cells strictly proportional
    to each cell's share of the parent's household total, so re-summing the 100 m children
    reproduces the parent's raked target exactly (modulo ordinary floating-point rounding). This is
    the identity the `ownership_grid` module docstring calls "bit-for-bit", and that claim is
    correctly scoped to this row identity only -- it says nothing about the Kreis-level rake.
  - The per-Kreis COLUMN identity -- that a Kreis's raked 1 km parents sum back to the
    `target2026` share times the Kreis household total -- holds only to the rake's own convergence
    tolerance, `tol=1e-9` RELATIVE margin error (`rake_ownership_targets`), not to literal
    bit-for-bit equality: IPF is iterative and stops as soon as the relative margin error drops
    below `tol`, leaving a residual up to that bound rather than an exact zero.
    `add_ownership_grid_columns` raises if a 1 km parent's Kreis assignment is not internally
    constant (protecting the row identity above), but it does not re-assert the column identity
    beyond what the rake itself already guarantees.
  - Downstream, `braunschweig.popsim.folders.build_control_totals` integerizes every
    ZENSUS100m/ZENSUS1km control column (largest-remainder method) WITHIN each 1 km parent before
    PopulationSim ever sees it -- true of every grid control in the catalog, not unique to this
    one. The raked real-valued `OWN_*` targets this ADR describes are therefore rounded to
    integers one further step downstream; the `1e-9` tolerance describes the rake's OWN
    real-valued output, not the integer totals PopulationSim ultimately balances against.
- **Rejected alternatives:**
  - *Raw H7 / H12.3 KREIS marginals as the anchor, instead of `target2026`.* Rejected: they
    disagree with SrV by up to 9.3 pp per Kreis where both cover it (Goslar), and Wolfsburg has no
    SrV coverage at all (see Context). Anchoring to either raw survey directly would create a
    SECOND, competing per-Kreis ownership truth beside the already-blended `target2026` tables the
    KREIS controls consume, with no principled way to reconcile the two layers.
  - *RS7-only prior* (drop the dwelling/haustyp mixing, key the shape purely on RegioStaR7).
    Rejected: RegioStaR7 is assigned at GEMEINDE granularity
    (`docs/registry/data/regiostar.yml`: `geography: Germany, Gemeinde (AGS)`), so it is CONSTANT
    across every cell inside a kreisfreie Stadt (Braunschweig, Salzgitter and Wolfsburg are each a
    single Gemeinde). An RS7-only prior would therefore be perfectly flat exactly where issue #240
    measured the deficit concentrating (Braunschweig-Stadt -3.0 pp) -- it supplies no within-Kreis
    differentiation at all in the cells that matter most.
  - *100 m geography instead of 1 km.* Rejected per the issue's own reasoning: finer than KREIS but
    with markedly more integerisation noise than 1 km on already-fractional per-cell counts;
    ZENSUS1km sits between the two and is synergistic with (not competing against) the
    building-type control already active at 100 m, since both are built from the same dwelling
    composition.
  - *Replacing the KREIS ownership controls outright.* Rejected: they remain the LEVEL anchor
    precisely so the 1 km layer has one committed target to rake to instead of inventing a second;
    the two layers cannot conflict by construction because the 1 km layer is raked to them exactly
    (consistency asserted at stage time; a violation raises rather than silently shipping a
    mismatched pair of layers).
- **Consequences:** the synthetic population is NOT byte-identical with the flag ON -- that is the
  point. Owed before this can claim more than "implemented, spec-layer control-fit checked": a 1 %
  smoke confirming the balancer still converges with 9 additional ZENSUS1km control columns
  (partially covered already by the reusable two-layer control smoke, issue #282, and by the
  spec-layer smoke this same task adds), then a 100 % A/B (flag ON vs OFF) re-measuring the
  EXISTING `cars_per_hh` / `bicycles_per_hh` validation controls (including the per-Kreis
  choropleth) and recording the fallback / Kreis-straddle / RS7-mixing rates from the stage logs in
  a run manifest -- no claim about the size of the improvement is made here. The `grid_shape=500`
  importance value (Decision 4) is an initial choice, re-examined once that A/B shows whether the
  shape layer moves the urban-core deficit without degrading the census backbone fit beyond noise.
  No change to the KREIS ownership controls, their committed targets, or `has_ebike`; no 100 m
  ownership variant; no new blend rule for `target2026` (unchanged from the design that preceded
  this branch).
