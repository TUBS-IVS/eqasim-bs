# ADR-0172 · 2026-10-30 · In-commuter days are re-drawn into the simulated day; pt stations are re-drawn and the pt share is re-balanced with timetable-connected agents

- **Status:** accepted (implemented on branch `fix/i467-incommuter-day-bound`; no run has measured it yet; tests and a smoke are not a validation)
- **Numbering:** 0172 checked free across all local and remote branches on 2026-10-30.
- **Issue:** #467 (work package D, in-commuters). **Relates to:** ADR-0146 (the simulated day = `MAX_PLAN_TIME_SECONDS`),
  ADR-0153 (timetable check of the pt days; its rejected option "choose another home station" is overturned here),
  ADR-0154 (day-end margin and the whole-day shift), ADR-0029 (mode balancer), ADR-0120 (donor demographics and time chronology).
- **Amends:** ADR-0146 and ADR-0153 (status lines point here).
- **Owner decisions:** spec of #467, resolution 3 (2026-10-22): re-draw the home station against the timetable before
  any mode switch, and re-balance the pt share with timetable-connected agents only; principle "everything realistic".
  ADR-0153 (2026-10-23): the departure shift comes before a mode switch.

## Context

- Donor times of in-commuters (and of student in-commuters) can make a day end after the simulated day. Before this
  decision only the population writer moved such plans (ADR-0154), and for the student stage nothing bounded them
  before the writer.
- The pt chain of ADR-0153 resolved an unserved pt day by a departure shift and then by switching the agent to car;
  the switch lowered the realised pt share of the in-commuters below the Mikrozensus draw and nothing re-balanced it.

## Decision

1. **Day bound by re-draw.** The days of in-commuters and student in-commuters are re-drawn from the donor pool
   (`braunschweig.data.cordon.plans.redraw_times_within_bound`) until the chain ends within the effective bound
   (`MAX_PLAN_TIME_SECONDS` minus the day-end margin when enabled, the same bound as the pt check; a direct caller of
   `build_incommuter_frames` that passes both a `time_bound_s` and a `PtCheck` gets `min(time_bound_s, pt_check.bound_s)`,
   so a later default can never leave the days between the two bounds unmoved: the #488 trap). Persons and
   demographics follow the final donor. A missing end time counts as fitting (it is imputed later). Re-draws are
   limited by `braunschweig.incommuters.time_redraw_max_attempts`; the remainder is counted (`n_unfit`).
2. **Last resort: the whole-day shift of ADR-0154**, which now always runs after the imputation (not only with the pt
   check) and keeps all durations; counted and logged (WARN above 5 % moved or any day still late).
3. **pt chain.** Order: `connection_found` -> `day_shifted` -> `station_redrawn` -> `to_car` -> `kept_no_connection`
   (the owner order of ADR-0153 is kept; the station step sits after the shift and before the mode switch). The station
   re-draw applies to far agents only (a real in-ring origin has a Gemeinde point as home, not a station); up to
   `braunschweig.incommuters.pt_station_candidates` - 1 other stations of the agent's Kreis in weight order are probed,
   both trips (home station -> work at the recomputed home departure, work -> station at the work departure), with
   the same probe and connection rule as ADR-0153; the first connected station wins.
4. **Iterative pt-share re-balancing** (`mode_balancer.rebalance_with_timetable`, step 4c of the builder): the
   balancer's draw stays iteration 0; while the realised pt count is below `n_pt_target * (1 - tolerance)`, car agents
   that may board pt, were not switched by the chain and were not tried before are promoted (weighted by pt propensity,
   as the balancer does), run the chain without `to_car`, and are kept only when connected (found, shifted or re-drawn
   station); unconnected ones stay car and leave the pool. The re-balancing is global over all in-commuters, as
   `balance_incommuter_modes` is today (not per Bundesland), and is skipped without any rng draw when `mode_balance`
   is off, no pt check is configured, or the deficit is within the tolerance.
5. **Config** (all four are ASSUMPTIONs, not calibrated; the table gives the module defaults chosen by this
   decision, the values a run uses are the ones in `configs/base_bs.yml`, the only place to retune them):

   | key | default | meaning |
   |---|---|---|
   | `braunschweig.incommuters.time_redraw_max_attempts` | 20 | donor re-draws per agent (also read by the student stage) |
   | `braunschweig.incommuters.pt_station_candidates` | 5 | stations tried per far pt agent (1 disables the re-draw) |
   | `braunschweig.incommuters.pt_share_tolerance` | 0.01 | relative tolerance of the pt share |
   | `braunschweig.incommuters.rebalance_max_iterations` | 10 | promotion rounds (0 disables) |

6. **Counters and report keys.** `redraw_times_within_bound` stats: n, n_redrawn, n_attempts_max, n_unfit. Frame
   `day_bound` (in-commuter and student frames, always present, with and without a check): those four stats plus
   `bound_s`, `n_days_moved` and `n_still_late`. Frame `pt_connection`: actions (now incl. `station_redrawn`), `n_station_candidate_days`, median shift, days moved before
   the bound. Frame `pt_rebalance`: n_pt_target, n_pt_start, n_pt_final, n_iterations, n_promoted,
   n_promoted_unconnected, residual, met, `promotion_actions`, `n_promotion_without_station`, tolerance,
   max_iterations, and the router load: `n_router_probe_calls`, `n_router_probe_tasks` and `router_probe_rounds`
   (calls and routed trips per round; a round costs 1 + shift offsets + 1 station probe), also logged per round and in
   total. One log line each with rates (WARN when the target is missed, when more than 5 % of the promoted have no
   connection or no station, or when `station_redrawn` exceeds the warn share). The 5 % WARN thresholds are fixed log
   constants, not config keys, except the `station_redrawn` WARN, which uses the configurable
   `braunschweig.incommuters.pt_no_connection_warn_share`.

## Rejected options

- **Clamp the day to the bound:** it shortens the donor's work duration; the shift keeps durations (ADR-0154).
- **Rely on the population writer alone:** it moves the day after the pt check and the timetable probe, so the probed
  times are not the written ones; the student stage would stay unbounded before the writer.
- **Keep "choose another home station" rejected (ADR-0153):** overturned. ADR-0153 judged the redraw a larger change
  and not needed when a shift or the car resolves the day; the owner principle "everything realistic" (spec
  resolution 3) prefers a connected station to a mode switch, so far agents try other stations first.
- **Per-Bundesland re-balancing:** the spec asks for the balancer's current behaviour, which is global; a per-Land loop
  would be an extension.

## Consequences

- Output change by design: in-commuter and student days that ended after the bound are re-drawn or moved; some pt
  in-commuters stay pt at another station instead of switching to car; the pt share is re-balanced upward from
  the chain's `to_car` losses. Runs with the pt check enabled differ from earlier runs.
- No observed reference of the realised pt share of in-commuters exists in the repo: the re-balancing restores the
  balancer's own target (the Mikrozensus draw), it does not match an observation. That the target is met (`met`) is
  convergence of the re-balancing, not validation; a smoke is not a validation.
- The four keys are assumptions; a high `station_redrawn`, unconnected-promotion or unfit rate is surfaced by WARN
  and signals a broken primary method rather than a result.
