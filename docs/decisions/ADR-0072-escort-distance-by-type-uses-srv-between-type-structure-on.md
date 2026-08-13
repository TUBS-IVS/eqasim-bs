# ADR-0072 — Escort distance-by-type uses SrV between-type structure on the MiD level (A3, #257) (2026-08-11)

- **Status:** accepted 2026-08-11. Branch `feature/escort-purpose-201` (worktree
  `.claude/worktrees/feature-escort-purpose-201`, stacks on #201), PR pending.
  Flag `escort_distance_by_type` defaults False (code); `true` in `configs/base_bs.yml`
  (features-default-ON convention); OFF path byte-identical (no layer synthesis,
  no draw-stream change).
- **Context:** the 5% validation run (`output_bs_5pct_escort`, baseline 2026-08-11)
  showed the escort trip-length distribution wrong in SHAPE while right in MEAN
  once `escort_purpose`/`escort_household_link` were wired: <2 km share 25.6 %
  vs 39-40.8 % reference (MiD W12 / donor legs), band L1 vs W12 27.8 pp. Root
  cause (systematic-debugging): every escort leg sampled ONE pooled aggregate
  `escort` distance layer regardless of the drawn destination type, so a Kita
  drop-off (typically 1-2 km) and a residential escort drew from the same
  distribution.
- **Decision: A3** -- keep the distance LEVEL from MiD, take only the between-type
  STRUCTURE from SrV. Per-type layers are synthesized at runtime
  (`_synthesize_escort_type_layers` in `secondary_chainsolvers.py`) as deep copies
  of the MiD-built `escort` layer, each destination type's `values` array scaled
  by a SrV-derived factor, `factor_c = weighted_median(GIS length | category c) / weighted_median(GIS length | all escort legs)`,
  computed in `scripts/derive_escort_location_weights.py`.
  Rejected alternatives: **A2** (full SrV per-type length distributions) -- mixes
  survey levels (different sample, weights, length variable than the MiD-built
  layer every other purpose uses); **A1** (alias each type to an existing MiD
  purpose layer, e.g. `escort_edu_*` -> `education`) -- kept only as the
  gate-fail pivot, not needed here. The A3-vs-A1 choice was gated on a
  pre-registered coherence check (spec section 3, see Evidence).
- **Evidence:** coherence gate PASS (`compute_length_coherence`, comparing SrV
  `V_ZWECK==12` `GIS_LAENGE_GUELTIG` against MiD `W_ZWECK==6` `wegkm_imp` on nine
  W12 length bands plus overall medians; ASSUMPTION thresholds L1<=25.0 pp, ratio
  in [0.67,1.5]): band_l1_pp=9.29 (threshold 25.0), median_ratio=0.929 (in
  [0.67,1.5]; SrV median 2.73 km vs MiD median 2.94 km). Source: header of the
  pinned `srv2023_escort_distance_factors.csv` (under
  `eqasim-data/data/braunschweig/srv/`), generated 2026-08-11 by
  `scripts/derive_escort_location_weights.py` from `SrV2023_Wege.csv` (GEWICHT_W-weighted,
  GIS coverage 82.45 % of valid-BHOL escort legs, n_valid=2602) and `MiD2023_Wege.csv`.
- **Consequences:** per-type distance layers are synthesized inside the chainsolver
  stage at runtime (no upstream cache invalidation -- the shared `distance_distributions`
  stage is untouched). Thin categories `edu_university` (n=11) and `shop` (n=13)
  are neutralized to `factor_applied=1.0` (documented, `min_obs=30`), not silently
  dropped. Under the pinned draw weights (`DEFAULT_ESCORT_LOCATIONS_WEIGHTS`) and
  factors (`DEFAULT_ESCORT_DISTANCE_FACTORS`), the expected escort distance level
  shifts by `sum(w_c x factor_c) = 1.0305` (+3.1 %) versus the pre-A3 pooled layer
  -- a known, accepted by-construction drift, well inside the +-20 % mean criterion
  of spec section 7 (documented as an ASSUMPTION in `docs/features/escort-purpose.md`).
  Missing per-type layers fall back COUNTED and two-level (drawn type -> aggregate
  `escort` -> `other`); a >20 % per-type fallback rate now raises a loud runtime
  WARNING (final-review hardening, #257). The 5% validation re-run
  (`configs/overlays/escort_reuse_5pct.yml`, popsim batches reused) reports the
  realised shift and the new band-fit metrics against the 2026-08-11 baseline
  (goals, not hard gates, per spec section 7). Related, explicitly out of scope:
  #256 (MiD W_ZWECK 13 passive-side semantics) does not affect these factors --
  they encode between-type structure, not level, and remain valid if #256 later
  changes the base `escort` layer's composition.
- **Evidence artefacts:** spec `docs/superpowers/specs/2026-08-11-escort-distance-by-type-design.md`;
  plan `docs/superpowers/plans/2026-08-11-escort-distance-by-type.md`;
  `docs/features/escort-purpose.md`.

