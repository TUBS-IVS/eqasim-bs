# ADR-0094 · 2026-08-21 · `carla_sample` as the default secondary chain solver

- **Status:** active
- **Context:** Secondary activity locations are placed by the `chainsolvers`
  package via `braunschweig.synthesis.locations.secondary_chainsolvers`. The
  solver is selectable through `braunschweig.chainsolvers.solver`, and the
  installed package registers `carla`, `carla_sample`, `carla_plus`, `dp_full`,
  `dp_rings`, `dp_carla`, `dp_rings_refine`, `dp_carla_refine`, `dp_carla_pot`,
  `dp_sample` and `milp`. Until now the choice was the deterministic `carla`,
  and it was expressed as three separate `"carla"` string literals inside the
  package (the declared config default plus two call-site fallbacks, one of them
  in the parallel worker); `configs/base_bs.yml` did not mention the key at all,
  so the active solver was invisible in the canonical configuration.

  Two things motivate a change. First, the CARLA author recommends the sampling
  variant. Second, this model's own toggle diagnostic of 2026-08-12 (issue #257)
  measured that desired-distance layers barely propagate into realised distances
  under the deterministic path: per-type escort distance layers were used as the
  desired distance for 100 % of legs, yet realised distances moved by less than
  0.3 km in the mean (11.17 vs 10.91 km) and the share below 2 km by 0.1 pp.
  Chain anchors and candidate geography dominate the greedy `top_n` pick, which
  leaves every feature whose mechanism is "shift the desired-distance
  distribution" largely inert -- a class that includes the shop / leisure / other
  subtype distance layers (#127 / #128), whose realised effect was never measured.

- **Decision:** Use `carla_sample` as the default secondary chain solver, and
  express that default ONCE, in
  `braunschweig.synthesis.locations.secondary_chainsolvers.solver_defaults.DEFAULT_CHAIN_SOLVER`.
  The declared config default, the serial call site and the parallel worker all
  read that constant; the key is additionally written explicitly into
  `configs/base_bs.yml` so the active choice is visible where the run is defined.

  - **One constant, not three literals.** The previous shape allowed the serial
    path and the parallel worker to drift to different solvers silently, which
    would make a parallel run and a serial run incomparable while both still
    looked healthy. A test pins the constant's value, a second pins that it is a
    name the installed package actually registers (so a typo or an upstream
    rename fails in seconds instead of deep inside a multi-hour run), and a third
    pins that the worker carries no literal of its own.
  - **Own module for the constant.** Submodules of this package must not import
    from its `__init__` (the #267 split constraint), and both the `__init__` and
    `parallel_solving` need the value, so it lives in a sibling module. It is
    registered in `_HELPER_MODULES` like every other submodule, so a change to it
    devalidates the stage cache instead of silently reusing a stale population.

- **Evidence status -- ADOPTED BUT UNVALIDATED.** This is the honest core of the
  record. The author's recommendation is a PERSONAL COMMUNICATION with a paper in
  preparation; it is not a citable published result, and it must not be reported
  as one. No A/B of `carla_sample` against `carla` has been run in this model:
  neither runtime, nor placement quality, nor whether the sampling actually
  restores desired-to-realised distance coupling has been measured here. The #257
  diagnostic that motivates the change was run on the DETERMINISTIC solver and
  says only that the deterministic path is inert -- it says nothing about how well
  the sampler performs. Until a run manifest records that comparison, every result
  produced with this default inherits the caveat.

- **Consequences:** Secondary locations change in every future run, so results are
  not comparable across this boundary; a cached population produced before it must
  not be mixed with one produced after (the stage cache token covers the change,
  see above). Being a sampler, the solver draws from the seeded chainsolvers RNG:
  reproducibility still holds for a fixed seed and worker count, but the run-to-run
  variance of secondary placement may differ from the deterministic path, which
  matters when reading small A/B effects elsewhere in the model.

- **Rejected alternatives:**
  - **Keep `carla` and raise `secondary_scorer_dist_dev_weight`, or set
    `secondary_scorer_selection: mnl`.** Both are config-only knobs already wired,
    and `mnl` in particular makes the selection probabilistic without changing the
    solver. They stay available and are the cheaper experiments, but they tune the
    scoring of a solver whose selection step is the thing measured to be dominant;
    the recommendation is specifically for the sampling solver.
  - **`dp_sample`.** The generative MNL sampler noted alongside `carla_sample` in
    the #257 follow-up. It is a different solver branch (`DpConfig` rather than
    `CarlaConfig`), so the existing carla-specific selection parameters this stage
    builds would need review before it could be adopted safely. Not chosen now;
    still open as a future experiment.
  - **Leaving the default in code only.** Rejected: the canonical resolved config
    is this project's active-state truth, and a solver that changes every
    secondary location must be readable there, not only in a module constant.

- **Issue:** #337
