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
and the leak test). The two reuse overlays no longer repeat it.

## Consequences

- **Outputs change for the 1 % and 25 % popsim runs** composed from the base plus an overlay. The 100 % run
  is unchanged. A smoke of the 1 % or 25 % run is still due; results of such runs from before this change
  were drawn with `uniform` and must not be compared to later ones without saying so.
- The work directory shared by the 1 % and 25 % overlays holds batches built with `uniform`. Whether the
  batch config signature (`_purge_stale_batches_for_changed_config`) takes the profile in through the
  control set and so recomputes on the next run is expected but not verified here.
- The standalone popsim fixtures under `configs/fixtures/` set no profile and stay on `uniform`.
