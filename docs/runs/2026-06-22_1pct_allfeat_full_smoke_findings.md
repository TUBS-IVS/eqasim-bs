# Run findings — 1% all-features full-pipeline clean smoke (2026-06-22)

> **Living document.** Updated continuously while the run progresses. Captures
> every bug (with root cause + fix) and every optimization / performance
> observation, with a concrete "how to optimize" for each.

## Run setup

- **Branch:** `run/smoke-1pct-allfeatures` (cut from `integration/all-features`
  `56b3b3d`, the combined branch that already contains popsim_mid + fleet v2 +
  income-age + employment grid + cordon + ALKIS home matching).
- **Config:** `config_local_braunschweig_1pct_allfeat_full.yml` (NEW; derived from
  `config_popsim_mid_braunschweig_population_allfeatures.yml` by switching the run
  target to the full `synthesis.output -> matsim.output -> analysis` chain,
  enabling freight, fresh cache/output).
- **Working dir:** `eqasim-data/cache_bs_1pct_allfeat_full` (FRESH, no cache reuse).
- **Goal:** verify EVERY wiring/IO boundary end-to-end at 1% with all features ON;
  popsim_mid (MiD donor, weight-sampled) + new fleet + cordon gates + freight +
  10 MATSim iterations. Mode choice OFF (this is a wiring/convergence smoke, NOT a
  behavioural calibration — convergence != validation, per CLAUDE.md).
- **Execution strategy:** phased per stage group (Phase B synthesis, C freight
  extraction, D MATSim, E analysis), so IO is checked at each boundary and a late
  failure does not waste an earlier expensive stage.
- **Interpreter:** conda env `eqasim` python, `PYTHONUTF8=1`.

---

## BUGS

### BUG-1 (precondition, not a code bug) — output directory must pre-exist
- **Phase:** B (synthesis.output).
- **Symptom:** `RuntimeError: Output directory must exist: eqasim-data/output_bs_1pct_allfeat_full`
  at `synthesis/output.py:84` `validate()`, before any execution.
- **Root cause:** `synthesis.output.validate()` deliberately fails early if the
  output dir is absent (documented in memory `dryrun-config-gaps`). Not a code bug.
- **Fix:** `mkdir -p eqasim-data/output_bs_1pct_allfeat_full` before running.
- **Status:** resolved. (Wiring win: synpp resolved all 46 stages with no
  alias/import errors -> the popsim_mid full-pipeline DAG is correctly wired.)

### BUG-2 (real bug) — popsim.stage writes into a non-existent work_dir on a fresh cache
- **Phase:** B (`braunschweig.popsim.stage`).
- **Symptom:** `OSError: Cannot save file into a non-existent directory:
  'eqasim-data\cache_bs_1pct_allfeat_full\popsim_work'` at `stage.py:664`
  `_weekend_trace.to_parquet(Path(work_dir) / "weekend_plan_match_trace.parquet")`.
- **Root cause:** `work_dir` is read from config (`stage.py:441`) but never created.
  The first writer into it is the weekend_plan_match trace (`:664`), which runs
  BEFORE the PopulationSim batch runner (`:699`) that would otherwise create
  `work_dir` implicitly (per-batch subfolders, `makedirs(parents=True)`). On a
  FRESH cache `work_dir` does not exist yet -> crash. Masked in prior runs because
  `popsim_work` already existed from earlier runs.
- **Fix:** create `work_dir` up front, right after reading it
  (`Path(work_dir).mkdir(parents=True, exist_ok=True)` at `stage.py:441`).
  Keeps the stage self-contained (CLAUDE.md: create output dirs explicitly).
- **Follow-up:** add a small regression guard (run popsim.stage with a fresh
  work_dir, assert no crash) — practical only as part of a popsim mini e2e fixture.
