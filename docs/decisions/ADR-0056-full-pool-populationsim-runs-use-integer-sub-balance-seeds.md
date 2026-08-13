# ADR-0056 — Full-pool PopulationSim runs use integer sub-balance seeds + numba (measured 40x; float-seed default is a full-pool trap)

> Numbering: 0055 is taken on `origin/main` (SrV ZENSUS-weight fix); this ADR takes 0056.

- **Date:** 2026-07-10 · **Status:** accepted (quality A/B vs float reference PENDING)
- **Problem:** the kreis5 100% popsim campaign (full national donor pool, `stratify_regiostar=false` —
  deliberate quality decision, per-stratum donors fit worse) projected ~8 days for 30 batches. Measured
  root cause (batch_000 live logs + pipeline.h5 inspection + shape-exact micro-benchmark on felix): with
  upstream default `SUB_BALANCE_WITH_FLOAT_SEED_WEIGHTS: true`, parent-level float weights are strictly
  positive for EVERY household in EVERY zone (observed down to 1e-248), so the sub-balancer's
  `weight > 0` filter never drops rows — every 1km cell (~148 households) balanced all 53,459 signature
  rows x 1000 iterations (~741 s/parent; python balancer benchmarked at 741 ms/iter at exactly this
  shape). The balancer can never converge-exit at fine geographies (requires `max_gamma_dif < 1e-5`,
  observed 7–190), so the 1000-iteration ceiling is always paid in full.
- **Decision:** for full-pool runs, the popsim settings file
  (`settings_tier3_mef100_intseed_numba.yaml` on felix, referenced by `config_run_kreis5_100pct.yml`) sets
  (1) `SUB_BALANCE_WITH_FLOAT_SEED_WEIGHTS: false` — sub-balances seed from the parent's INTEGER weights
  (mean 143 positive rows per 1km parent instead of 53,459; upstream's own code comment supports this:
  "using balanced_weight slows down simul and doesn't improve results"), and
  (2) `USE_NUMBA: true` — measured 2.4x/iteration, numerically identical to 1e-13, `cache=True`.
  Iteration capping (`MAX_BALANCE_ITERATIONS_SIMULTANEOUS`) was considered and REJECTED: the importance
  schedule decays every 100 iterations, so truncation changes results; unnecessary after lever 1.
- **Measured evidence (A/B, batch_000 copy, felix, 2026-07-10):** full batch in **1,958 s (32.6 min)** vs
  the float production run's >14 h unfinished (~22 h projected) = ~40x. 1km household totals fit EXACTLY
  in both regimes (93 zones, 13,767 HH, zero deviation). The stopped float run was relaunched 14:11 with
  the new settings; ~28 min/batch confirmed in production.
- **Honest limitation:** integer seeds change the donor-selection cascade, i.e. results differ from the
  float regime. The fine-grained quality comparison (100m composition, donor diversity, person marginals)
  runs against a dedicated float reference batch (`bench_batch_float` on felix, done ~2026-07-11); until
  that comparison is clean, the speedup is operational, not scientifically validated.
- **Related:** per-batch `pipeline.h5` is ~15 GB at full pool (12.1 GB = dense `ZENSUS100m_weights`,
  ~215 M rows, 99.9% zeros) and verified dead after batch completion — interim server watcher deletes it;
  permanent stage.py flag tracked as **issue #153** (fix shipped 2026-07-10 as **PR #155**, default-ON
  `cleanup_batch_pipeline`; merge pending the felix pytest after the run). Two verified upstream bugs (populationsim v0.10.0):
  missing `MIN_GAMMA` clamp in the python single balancer (NaN risk; `balancers.py:84-89`) and hardcoded
  `converged=True` on no-progress exits (`balancers.py:111-112`) — both bypassed by `USE_NUMBA: true`.
- **Evidence:** felix `~/wt-kreis-run/logs/run_kreis5.log` (stop/relaunch markers), A/B outputs
  `~/bench_batch_int/output/timing_log.csv`, benchmark `~/bench_balancer.py`, watcher
  `~/cleanup_batch_h5.log`; issue #153; memory `project-popsim-fullpool-perf-fix`.

---

