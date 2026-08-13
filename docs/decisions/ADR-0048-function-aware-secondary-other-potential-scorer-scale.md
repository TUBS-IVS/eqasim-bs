# ADR-0048 · 2026-06-28 · Function-aware secondary `other` potential + scorer scale-alignment
- **Status:** active on PR #77 (open, Closes #27) — Part A active; Part B scorer calibration server-deferred.
- **Context:** The secondary `other` potential was the raw `potential_generic =
  volume_m3 × bosserhof_class_weight`, which is function-blind. The VW-Werk Wolfsburg (8.9M m³ →
  26.7M potential, a real building) and steel/wholesale giants dominated the chainsolvers `other`
  candidate score, concentrating errand activities on industrial mega-structures. The realised
  distance distribution was unaffected (carla's ring candidate generation bounds distance) — the
  defect was within-pool placement, not distance.
- **Decision:** (A) Derive `potential_other = min(generic, cap) × (broad_share + errand_share·1(class
  ∈ whitelist))`, zeroed below `min_volume_m3`, from a committed Bosserhof-class→eqasim-purpose
  mapping CSV; attach it to the legacy `other` candidates instead of raw `generic`. (B) Bump the
  chainsolvers pin to `d8d8ae7d` for the native `Scorer(attr_transform="log1p")` + `mnl` selection
  (use the library lever, no downstream pre-scaling) and add a measure-first calibration CLI; defer
  pinning `attr_transform`/weights and any `dp_sample`/`mnl` A/B to a server run.
- **Rationale:** `other` = MiD 2023 W_ZWECK 5 Erledigung (45.7%) + 6 Bringen/Holen (23.1%) + 10 anderer
  (31.2%) collapsed into one eqasim `other`, so it cannot be restricted to service buildings;
  broad_share=0.54/errand_share=0.46 are those W_GEW-weighted shares (`MiD2023_Wege.csv`). Whitelist =
  11 errand classes; research institutes + car dealerships excluded (user decision). A uniform cap
  (whitelist-generic percentile, applied to all) tames the volume tail. OFF byte-identical; no invented
  values; pinning gated on a measured W12 win (shop 0.053/leisure 0.064/other 0.018) — convergence ≠
  validation.
- **Consequences:** Errand placement no longer over-attracted to factories; Part A enabled in the 5
  real configs; chainsolvers bump backward-compatible (attr_transform defaults to "linear").
- **Evidence:** PR #77; issue #27; spec/plan `docs/superpowers/{specs,plans}/2026-06-28-smart-other-potential*`
  (gitignored); memory `project-smart-other-potential`; commits `8fdb2f3..4af644a` on `feature/smart-other-potential`.

