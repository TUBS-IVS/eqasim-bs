# SrV-grounded secondary location types

Issue #262 (pattern donor: the escort family, #201/#256/#257). Transfers the
escort feature's SrV-grounded location refinement (type draw -> type-specific
candidate universe -> type-specific distance coherence) to the two remaining
undifferentiated secondary purposes, `leisure` and `other`, behind
`secondary_srv_location_types` (code default OFF = byte-identical; ON in
`configs/base_bs.yml`). Design record:
`docs/superpowers/specs/2026-08-12-srv-location-types-design.md`.

## Data sources

- **Type-by-distance probability table** — SrV 2023 BS+RGB Wege
  (`V_ZWECK` mapped to 6 leisure + 3 other categories, `E_HVM_5` main mode,
  `GIS_LAENGE_GUELTIG` valid-only routed length, GEWICHT_W-weighted),
  `min_obs=30` per `(purpose, category, mode, band)` cell, thin cells omitted
  and marginal-fallback rate-logged — `scripts/derive_srv_location_types.py`
  writes the pinned reference
  `eqasim-data/data/braunschweig/srv/srv2023_location_type_by_distance.csv`.
  Real-data coverage (13,514 in-scope legs): 4 legs (0.03%) excluded for an
  invalid `E_HVM_5` mode code, 3,547 (26.2%) excluded from the per-cell band
  split and the weighted-median columns for an invalid/sentinel
  `GIS_LAENGE_GUELTIG` (NOT excluded from the category-share marginals, which
  are distance-independent); 17/67 candidate cells (25.4%) omitted as thin,
  leaving 50 reported cells.
- **Type shares + reference medians** — the same script's second output,
  `eqasim-data/data/braunschweig/srv/srv2023_secondary_type_shares.csv`:
  weighted marginal category shares and weighted-median euclidean-equivalent
  distances for `leisure` (6 categories), `other` (3 categories), and — the
  #242 contribution — `shop` (`shop_daily`/`shop_non_daily`, VALIDATION-ONLY
  rows; shop location choice is decided by its own existing daily/non-daily
  split, never by this decider). Largest categories by weighted share:
  `leisure_outdoor` 31.2% (median 1.02 km), `errand_authority_medical` 44.8%
  (median 3.55 km).
- **Bosserhof class -> location-category mapping** — the committed
  `eqasim-data/data/braunschweig/buildings/bosserhof_class_to_location_category.csv`
  (19 of the ~44 Bosserhof classes mapped to the 5 building-backed categories:
  `leisure_culture`, `leisure_gastronomy`, `leisure_sports`,
  `errand_authority_medical`, `errand_service`), seeded and
  cross-validated against `bosserhof_class_to_purpose.csv` by
  `scripts/seed_bosserhof_class_to_location_category.py` (fail-fast on drift
  between the two mappings). Loader: `braunschweig.data.bosserhof_location_category`.
- **ATKIS landuse polygons** (`braunschweig.data.landuse`) supply the
  building-less categories: `ln_kulturundunterhaltung` (mixed into
  `leisure_culture`), `ln_sportanlage` (mixed into `leisure_sports`),
  `ln_freiluftundnaherholung` (pure `leisure_outdoor` pool). Measured
  directly from the committed source parquet,
  `eqasim-data/data/braunschweig/preprocessed/landuse.parquet`
  (`gpd.read_parquet(path)`, filtered to `layer == "ln_freiluftundnaherholung"`,
  reporting `len(df)` and `df["area_m2"].sum() / 1e6`, CRS EPSG:25832):
  **15,572 polygons / 51.53 km2** (re-measured 2026-08-12).

## Mechanics

1. **Two labels per leg.** The existing MiD `W_ZWD` subtype
   (`leisure_local`/`visit`/`activity`/`excursion`,
   `other_errand_short`/`long`) remains the per-leg **distance label** — it
   keys the unchanged distance sampler, so no distance layer is
   re-estimated. The newly drawn SrV `V_ZWECK` category becomes the per-leg
   **location category** — it keys candidate lookup and placement. A leg can
   carry `distance_label=leisure_visit` and a non-visit `location_category`
   by design (SrV owns location semantics; checked only in aggregate, see
   Validation).
