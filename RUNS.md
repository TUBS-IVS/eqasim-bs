# RUNS — simulation run ledger

> One row per simulation/synthesis run. **Add a row at every `/close`** when a run happened.
> Fields that are not recoverable from a committed source are marked `unknown` (no invented
> values — CLAUDE.md). "Validated against" names the observed reference a result was compared
> to; convergence (mode shares stabilising) is **not** validation.
>
> Backfilled 2026-06-28 from `docs/runs/*`, `SESSION_LOG.md`, and the feature docs.

| run_id | date | config | sampling | env | features | MATSim iters | validated against | output / cache | status | source |
|---|---|---|---|---|---|---|---|---|---|---|
| empstatus-control-smoke-2026-07-14 | 2026-07-14 | `config_smoke_bs_on/off.yml` (felix `~/wt-empctrl` @ `453cd59` feature/srv-employment-status-control) | 1 Kreis (Braunschweig 03101), max_cells 750, 8 workers | server | SrV+MiD per-Kreis employment_status CONTROL smoke (PR #173). region-restricted + `cache_share_recompute:[]` (completed_donor restored) so seed prep is cheap; 8 batches parallel. | n/a (popsim balancing smoke) | **control rakes in_ausbildung(14+) 2.98% (unraked kreis5) -> 2.09%, target 2.01%**; 14+ universe confirmed (total persons age>=14) | (ephemeral; scratch removed) | completed | ADR-0060; PR #173; memory `feedback-popsim-smoke-scoping` |
| empstatus-measure-2026-07-13 | 2026-07-13 | `config_empstatus_measure.yml` (felix `~/wt-empstatus` @ kreis5 `66cca1c` + employment_status delta; worktree since removed) | 100% (1,124,108 persons, 8 Kreise) | server | employment_status Phase-0 validation vs MiD P9. **Reused kreis5 balancing** (60/60 batches, no re-balance — signature untouched); regenerated `popsim.stage` only (source-comment hash bump); measured directly off cached persons frame (no chainsolvers/MATSim). | n/a (attribute measurement) | **MiD 2023 P9 (independent): SRMSE 0.194, mean\|Δ\| 1.88pp, grade good, 100% cells <10pp, r²=0.979** | (no output written; ephemeral) | completed | ADR-0058; scratchpad `measure_empstatus.py`; memory `project-employment-status-and-pbkat-bugs` |
| 100pct-kreis5-2026-07-10 | 2026-07-10 | `config_run_kreis5_100pct.yml` (felix `~/wt-kreis-run` @ `b6ba420` main) | 100% (~1.13 M persons, 558k HH) | server (64c/128GB) | popsim_mid full pool (stratify=false) + tier0-3 + employment_grid + 5 KREIS controls; popsim settings `settings_tier3_mef100_intseed_numba.yaml` (**SUB_BALANCE_WITH_FLOAT_SEED_WEIGHTS=false + USE_NUMBA=true**, ADR-0056) | synthesis running (popsim ~28 min/batch, ETA same day) | quality A/B vs float reference batch PENDING (2026-07-11); 1km HH totals exact | `cache_bs_100pct_allfeat_popsim` / `output_bs_100pct_allfeat_popsim_kreis5` | **running** (float predecessor stopped 2x: 07-09 trip_class audit, 07-10 perf regime) | ADR-0056; `logs/run_kreis5.log`; memory `project-popsim-fullpool-perf-fix` |
| 100pct-2026-06-06 | 2026-06-06 | `config_server_braunschweig_100pct.yml` | 100% (~1.13 M persons) | server (64c/125GB) | cordon, own java jar, gtfs_cordon; **pre-freight** (freight added 06-12) | 100 (last_iteration 99) | run-level MiD validation (post-run); not freight-calibrated | `cache_bs_100pct` | completed (older code) | `docs/runs/2026-06-06_100pct_run_monitor.md` |
| popsim-smokes-2026-06-11 | 2026-06-11 | `config_smoke_{simple_ipf,popsim_mid_mini,popsim_open_mini}.yml` | 1% mini | local | three population methods (regression) | n/a (synthesis) | fast suite 1504 passed; three-case comparability (BS city cells) | mini caches | completed | `docs/runs/2026-06-11_popsim_bugfix_wave.md` |
| 25pct-allfeat-2026-06-08 | 2026-06-08 (approx) | `config_server_braunschweig_25pct_allfeat_popsim.yml` | 25% | server | popsim_mid + fleet + income/cars/tenure + cordon (all-features) | unknown | MiD P13 (commute EMD ~0.065), MiD W12 (secondary); Zensus controls | `cache_bs_25pct_allfeat` / `output_bs_25pct_allfeat` | completed | `SESSION_LOG.md`, calibration feature docs |
| 1pct-allfeat-full-2026-06-22 | 2026-06-22 | `config_local_braunschweig_1pct_allfeat_full.yml` | 1% | local | popsim_mid + fleet v2 + income-age + employment grid + cordon + ALKIS homes + freight | 10 | wiring/convergence smoke only (mode choice OFF — NOT behavioural validation) | `cache_bs_1pct_allfeat_full` | completed (smoke) | `docs/runs/2026-06-22_1pct_allfeat_full_smoke_findings.md` |
| 25pct-parking | unknown | `config_local_braunschweig_25pct_parking.yml` | 25% | unknown | urban parking (BS inner ring) | unknown | MiD validation report (parking vs no-parking comparison) | `output_bs_25pct_parking` | referenced in `run-analysis` docs; date unknown | feature docs |

## Runs on the run server (discovered 2026-06-28 via SSH; read-only)

Authoritative run artifacts live on the Linux run server under
`/home/felix/eqasim-bs/eqasim-data/`. The following exist there (date = directory mtime,
size = `du -sh`). Server git HEAD at discovery: `e1164cc` (2026-06-23) — **behind**
`origin/main` (`381b6a4`). Several fields are not safely recoverable: the per-run
`*_meta.json` is inconsistent (reads `sampling_rate 1.0` / `hts entd` even in a `25pct`
popsim directory — looks like a default template, not the real run state), so sampling/hts
below are taken from the **directory name** and flagged where the meta disagrees.

| artifact | date | size | what it is (from dir name) | notes |
|---|---|---|---|---|
| `cache_bs_100pct_allfeat_synth` | 2026-06-27 | 11G | **100% all-features synthesis** cache (newest) | synthesis-only cache; no MATSim `output_*` dir found alongside |
| `cache_bs_25pct_allfeat_popsim` | 2026-06-24 | 13G | 25% all-features popsim cache | meta.json says 1.0/entd — inconsistent, verify |
| `output_bs_25pct_allfeat_popsim` | 2026-06-27 | 2.3G | 25% all-features popsim output | has `analysis/cordon`; no `mid_validation` report present |
| `cache_bs_1pct_allfeat_fit` | 2026-06-26 | 2.3G | 1% all-features fit cache | calibration-fit working dir |
| `cache_bs_1pct_allfeat_popsim` | 2026-06-22 | 3.3G | 1% all-features popsim cache | — |
| `output_bs_1pct_allfeat_popsim` | 2026-06-26 | 19M | 1% all-features popsim output | — |
| `output_bs_100pct_popsim_t3` | 2026-06-17 | 810M | 100% popsim tier-3 output | older code |
| `output_full_allfeatures` | 2026-06-17 | 828M | full all-features output | older code |

> **Note for the backlog:** a **100% all-features *synthesis*** (`cache_bs_100pct_allfeat_synth`,
> 2026-06-27) already exists on newer code — but it is a synthesis cache, not a confirmed full
> MATSim production run. The "100% production run on newest code" item should be re-scoped to
> "run/confirm MATSim on top of this synthesis", not "synthesise from scratch". Verify against the
> server before launching a fresh full run.

## Open / planned runs (see PROJECT_BACKLOG.md)

- **100% production run on newest code** (all current features; Tier-A/B caching makes it
  affordable). The last live 100% run (above) used older, pre-freight code.
- A **25% all-features ON re-run** (`cache_bs_25pct_allfeat`) is the gate for the deferred
  secondary-distance W12 after-state and scorer-weight activation (see backlog).
