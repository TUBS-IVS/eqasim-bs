# ADR-0026 · 2026-06-25 · Purpose-resolved secondary distances (Tier 1 + Tier 2 daily/non-daily)
- **Status:** active
- **Context:** `_sample_leg_distance` drew the desired distance per mode only, so a shop-by-car and
  a leisure-by-car leg drew the same distribution, diluting shop distances by the longer leisure tail.
- **Decision:** Tier 1 — build per-(purpose×mode×band) distributions (`secondary_distance_by_purpose`).
  Tier 2 — split shopping into daily/non-daily (`secondary_shop_daily_split`) via a seeded subtype
  imputation from MiD W_ZWD, with daily/non-daily distances and `retail_daily`/`retail_non_daily`
  building placement. Both flags ON in the all-features popsim configs.
- **Rationale:** MiD W_GEW means show ~3× shop and ~5× leisure subtype distance ranges; OFF baseline
  EMD (shop 0.053/leisure 0.064/other 0.018) is below the 0.08 threshold, so this is a realism
  *refinement*, not a broken-model fix; sparse-cell fallback rate is logged (no silent fallback)
  (`docs/features/secondary-distances.md`).
- **Consequences:** The eqasim output purpose stays shop/leisure/other; resolution is internal.
  A later leisure W12 fix (ADR-0033) corrected a double-counting interaction.
- **Evidence:** `docs/features/secondary-distances.md`; commits `c68c8df`, `706b87a`, `8e98e3d`;
  PROJECT_STATUS.md §2.4 (MiD W12 per-purpose).

