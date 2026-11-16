# ADR-0195 · 2026-11-16 · The optimized PopulationSim importance profile is a base config value, so every scale uses it

- **Status:** accepted (owner, 2026-11-11: decision A5 of the repository audit follow-up plan).
- **Numbering:** ADR-0195 is the next free id. Checked on 2026-11-16 across all local branches: the
  highest existing id is ADR-0194.
- **Issue:** none; repository audit of 2026-11-11, package I (config hygiene).

## Context

- `braunschweig.population.popsim.importance_profile` selects the per-control importance weights of
  PopulationSim (`braunschweig/popsim/control_spec.py`, `IMPORTANCE_PROFILES`; code default `uniform`).
- Until now only the overlay `configs/overlays/test_100pct.yml` set `optimized_2026_06_30`. The 1 % and
  25 % overlays ran `uniform`. The per-scale key list of `tests/test_configs_composed.py` kept the key out
  of the base for that reason (ADR-0070).
- The measured effect of the optimized profile on the control deviation is stated beside the key in
  `configs/base_bs.yml`; it was measured on one batch of the 100 % run.

## Decision

`importance_profile: optimized_2026_06_30` lives in `configs/base_bs.yml`; the 100 % overlay no longer
sets it. The key is no longer a per-scale key (`SCALE_ONLY_KEYS` in `braunschweig/documentation/checks.py`
and the leak test). The two reuse overlays no longer repeat it. The four standalone popsim fixtures
(`config_popsim_{mid,open}_braunschweig.yml`, `config_smoke_popsim_{mid,open}_mini.yml`) set the same
profile, so no popsim config silently runs another one.

## Consequences

- **Outputs change for every popsim run composed from the base whose overlay sets no profile**: the 1 % and
  25 % scale overlays and also `test.yml`, `test_matsim.yml` and `smoke_kreis_control_fit.yml`, plus the
  four popsim fixtures. The 100 % run is unchanged. The control-fit, ownership-grid and pt-never-group smoke
  manifests of 2026-08-19/21 under `docs/runs/` (base + `smoke_kreis_control_fit.yml`) were produced with
  `uniform`; a re-run of that smoke differs by the profile, not only by whatever else changed. A smoke of the 1 % or 25 % run is still due; results of
  such runs from before this change were drawn with `uniform` and must not be compared to later ones
  without saying so.
- No stale `uniform` batch is reused. The profile rewrites the `importance` column of the controls frame
  (`braunschweig.popsim.control_spec.apply_importance_profile`), and that frame is part of the work-dir
  batch signature (`braunschweig.popsim.stage.batch_cache.compute_batch_config_signature`), so the first
  run in an existing work directory purges its completed batches and recomputes PopulationSim in full.
  Checked on 2026-11-16 with the catalog controls of the base (tiers 0 to 3, MiD seed): the profile changes
  the importance of 36 of 46 controls. The stage also declares the key to synpp, so its cache recomputes too.