- **Status:** FIXED + CONFIRMED. On the re-run the exact write that crashed
  succeeded: `popsim_work/weekend_plan_match_trace.parquet` (4.4 MB) written at
  11:01, weekend_plan_match report clean (62,572 weekend HH matched, 0 fallback,
  138,777 persons remapped). Stopped here (user checkpoint) BEFORE the
  PopulationSim batches.

---

## OPTIMIZATIONS / PERFORMANCE OBSERVATIONS

### OPT-1 — no progress feedback in the file log for long pre-batch steps
- **Observation:** the live progress bars (PopulationSim batches, cell->building
  handoff) only render on a TTY. We run with stdout redirected to a log file, so
  they fall back to plain mode. Worse, the **pre-batch** steps
  (`member_completion`, `weekend_plan_match`) emit only a single summary line when
  DONE — so for ~17 min the log looks frozen although the process is computing
  (confirmed alive: ~100% of one core, CPU advancing).
- **Impact:** impossible to estimate remaining time; looks like a hang.
- **How to optimize:** emit a periodic plain-text heartbeat / progress line in
  non-TTY mode for long-running single-threaded steps (e.g. every 30 s or every
  10% of the iterrows loop in `member_completion`), gated so it does not spam.
  Reuse the existing `progress_iter` plain fallback for the iterrows loop.

### OPT-2 — member_completion is a single-threaded iterrows loop over the FULL donor
- **Observation:** `member_completion` ran ~9 min (10:22:42 -> 10:31:55) filling
  36,253/218,097 incomplete households. It iterates the full MiD donor
  (`member_completion.py:237` `for _, row in incomplete.sort_values(...).iterrows()`),
  single-threaded, and is **sampling-rate INDEPENDENT** (it runs on the donor, not
  the 1% synthetic population). `weekend_plan_match` added another ~8.5 min.
- **Impact:** ~17 min of single-core work on EVERY fresh run and at EVERY sampling
  rate; at 100% the rest of the pipeline scales but this constant cost does not
  shrink. Only 1 of 22 cores used.
- **How to optimize (two independent levers):**
  1. **Cache the completed/weekend-matched donor as its own synpp stage.** It is
     sampling-rate-independent and deterministic (seeded) -> compute once, reuse
     across runs and sampling rates. Today it lives INSIDE `popsim.stage`, so any
     downstream crash (e.g. BUG-2) re-runs all ~17 min. Splitting it out also makes
     `popsim.stage` re-runs cheap.
  2. **Vectorize / parallelize the iterrows loop.** Replace the per-row
     `.iterrows()` mirror-draw with a vectorized group-wise draw (group by
     match key, sample mirrors per group), or parallelize over independent host
     households. Mirrors the chainsolvers parallelization already in the project.

### OPT-3 — monolithic popsim.stage forces full recompute on any late crash
- **Observation:** `member_completion` + `weekend_plan_match` + seed build +
  PopulationSim batches + assembly are all inside the single uncached
  `braunschweig.popsim.stage`. BUG-2 crashed AFTER the ~17 min donor work, so the
  fix re-runs all 17 min before reaching the batches.
- **How to optimize:** decompose `popsim.stage` into cacheable synpp sub-stages
  (completed-donor -> seed -> batches -> assembly). Each becomes independently
  cached and individually debuggable; aligns with eqasim's fine-grained DAG style.
  (Overlaps with OPT-2.1.)

### OPT-4 — NumExpr capped at 8 threads (minor)
- **Observation:** `NumExpr detected 22 cores but NUMEXPR_MAX_THREADS not set, so
  enforcing safe limit of 8`.
- **Impact:** minor; affects numexpr-backed pandas ops only.
- **How to optimize:** set `NUMEXPR_MAX_THREADS` explicitly in the launch env to a
  deliberate value (paired with per-worker caps when batches run in parallel to
  avoid oversubscription, as the config comments already note).

---