2. **A2 draw, after desired-distance sampling.** Once a leisure/other leg's
   desired distance is sampled (unchanged), it draws
   `(category, used_marginal) = decider(purpose, mode, distance_m)` from the
   pinned probability table: `P(category | mode, euclidean-equivalent
   distance band)`. Model desired distances are already euclidean; SrV's
   routed `GIS_LAENGE_GUELTIG` is converted with the same
   `DETOUR_FACTOR = 1.3` already used for the distance layers (ASSUMPTION,
   shared with the escort feature). Band edges (euclidean km):
   `0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0, inf`. `E_HVM_5` maps 1:1 onto the five
   eqasim modes (`walk`, `bicycle`, `car`, `car_passenger`, `pt`), so
   conditioning is exactly `(mode, band)` — no mode reduction was needed.
   Thin `(mode, band)` cells fall back to the purpose's marginal category
   distribution; the fallback rate is counted and rate-logged PER PURPOSE
   (`srv_location_marginal_fallback_leisure` / `_other`), not pooled — leisure
   legs outnumber `other` legs several times over, so a pooled rate would let
   a badly covered purpose hide behind a well-covered one. A per-purpose share
   >= 20% (heuristic, `SRV_LOCATION_MARGINAL_FALLBACK_WARN_SHARE`) escalates
   to a `WARNING` log line. Dedicated RNG offset `SRV_LOCATION_SEED_OFFSET =
   90215` (`random_seed + offset`, one draw per leg in leg order) — enabling
   or disabling the flag never perturbs any other RNG stream.
3. **`leisure_visit` collision, resolved.** SrV category 15 (privater Besuch)
   overlaps the pre-existing `leisure_visit_building_potential` placement
   (`leisure_visit` MiD subtype -> `pot_visit` residential candidates). With
   the flag ON, the SrV draw owns placement for ALL leisure legs; a leg drawn
   into `leisure_visit` routes to the SAME residential `sec_res_*` machinery
   (issue #127) via the shared `offers_visit`/`pot_visit` columns.
   `leisure_visit` as a MiD label degrades to a pure distance label under this
   flag.
4. **Category candidate universes.**
   - **Leisure building categories** (`leisure_culture`, `leisure_gastronomy`,
     `leisure_sports`) mask the existing `pot_leisure` aggregate on
     Bosserhof-mapped buildings (unchanged potential, category-restricted
     membership). ASSUMPTION: hotels are excluded from `leisure_gastronomy`
     (lodging, not dining).
   - **Errand building categories** (`errand_authority_medical`,
     `errand_service`) cannot mask `pot_other`: `build_secondary_candidates`
     sets `pot_other = 0.0` on every building row and excludes errand-class
     buildings (hospitals, authorities, service businesses) from its
     `retail>0 | leisure>0` keep-filter, so masking alone left both
     categories with zero structural supply — confirmed against real data via
     the committed mapping CSV. Fix (approved plan amendment): potential is
     derived directly from `building_potentials`, generalizing
     `secondary_other_potential.derive_other_potential`'s cap-and-floor
     formula (`min(potential_generic, cap) x class-membership`, using the
     already-configured `secondary_other_min_volume_m3` /
     `secondary_other_cap_percentile`) from one whitelist to each errand
     category independently. A building with positive computed potential
     that was excluded from the base candidate set is appended as a NEW
     `sec_b_<building_id>` row (`sec_b_` prefix; "sec_b_ rows" in the review
     history) carrying only that category's offer/potential — every other
     category column `False`/`0.0` on that row.
   - **Landuse categories** use deterministic, area-proportional grid seeding
     (`braunschweig.synthesis.locations.landuse_candidates`,
     `secondary_landuse_grid_spacing_meters`, default 150 m, EPSG:25832): a
     square grid absolute to the CRS origin (not per-polygon bounds, so
     fragmentation cannot change point counts) is laid over each category's
     polygons; every contained grid node becomes a candidate with
     `represented_area_m2` = one grid cell's area; polygons too small to
     catch a node fall back to their `representative_point()` with their own
     true area. No RNG. CAVEAT: a shared fragment edge lying exactly on a
     grid line loses that edge's nodes to both fragments (`shapely.contains`
     is boundary-excluding) — a measure-zero case for real ATKIS polygon
     boundaries, not observed to matter for irregular real geometry, verified
     only against synthetic exactly-gridded input.
   - **Mean-normalization in MIXED pools** (`leisure_culture`,
     `leisure_sports`, which have BOTH building and landuse supply):
     building `pot_<category>` is a disaggregated zonal person-mass; landuse
     `pot_<category>` is a constant grid-cell area. Under the combined
     scorer's default linear attribute transform the larger raw magnitude
     would dominate regardless of actual relative attractiveness, so landuse
     potentials in a mixed pool are rescaled by
     `mean(positive building pot_<category>) / mean(raw landuse pot_<category>)`
     — a pure linear rescale that preserves the relative area ratio among a
     category's own landuse points exactly while equalizing the two sources'
     means. ASSUMPTION: an average landuse point ranks like an average
     building of its category. **Pure pools** (`leisure_outdoor`, no building
     counterpart) stay raw — every candidate is already on the same (area)
     scale, so a constant multiplier would cancel in the ranking.
   - **External Gemeinde centroids** (category-agnostic long-distance
     escapes, pre-existing `secondary_external_candidates` machinery) are
     given `offers_<category> = True` / `pot_<category> = pot_leisure` (for
     leisure categories) or `pot_other` (for errand categories) — their `ewz`
     — restoring long-distance reach for every placement category except
     `leisure_visit` (residential-only pool, unchanged from the OFF path).
     APPLIED AFTER the supply guard (`check_category_supply`), never before:
     running the escapes first would make every category unconditionally
     non-empty and neuter the guard's ability to catch a genuinely
     zero-supply category.
   - **No per-leg type-specific-to-generic candidate fallback.** An earlier
     design draft proposed a two-level per-leg fallback (type-specific
     candidates unavailable/empty in reach -> generic `pot_leisure` /
     `pot_other` pool, counted and rate-logged). That mechanism was NOT built;
     it was superseded during implementation by three separate, more
     falsifiable mechanisms:
     1. **`check_category_supply`** (hard `RuntimeError`) raises if any
        placement category has zero positive-potential rows REGION-WIDE —
        a wiring failure (broken mapping, grid-seeding gap, potential-join
        miss), not thin data. It runs on the escape-free frame, BEFORE the
        external escapes below, so it stays falsifiable (see the External
        Gemeinde centroids bullet above).
     2. **External Gemeinde-centroid category escapes** (the bullet above)
        are category-agnostic long-distance candidates, not a per-leg
        category-specific fallback: every placement category (except
        `leisure_visit`) always has this same escape pool available in
        addition to its in-area candidates, regardless of whether the
        category is locally thin.
     3. **The counted per-purpose marginal fallback of the A2 draw** (point 2
        above, `srv_location_marginal_fallback_leisure` / `_other`) is a
        different fallback: it degrades a thin `(mode, band)` CELL to the
        purpose's marginal CATEGORY distribution during the type DRAW, before
        any candidate lookup happens — not a candidate-universe fallback.
     A category that is locally thin but not region-wide zero (passes
     `check_category_supply`, has few in-area candidates) is not caught by
     any explicit fallback counter; it is instead absorbed implicitly by the
     combined scorer's distance-deviation term, which can still select a
     distant in-area or external-escape candidate over a nonexistent
     type-specific one nearby. Whether this leaves locally thin categories
     under-served in practice is a measurement question, not yet answered —
     it is in scope for the pending A/B run (see Validation).
     Separately, and pre-existing (not part of this feature): the legacy
     `carla`-to-RDA problem-level fallback on the underlying candidate-search
     frame is unchanged by this feature and continues to apply uniformly
     across all secondary purposes.
