# ADR-0155 · 2026-10-23 · Every agent chooses its modes once at the first replanning

- **Status:** accepted (implemented; measured in the 1 % rerun of the #465 fixes, see the status line at the end)
- **Numbering:** 0155 checked free across all local and remote branches on 2026-10-23 (0153/0154/0156/0157 are the
  sibling branches of #465).
- **Issue:** #465 (mechanism 4 of the 68 stuck residents of run `i465-wpe-smoke-1pct-2026-10-23`).
  **Owner question (2026-10-23):** "kann man bei DMC einstellen, dass jeder einmal neu gemacht wird?"

## Context

- eqasim configures the discrete mode choice "in the loop" (`DiscreteModeChoiceConfigurator.configureAsModeChoiceInTheLoop`,
  weight 0.05) next to `KeepLastSelected` (0.95), with `maxAgentPlanMemorySize` 1 and no innovation switch-off: in every
  iteration each agent chooses again with probability 0.05, and a plan is never selected by its score.
- After the 86 replanning rounds of run `i465-wpe-smoke-1pct-2026-10-23` (terminated by the mode-share criterion at
  iteration 86; convergence, not validation) about 0.95^86 = 1.2 % of the residents had never chosen and still used
  the synthesis mode. In the final plans 49 in-region persons had a walk trip over 5 km (79 trips; felix
  `~/wt-i487/analysis/long_walks_final_run1.txt`), 34 of them with a mode sequence unchanged from the prepared plan
  (`replanned_run1.txt`). Example: person 834024 walked 4 x 28 km (6.3 h each) between home and an education location
  22 km away, a 5-minute survey walk whose destination the education model placed at a university; the batch pt
  router finds a pt connection for that trip at every hour 6-16 h.
- The mode-share termination criterion cannot see this: the shares are stable long before every agent has chosen.

## Decision

1. The prepare stage adds to the final config (after the cut and the freight injection, which rewrite it), for every
   subpopulation with the in-the-loop DiscreteModeChoice strategy (`disableAfterIteration` -1), a second
   DiscreteModeChoice strategy with weight `braunschweig.initial_mode_choice.weight` (default 1e6) and
   `disableAfterIteration` 1 (`braunschweig.matsim.initial_mode_choice.add_initial_mode_choice`). MATSim sets its weight
   to 0 from iteration 2 on, so it acts in the first replanning only, where `(1e6 + 0.05) / (1e6 + 1)` = 99.9999 % of
   the agents choose; afterwards the loop runs as before. Subpopulations without the mode choice (freight) are untouched.
2. The config is edited as text (XML declaration, DOCTYPE and line endings kept); an identical strategy already present
   is left alone; a config without the in-the-loop strategy is refused. The share per subpopulation is logged.
3. Switch `braunschweig.initial_mode_choice.enabled` (default true), pinned with the weight in `configs/base_bs.yml`.
   The weight is not a behavioural parameter; it only sets how close to 100 % of the agents choose in the first replanning.

## Rejected options

- **eqasim's standalone mode choice before the simulation** (`mode_choice: true` of the vendored prepare): replaces the
  population routing of the prepare stage (`RunPopulationRouting`, with the walk-fallback stamp of #465/#487) by the
  routing of the standalone mode choice, and runs before the cordon cut; the first replanning reaches the same goal inside
  the loop.
- **A higher in-the-loop weight:** changes the dynamics of every iteration (twice as many agents choose in each
  iteration at weight 0.1), and coverage still only grows geometrically (0.9^86 = 0.01 % never chosen).
- **Repair implausible modes in the synthesis** (walk over 5 km and so on): duplicates the mode choice with a second
  rule and does not reach the router's pt-to-walk fallbacks.

## Consequences

- Output change by design: at iteration 1 almost every agent chooses with the travel times of iteration 0 (synthesis
  modes), so the first iterations move further than before; the converged state is the same model's equilibrium. Frozen
  tours (OutsideFilter) are not affected.
- The adult education locations behind the example (age 20+ always at a university facility, survey distance ignored)
  are a separate model question; this ADR only makes the mode consistent with the location.
- **Status:** implemented; MATSim loads the edited WP-E production config (checked on felix); measured in the 1 % rerun
  of the #465 fixes (run manifest `docs/runs/`, numbers to be filled from it). A smoke, not a validation.