### ENH-1 — home_cell fallback for empty cells is commune-wide, discarding Zensus locality
- **Question raised:** ~96.9% of households land in their exact Zensus 100m cell
  (CLAUDE.md, prior run). Where do the other ~3.1% go, and why?
- **Mechanism (verified in `home_cell.py:271-313`, `place_households_in_cells`):**
  per household, three exhaustive outcomes, counted + logged (warning if any
  fallback/unplaced — no silent fallback):
  1. **PRIMARY (in-cell):** the household's 100m cell contains >=1 eligible
     building -> weighted draw within that exact cell. (~96.9%)
  2. **FALLBACK (commune-wide):** the cell has NO eligible building -> area-weighted
     draw among ALL buildings of the household's OWN commune (legacy behaviour).
     This is the ~3.1%. They stay in their Gemeinde but the Zensus 100m locality is
     thrown away.
  3. **UNPLACED:** the commune ALSO has no building (should be ~0) -> household left
     unplaced (dropped), logged loudly. Never silently relocated to another commune.
- **Why a Zensus-populated cell can have NO building:** buildings are mapped to a
  cell by their **centroid** reprojected to EPSG:3035 (`home_cell.py:241-246`).
  Causes: (a) near-boundary buildings whose centroid falls into the adjacent cell;
  (b) the building set (`braunschweig.data.buildings`, ALKIS) is filtered to
  residential-eligible footprints, so a cell with only non-residential ALKIS
  buildings has no candidate; (c) Zensus-2022-vs-ALKIS vintage mismatch (new/removed
  buildings); (d) a large building spanning cells counts only in its centroid cell.