5. **Facilities fold.** `braunschweig.matsim.scenario.facilities` folds every
   `offers_<category>` column back into the plain `offers_leisure` /
   `offers_other` advertisement (OR-accumulation across the 4 leisure and 2
   errand category columns) so the MATSim facilities file keeps its existing
   `leisure`/`other` activity vocabulary; fails fast (naming the missing
   column) if the flag is ON but an expected category column is absent.
6. **Draw-summary artifact.** Every run with the flag ON writes
   `srv_location_draw_summary.csv` under the chainsolver stage's output
   directory: one row per `(purpose, category)` comparing the drawn share and
   drawn median desired distance against the pinned reference share/median
   from `srv2023_secondary_type_shares.csv`, flagging any `|drawn share -
   reference share|` above `srv_location_share_warn_pp` (default 5.0
   percentage points, heuristic) with a `WARNING`, plus loud warnings for a
   category absent from the pinned reference (vocabulary drift) and for a
   purpose that drew zero legs. **Honesty label, carried verbatim in the
   CSV's header comment**: this is a DRAW-COHERENCE check on the
   decider — comparing sampled DESIRED distances against SrV medians —
   **NOT a validation of REALISED (placed) distances**; carla's candidate
   search can still deviate from the desired distance (`top_n` selection
   inertness, backlog Tier-0 item (a)), which is a separate question assessed
   only in the A/B run below.
7. **Excursion boundary-clip diagnostic — inert, not removed.** A pre-existing
   Task-6-era diagnostic measured how often `leisure_excursion` legs clip at
   the study-area boundary. With this flag ON, no plan row carries
   `leisure_excursion` as a placement activity any more (SrV owns placement),
   so the diagnostic's counters are structurally `0/0`. Rather than print a
   `0/0` line that misreads as "no clipping observed," the stage prints an
   explicit line instead (`secondary_chainsolvers.py`, verbatim):
   `"[braunschweig.secondary_chainsolvers] leisure_excursion boundary-clip:
   not measured -- secondary_srv_location_types is ON, so the MiD
   'leisure_excursion' label drives the distance layer only and never
   becomes a placement activity (the drawn SrV location category does)."`
   Re-establishing the measurement under SrV placement needs the distance
   label carried alongside the placement activity — out of this feature's
   scope; see Follow-ups.

## Assumptions

