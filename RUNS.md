# RUNS — simulation run ledger

> One row per simulation/synthesis run. **Add a row at every `/close`** when a run happened.
> Fields that are not recoverable from a committed source are marked `unknown` (no invented
> values — CLAUDE.md). "Validated against" names the observed reference a result was compared
> to; convergence (mode shares stabilising) is **not** validation.
>
> Backfilled 2026-06-28 from `docs/runs/*`, `SESSION_LOG.md`, and the feature docs.

| run_id | date | config | sampling | env | features | MATSim iters | validated against | output / cache | status | source |
|---|---|---|---|---|---|---|---|---|---|---|
| 100pct-2026-06-06 | 2026-06-06 | `config_server_braunschweig_100pct.yml` | 100% (~1.13 M persons) | server (64c/125GB) | cordon, own java jar, gtfs_cordon; **pre-freight** (freight added 06-12) | 100 (last_iteration 99) | run-level MiD validation (post-run); not freight-calibrated | `cache_bs_100pct` | completed (older code) | `docs/runs/2026-06-06_100pct_run_monitor.md` |
| popsim-smokes-2026-06-11 | 2026-06-11 | `config_smoke_{simple_ipf,popsim_mid_mini,popsim_open_mini}.yml` | 1% mini | local | three population methods (regression) | n/a (synthesis) | fast suite 1504 passed; three-case comparability (BS city cells) | mini caches | completed | `docs/runs/2026-06-11_popsim_bugfix_wave.md` |
| 25pct-allfeat-2026-06-08 | 2026-06-08 (approx) | `config_server_braunschweig_25pct_allfeat_popsim.yml` | 25% | server | popsim_mid + fleet + income/cars/tenure + cordon (all-features) | unknown | MiD P13 (commute EMD ~0.065), MiD W12 (secondary); Zensus controls | `cache_bs_25pct_allfeat` / `output_bs_25pct_allfeat` | completed | `SESSION_LOG.md`, calibration feature docs |
| 1pct-allfeat-full-2026-06-22 | 2026-06-22 | `config_local_braunschweig_1pct_allfeat_full.yml` | 1% | local | popsim_mid + fleet v2 + income-age + employment grid + cordon + ALKIS homes + freight | 10 | wiring/convergence smoke only (mode choice OFF — NOT behavioural validation) | `cache_bs_1pct_allfeat_full` | completed (smoke) | `docs/runs/2026-06-22_1pct_allfeat_full_smoke_findings.md` |
| 25pct-parking | unknown | `config_local_braunschweig_25pct_parking.yml` | 25% | unknown | urban parking (BS inner ring) | unknown | MiD validation report (parking vs no-parking comparison) | `output_bs_25pct_parking` | referenced in `run-analysis` docs; date unknown | feature docs |

## Open / planned runs (see PROJECT_BACKLOG.md)

- **100% production run on newest code** (all current features; Tier-A/B caching makes it
  affordable). The last live 100% run (above) used older, pre-freight code.
- A **25% all-features ON re-run** (`cache_bs_25pct_allfeat`) is the gate for the deferred
  secondary-distance W12 after-state and scorer-weight activation (see backlog).