- **NOTE — the typed-matching (PR #14) is a DIFFERENT axis.** "Height-typed
  buildings where no census signal" + "draw proportional to H_GEW/P_GEW" decide
  WHICH building TYPE is drawn *within* a cell that already has buildings. They do
  NOT change the in-cell vs commune-fallback split. Turning typed-matching off does
  not change the 96.9% in-cell rate.
- **How to optimize (ENH, scientific-output change -> needs sign-off):** replace the
  commune-wide fallback with a **nearest-cell ring search** (place the 3.1% in the
  closest neighbouring 100m cell that has a building, expanding the ring until one
  is found) so Zensus locality is preserved for them too, instead of an
  area-weighted draw across the whole Gemeinde. Add a diagnostic that quantifies,
  for ZGB, how many Zensus-populated cells lack an eligible building and why
  (centroid-edge vs coverage vs vintage), so the residual is understood not just
  worked around.

## IMPLEMENTED changes (2026-06-22, branch run/smoke-1pct-allfeatures)

All verified by targeted tests (112 passed, 2 skipped) before re-running.

- **BUG-2 fix** — `popsim.stage` creates `work_dir` up front ([stage.py:441](braunschweig/popsim/stage.py#L441)).
- **OPT-2 + OPT-3 (member_completion)** — pre-group the complete-household pool by
  (size[, day type]) ONCE instead of scanning the full frame per host. O(n_incomplete
  x n_complete) -> O(n). **BYTE-IDENTICAL** (same candidate sets, same seeded draw;
  21 member_completion tests green). MEASURED on the 1% re-run: 553 s -> ~184 s
  (~3.0x). NOT "-> seconds" (an earlier note overstated this): the candidate-filter
  was only part of the cost. The REMAINING bottleneck is the per-household pandas
  work (iterrows + `_select_mirror` + `_match_present_members` + per-household
  filler concat), which the O(n) fix does not touch.
  ([member_completion.py](braunschweig/popsim/member_completion.py))
- **OPT-2/3 (weekend_plan_match)** — same fix: pre-group the weekday pool on the hard
  keys (size, regiostar7). BYTE-IDENTICAL (weighted_choice draws over sorted ids;
  weekend tests green). MEASURED: ~511 s -> ~300 s (~1.7x). The dominant REMAINING
  cost here is the per-person `persons.loc[ridx, ...] = ...` scalar assignment over
  ~138 k remapped persons (a slow pandas anti-pattern) plus `align_members`.
  ([weekend_plan_match.py](braunschweig/popsim/weekend_plan_match.py))
- **OPT-5 (follow-up, NOT done)** — to cut the residual ~3 min + ~5 min single-core
  pre-batch cost further: vectorize the filler assembly (member_completion) and
  replace the per-person `.loc` scalar writes with a single vectorized assignment
  (weekend_plan_match), or parallelize per independent host. Deferred: it rewrites
  the seeded mutation path, so it needs an explicit byte-identity / determinism
  check before merging.
- **OPT-1 (progress)** — plain-text progress heartbeats (~10 per loop) in both
  member_completion and weekend_plan_match, so a non-TTY file log shows progress
  instead of looking frozen.
- **ENH-1 (home_cell neighbour fallback)** — a household whose own 100m cell has no
  building is now snapped to the spatially nearest building in a NEIGHBOURING cell
  (ring search up to `NEIGHBOUR_MAX_RING=3`) before falling back to a random in-cell
  point; the in-cell point is still drawn so the RNG stream stays aligned (all other
  cells byte-identical). The neighbour-snap count is in `TypedHomeReport`
  (`n_neighbour_cell_placed`) and logged. Tests updated to the new contract + an
  isolated-cell fallback test added (5 home_match tests green).
  ([home_cell.py](braunschweig/synthesis/locations/home_cell.py))
  NOTE: this changes the home location of the small zero-building subset (a
  deliberate, user-approved fidelity improvement -- NOT byte-identical for them).
- **F2 (core-scaling)** — new `braunschweig/parallelism.py` (`resolve_workers`):
  `num_workers` / `chainsolvers.processes` honour an auto sentinel (0/null/"auto" ->
  cpu_count - 2), so the parallel side-processes scale with the box. Explicit positive
  integers are used verbatim (existing configs byte-identical). The 1% run config now
  sets both to 0 (auto). Resolved counts are logged.
  CAVEAT (tracked, not yet done): when many worker SUBPROCESSES run, each inheriting
  BLAS/numexpr thread pools can oversubscribe cores. The principled fix is a per-worker
  thread cap (e.g. cpu_count // num_workers) set in the batch-runner subprocess env.
  OPT-4 (NUMEXPR cap warning) folds into this. Deferred to avoid a wrong global env
  setting that slows the BLAS-heavy single-process stages.

## COMPREHENSIVE PRE-SERVER ANALYSIS (2026-06-22, run aborted in popsim batches)

The local 1% run was deliberately ABORTED inside the popsim batches (the heavy run
belongs on the 64-core SSH server). This section is the systematic log review done
in preparation. Scope reached: full data layer + popsim donor build + member
completion + weekend match + ~20/33 PopulationSim batches (partially). home_cell /
fleet / MATSim / freight / cordon were NOT reached locally.

### Errors / crashes
- After BUG-1 + BUG-2 fixes: **no Python errors, no crashes**. All `WARNING`s are
  benign (osmconvert version hint, OLE2/xls reader note, one pandas
  SettingWithCopyWarning in `eqasim_common/spatial/codes.py:34`, pyogrio GPKG
  extension note). None affect results.

### Fallback rates ("how often is which fallback triggered") — the requested audit
| Stage | Primary | Fallback | Rate | Verdict |
|---|---|---|---|---|
| RegioStaR7 fill (`bbsr.regiostar`) | 122/123 direct | 1/123 nearest-neighbour (`03153019`) | **0.8%** | healthy, logged |
| Schools facilities | 474/477 geocoded | 3/477 PLZ-centroid | **0.6%** | healthy, logged |
| Donor seed completeness | 218,097/218,101 HH kept | 4 HH / 312 persons dropped (H_ID=0/null) | **~0%** | fail-loud, intended |
| member_completion | 36,253 incomplete filled | 0 unfillable | **100% filled** | healthy |
| weekend_plan_match | 62,572 HH-matched (99.5% at best level 0) | 0 person-fallback, 0 swept | **0% fallback** | healthy |
| **PopulationSim integerizer** | 8,167 OPTIMAL | **666 INFEASIBLE -> smart-round** (+47 simul-retry-failed) | **~7.5%** | see below |

### KEY FINDING — PopulationSim integerizer INFEASIBLE rate (~7.5% at 1%)
- For ~7.5% of zone integerizations PopulationSim's LP integerizer returns
  INFEASIBLE and falls back to "smart-rounded original weights". Concentrated in the
  rural strata (batches 014-019). The synthetic counts for those zones are therefore
  smart-rounded, not LP-optimal.
- **Cause:** a 1%-sampling artifact. At 1% a 100 m Zensus cell holds ~0-2 households,
  and the full tier0-2 + RegioStaR-stratified control set cannot be satisfied
  integer-simultaneously in such tiny cells. At 100% (server) cells hold ~100x more
  households -> the integerizer should be feasible far more often. RE-MEASURE on the
  server; expect a much lower rate.
- **Action (no-silent-fallback):** this fallback is currently INVISIBLE to our
  pipeline (it lives only in the per-batch PopulationSim logs). Recommend the
  popsim stage parse each batch's `populationsim.log`, count INFEASIBLE/total zones,
  and surface the rate in the stage log + run summary (so a high rate is a visible
  signal, not buried). FOLLOW-UP, not yet implemented.

### Convergence / performance
- Balancer is CONVERGING, not diverging (batch_000 `max_gamma_dif` 14.3 -> 8.9 ->
  4.2 over iterations). Slow because `batch_timeout_s: 0` runs each batch to full
  convergence with a heavy control set.
- **Dominant local cost = the 33 PopulationSim batches** (first wave of 20 not done
  after ~50 min). On the server (64 cores + `num_workers: 0` auto -> ~62) the 33
  batches run in ~1 wave instead of 2, and per-batch convergence has more cores.
- Donor steps (member 3.1 min, weekend 5.0 min) are single-threaded; fine as a
  one-time cost, OPT-5 (vectorize the per-row mutations) would cut them further.

### Server-run preparation checklist
1. Use `integration/all-features` (or this run branch merged) so all fixes are in:
   BUG-2 work_dir, member/weekend O(n) + progress, ENH-1 home neighbour, F2 auto-workers.
2. Server config: `num_workers: 0` + `chainsolvers.processes: 0` (auto -> 62), keep
   `batch_timeout_s: 0` (cores make full convergence affordable), `java_memory` to the
   128 GB box.
3. CREATE the output dir before running (BUG-1) or add a `mkdir` in the launch script.
4. On the server run, capture and report: the integerizer INFEASIBLE rate (expect
   << 7.5%), the `[home_typed]` neighbour-snap counts (ENH-1), and the cordon/freight
   injection wiring (not reached locally).

## Wiring / IO checks PASSED so far (positive confirmations)

- synpp resolved all 46 synthesis.output stages — popsim_mid full DAG wired OK.
- MiD donor active (`active donor source: mid`); 1,087,393 MiD trips loaded;
  secondary distance distributions built for 5 modes.
- Seed completeness: 218,097/218,101 donor households kept (100%), 420,667 persons;
  312 invalid (H_ID=0/null) persons dropped fail-loud.
- member_completion: 16.6% incomplete, 100% filled by weight-proportional mirror
  sampling, +65,042 persons.
- weekend_plan_match: 62,572 weekend households matched (kernwo day-type aware).
- Data layers loaded sane: ALKIS 943,668 buildings; schools 477 (capacity by
  level plausible); pendler 48,340 flows; landuse 470,458 polygons; OSM 22,011 POIs;
  RegioStaR7 fill primary 122/123 (99.2%), 1 nearest-neighbour fallback (logged).