- `DETOUR_FACTOR = 1.3` converts SrV's routed `GIS_LAENGE_GUELTIG` to a
  euclidean-equivalent for banding, mirroring the identical assumption
  already used for the MiD distance layers.
- Hotels are excluded from `leisure_gastronomy` (lodging vs. dining) in the
  Bosserhof mapping seed.
- Band edges (`0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0, inf` km euclidean) are a
  fixed discretization choice, not independently validated against an
  external reference.
- Bosserhof LLM-classified building-class accuracy is inherited unchanged
  from the pre-existing building-potentials pipeline; this feature adds no
  new classification step.
- Mean-normalization in mixed landuse/building pools assumes an average
  landuse point ranks like an average building of its category (stated
  explicitly in Mechanics point 4).
- A shared ATKIS-polygon-fragment edge lying exactly on a 150 m grid line
  loses that edge's nodes from both fragments (measure-zero for real
  boundaries; see Mechanics point 4).
- The building `pot_leisure`/`pot_other` potential a category masks off
  carries forward the pre-existing residual: `pot_visit`'s park share (the
  approximate, TAZ-proportional redistribution of the old zonal leisure
  totals onto residential buildings, documented in
  `docs/features/building-potentials.md`) is inherited unchanged by this
  feature, not re-derived.

## Validation

- **Draw-coherence CSV** (`srv_location_draw_summary.csv`, Mechanics point 6)
  is written on every ON run; read it as a decider-sanity check, never as
  "validated against SrV" (see the honesty note carried verbatim in its
  header).
- **`leisure_visit`-vs-type-15 aggregate consistency**: no dedicated
  comparison line exists. The two shares are each printed in their own,
  separate info-level log line — the MiD `leisure_visit` distance-label
  share in the "leisure subtype labelling" line
  (`leisure_subtype_decider`'s per-subtype breakdown) and the drawn
  `leisure_visit` SrV-category share in the "srv location draw (leisure)"
  line (`_srv_location_draw_summary_lines`) — both exist simultaneously per
  Mechanics point 1 and are expected to diverge somewhat, since they answer
  different questions. Reading the two numbers side by side is a manual step
  today; a computed side-by-side comparison is out of scope for this feature
  and, if wanted, belongs with the pending A/B run (see below).
- **A/B on realised (placed) output** — no dedicated committed OFF overlay
  exists for this feature, matching the escort family's precedent (the
  escort A/B runs toggled the flag on a scratch copy of
  `configs/overlays/escort_reuse_5pct.yml` rather than committing a second
  overlay file, see `RUNS.md` `escort-AB-5pct-2026-08-11`). To reproduce the
  same recipe for this feature: compose `configs/base_bs.yml` +
  `configs/overlays/escort_reuse_5pct.yml` (or an equivalent 5% reuse
  overlay) twice, once as committed (`secondary_srv_location_types: true`)
  and once with a local, uncommitted copy overriding it to `false`, and
  compare the realized `location_category` shares and per-category median
  placed distances against the pinned SrV reference plus the OFF run's
  undifferentiated `pot_leisure`/`pot_other` placements. **A/B run: PENDING**
  — no server run has been executed for this feature yet; do not read any
  number in this document as a validated realised-output result until that
  run exists and is recorded in `RUNS.md`.
- **Expected, intended result change** (by construction, not yet measured):
  roughly 31% of leisure placements move from generic buildings to
  green/open-space landuse candidates; errand placements split across the
  authority/medical and service whitelist halves. This is a genuine change
  to scientific output, not a refactor — the OFF path stays byte-identical.

## Follow-ups

- **Excursion boundary-clip diagnostic** (Mechanics point 7): currently
  inert under the flag with an explicit log line; re-establishing the
  measurement needs the distance label threaded alongside the placement
  activity. Issue proposal pending user confirmation (not yet filed).
- **Option B** (possible later upgrade, non-blocking): request per-category
  `potential_*` columns directly from the TUBS-IVS
  Activities-and-Potentials-Calculation-Pipeline instead of deriving them
  in-repo from `potential_generic` via the generalized
  `derive_other_potential` formula.
- **#242 remainder**: this feature's `shop` validation-only rows in
  `srv2023_secondary_type_shares.csv` substantially co-resolve #242's SrV
  subtype re-validation goal, but the re-estimation decision for cases where
  MiD-national and SrV-regional shares/medians disagree (beyond the shop
  rows already pinned here) is still open and tracked under #242.
- `leisure_visit` legs (~21% of leisure under SrV) still have no
  out-of-area candidate — inherited unchanged from the pre-existing
  residential-only visit pool (issue #127); worth a backlog note if long
  visit distances clip in the pending A/B run.
- #241 (MiD `W_ZWECK` 14-16/99 explicit mapping gap) stays a separate,
  independently validated PR, as decided in the design record.
