# ADR-0075 — SrV location types (#262): crosswalk A2, formula-based errand supply, mixed-pool mean normalization, escapes after guards (2026-08-13, PR #263 pending)

- **Context:** issue #262 transfers the escort feature's SrV-grounded location refinement to
  leisure/other secondary activities. Three design decisions were contested during brainstorming
  and two plan defects surfaced during execution; all are recorded here with their committed
  sources (the feature doc `docs/features/secondary-location-types.md`, the pinned CSVs under
  `eqasim-data/data/braunschweig/srv/` and `.../buildings/`, and PR #263).
- **Decision:** (1) **crosswalk A2** — MiD W_ZWD subtypes remain the per-leg DISTANCE labels
  (layers unchanged); a per-leg SrV `V_ZWECK` category, drawn from pinned
  P(type | E_HVM_5 mode, euclidean-equivalent distance band) AFTER desired-distance sampling,
  owns PLACEMENT (rejected: A1 travel-time conditioning — weaker type↔distance coupling;
  SrV-primary draw — discards donor information; unified taxonomy — forces re-estimating all
  distance layers). (2) **Errand supply via the spec formula** — `min(potential_generic, cap) ×
  class-membership` per category with errand-class buildings appended as candidates; the plan's
  original pot_other masking was a defect (sec_b_* rows carry pot_other=0.0 by construction,
  errand-class buildings were excluded by the keep-filter → structurally zero supply).
  (3) **Mixed-pool mean normalization** — landuse-point potentials in pools shared with buildings
  (leisure_culture/sports) are scaled to the category's building-potential mean (ASSUMPTION:
  an average landuse point ranks like an average building; needed because the linear
  attr_transform feeds raw magnitudes into the combined score); pure pools (leisure_outdoor)
  stay raw-area. (4) **External-centroid category escapes AFTER the seven-pool supply guards** —
  escapes restore long-distance reach for category legs but would structurally neuter
  `check_category_supply`/`check_visit_pool_supply` if applied first (found by scoped re-review,
  fixed with a mutation-proof ordering test). (5) The spec's original per-leg two-level candidate
  fallback chain is SUPERSEDED by the guard+escape architecture (final-review finding).
- **Consequences:** 5% A/B (RUNS.md `srv262-AB-5pct-2026-08-12`): leisure placements move
  buildings 67.7→34.7%, landuse points 0→31.9%; errands to typed buildings 0→75.5%; all 9 drawn
  shares within 1.8 pp of the pinned SrV references; purpose mix byte-identical. Known limits,
  documented not hidden: realized distance medians nearly unchanged (top_n desired-distance
  inertness, pre-existing backlog item); MiD-vs-SrV distance level gap (~1.5–2×) present in both
  runs (pre-existing); `leisure_visit` boundary clip 10.8% (residential-only pool, no external
  escape by design — now measurable via the restored per-category clip diagnostic).

