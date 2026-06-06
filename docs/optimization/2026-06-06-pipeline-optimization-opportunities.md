# Pipeline optimization opportunities — prioritized impact analysis

Generated 2026-06-06 from a parallel module-by-module scan (7 read-only agents over
IPF/households, gravity/OD, secondary locations, education gravity, cordon demand,
population enrichment, MATSim+Java). Each item is evidence-backed (file:line). Two
axes: **MODELING** (make the model scientifically better) and **PERFORMANCE**
(runtime/memory at 25%/100%).

Priority key: **P0** headline / strategic, **P1** do-first quick wins, **P2**
reproducibility hazards, **P3** performance-at-scale refactors, **P4** modeling
realism (larger). Effort S(<1h)/M(hours)/L(day+). "Repro" = changes outputs, must be
a deliberate re-baseline.

---

## P0 — CORRECTED (the original "DMC is off" claim was WRONG)

The scan agent claimed mode choice was disabled. **This is incorrect** and has been
retracted. Verified 2026-06-06:

- `BraunschweigConfigurator` registers `BraunschweigModeChoiceModule`
  (`BraunschweigConfigurator.java:11`) and `RunAdaptConfig` configures the
  `DiscreteModeChoiceConfigGroup` (`RunAdaptConfig.java`) → **Discrete Mode Choice
  runs inside the MATSim loop**; the cost/parking/PT-tariff models are live, NOT dead.
- The `mode_choice` config flag is read ONLY in the upstream
  `eqasim-bs/matsim/simulation/prepare.py:114` and gates the optional **standalone
  (offline, one-shot) mode-choice pre-pass** (`--config:standaloneModeChoice...`).
  `mode_choice: false` just means that offline pre-pass is skipped — it does NOT
  disable the in-loop DMC. So the run IS a behavioural mode-choice equilibrium.

What MAY still be worth a look (separate, lower-priority, NOT a P0):

| # | Finding | Where | Impact | Effort |
|---|---|---|---|---|
| 0.2 | ASC values inherited from Munich and hand-adjusted (SP originals commented out). Worth confirming the ZGB modal split validates against MiD 2023 after a run, and whether an ASC re-fit is warranted. NOTE: needs verification against the actual post-run modal split before treating as an issue (the earlier "uncalibrated" framing came from the same over-reading). | `BraunschweigModeParameters.java:31-69` | M (if split is off) | M |

> Net: there is **no fixed-mode problem**. Treat the modal-split calibration as a
> normal post-run validation question, not a structural gap.

---

## P1 — Do-first quick wins (low effort, high/medium impact, low/no output risk)

| # | Finding | Where | Type | Impact | Effort | Risk |
|---|---|---|---|---|---|---|
| 1.1 | Build the dense flow matrix `T` **once after convergence** instead of every Furness iteration (only the matvecs are needed for the residual). Multi-GB churn ×50 iters on grundschule at 100%. | `education_gravity_model.py:29-41` | PERF | **H** (mem) | S | none |
| 1.2 | PT-mapper (pt2matsim, heaviest build step, maps Germany-wide GTFS clip) uses `processes`(32) not `matsim_threads`(48) → 16 cores idle during build. One-line. | `matsim/scenario/supply/processed.py:44` | PERF | M | S | none |
| 1.3 | Pre-index the Zensus household-type frame (`set_index(["commune_id","hh_size"])`) instead of a full boolean scan per `(commune,size)` bucket (~740 scans over the full 1.13 M-person formation). | `ipf/attributed.py:227-252, 324-332` | PERF | M | S | none |
| 1.4 | Build the RDA `CandidateIndex` (3 KDTrees over all candidates) **once** and reuse across both fallback calls; replace per-geometry `.apply([g.x,g.y])` with vectorised `.x/.y`. | `secondary_chainsolvers.py:357,368,779-783` | PERF | M | S | none |
| 1.5 | `evaluate_gravity`: drop the per-iteration `print` + the three full N×N `np.copy` snapshots, add a real `maxiter` config (currently `range(int(1e6))`). | `gravity/model.py:180-222` | PERF | M | S | none |
| 1.6 | Cordon `expand_to_agents`: replace `iterrows`+list-mult with `np.repeat` (~68k agents at 100%). | `data/cordon/demand.py:41-44` | PERF | M | S | none (verify rounding) |
| 1.7 | Cordon: memoise `extract_commute_times` per **unique** donor (replacement sampling re-sorts the same donor many times) + vectorise the `gate_entry` arithmetic. | `synthesis/incommuters.py:235-247` | PERF | **H** | M | none |
| 1.8 | Memoise `_derive_kreis_ars5` (built 3× per execute as an object-array Python loop over 1.13 M rows). | `synthesis/population/enriched.py:674-712` | PERF | M (100%) | S | none |

---

## P2 — Reproducibility hazards / latent correctness (fix deliberately)

| # | Finding | Where | Type | Impact | Effort | Risk |
|---|---|---|---|---|---|---|
| 2.1 | **`set(kreis.unique())` iteration order** drives RNG consumption order in `_sample_counts` → vehicle-count assignment is non-reproducible across runs unless `PYTHONHASHSEED` is pinned. Fix: `sorted(...)`. | `synthesis/population/enriched.py:685-698` | MODELING | M | S | repro (one-time) |
| 2.2 | **In-place mutation of cached MiD stage**: `constraints = mid["..."]; constraints.append(...)` grows the cached list; should `list(...)` copy first. | `synthesis/population/enriched.py:132-136,161-165` | MODELING | M | S | none |
| 2.3 | Distance-distribution resampling **mutates the cached stage object in place** → double-resampling if two consumers run (legacy + chainsolvers). Deep-copy first. | `secondary_chainsolvers.py:141-144,760` | MODELING | M | S | none |
| 2.4 | Cordon PT timing uses the **gate→work** distance, but the PT agent's home was moved to the entry stop afterwards → departure-time distance inconsistent with boarding point (latent bug). | `synthesis/incommuters.py:146 vs 153,158` | MODELING | M | S | repro |
| 2.5 | Cordon: silent **whole-ZGB workplace fallback** when a dest Kreis is absent/format-mismatched (`by_kreis.get(dest, w)`) — violates the "no silent assumptions" rule; should warn/assert + verify `dest_ars` format. | `synthesis/incommuters.py:198` | MODELING | M | S | investigate first |
| 2.6 | Duplicated `random_seed + 8572` offset for two independent draws (PT + car/bike); other attributes use distinct offsets. Give each attribute its own offset. | `synthesis/population/enriched.py:528,554` | MODELING | M | S | repro (one-time) |

---

## P3 — Performance at scale (medium effort; some FP/RNG-repro sensitive)

| # | Finding | Where | Type | Impact | Effort | Risk |
|---|---|---|---|---|---|---|
| 3.1 | IPF **selector construction** is O(constraints × \|df_model\|) boolean masking incl. two `iterrows()` loops; replace with `groupby(...).indices` / factorize group keys. Dominant IPF-stage cost at 100%. | `ipf/model.py:185-422` | PERF | **H** | M | output-identical if pairs preserved |
| 3.2 | IPF **iteration** recomputes every selector sum from scratch up to 1500×; mutually-exclusive margins can be one `np.bincount`. | `ipf/model.py:435-466` | PERF | **H** | M | FP repro |
| 3.3 | Secondary `_extract_locations` per-row Python loop over millions of legs (`str.split`, `geo.Point` each) → vectorise with `str.rsplit(expand)`, masks, `points_from_xy`, `groupby.size`. | `secondary_chainsolvers.py:508-550` | PERF | **H** | M | output-identical (no RNG) |
| 3.4 | Secondary `_build_plans_df` builds millions of rows via list-of-dicts → columnar build (keep scalar RNG draw order). | `secondary_chainsolvers.py:252-324` | PERF | M (H@100%) | M | repro if order kept |
| 3.5 | Car/bike availability raking: **1000 fixed iterations** of boolean-masked pandas reductions over the full population (two loops). Tolerance break + NumPy array. | `synthesis/population/enriched.py:128,149,181` | PERF | **H** | S/M | FP repro |
| 3.6 | Education dense `cdist` (pupils×schools) not radius-blocked → memory wall at 100% (grundschule); KDTree `query_radius` + sparse Furness. Pair with the `cdf`/boolean-matrix allocs. | `education_gravity_model.py:73,122,49,135` | PERF | M (H mem@100%) | L | repro-sensitive |
| 3.7 | Secondary `dict(tuple(groupby))` materialises every person's sub-frame at once → memory spike on the serial/large-shard path at 100%; slice by integer offsets instead. | `secondary_chainsolvers.py:661,694` | PERF | M-H (mem) | M | repro if order kept |
| 3.8 | Per-zone `sjoin` over all homes ×N_zones → single `sjoin(predicate="within")` + pivot. | `synthesis/population/enriched.py:115-121` | PERF | M (100%) | M | output-equiv |

---

## P4 — Modeling realism (larger; mostly need calibration/data + flag-gating)

| # | Finding | Where | Type | Impact | Effort |
|---|---|---|---|---|---|
| 4.1 | Education **by-age BBS share** unfilled (`bbs_share_by_age=None`): every 16-19yo gets 68.1% BBS, mis-routing 16yo to the long-trip vocational catchment. Infra exists; fill from NDS Schuljahresstatistik. | `education_gravity.py:128-133` | MODELING | M | M |
| 4.2 | Cordon **gate gravity `beta`/`capacity_exponent` are pinned but UN-calibrated** (unlike every other gravity in the repo) — biggest unvalidated cordon assumption; `capacity_exponent=1.0` treats link capacity as linear attraction. | `data/cordon/gate_assignment.py:90-151`; `cordon_gates.py:57` | MODELING | M | M |
| 4.3 | Cordon PT entry-stop = **nearest only** (Euclidean argmin), ignores frequency/rail priority; car uses a gravity draw → asymmetric realism. | `synthesis/incommuters.py:206-232` | MODELING | M | M |
| 4.4 | Cordon in-ZGB leg uses a **fixed 30 km/h for car and PT** → car released too late, PT too early (departure-time bias). Mode-dependent speed. | `synthesis/incommuters.py:158`; `gate_entry.py:15-34` | MODELING | M | S |
| 4.5 | Gravity **diagonal/intrazonal friction is a flat `+1.0`** regardless of Gemeinde size; a size/area-scaled diagonal makes the intra- vs inter-Gemeinde OD split realistic (masked at KPI level by the MiD override). | `gravity/model.py:38,422-425` | MODELING | M | S/M |
| 4.6 | Gravity slope vs MiD-P13 override are **double-modeled**: assigned destination distance and the MiD-drawn person distance can contradict per-person. Reconcile (draw conditioned on the assigned band). | `synthesis/spatial/commute_distance.py:90-121` | MODELING | M | M |
| 4.7 | Provenance debt (violates the repo's "no hardcoded calibration values" rule): PT cost = **Munich MVV zonal tariff** (BS is VRB), car cost flat **0.20 EUR/km** unsourced, parking a binary 8 km ring at 3 EUR/h. Move to reference CSVs + regional values. | `BraunschweigPtCostModel.java:136`; `BraunschweigCostParameters.java:12`; `BraunschweigCarCostModel.java:39` | MODELING | M | M |
| 4.8 | No **Park&Ride / bike-feeder** to rail; PT access is walk-teleport only → understates PT for the commuter ring (the cordon population). | `RunAdaptConfig.java:41-49`; `BraunschweigModeAvailability.java` | MODELING | M-H | L |
| 4.9 | IPF convergence stops on the **update-factor proxy** (tol 1e-2), not the actual margin deviation the validator checks → can stop in a state the validator then hard-rejects (wasted full run). Tie stop to margin deviation + report per-margin worst cell. | `ipf/model.py:461-474,489-538` | MODELING | M | M |
| 4.10 | IPF zero-cell recovery re-seeds with `1e-9` (factor ~1e9 into the convergence stat) **silently**; log the re-seed count + exclude from the convergence statistic. | `ipf/model.py:445-451` | MODELING | M | S |
| 4.11 | Realised hh_type marginal drifts from Zensus after `normalize_type` demotions + `_ensure_child_capacity` promotions, with **no in-stage realised-vs-target check**. Add a quality log. | `ipf/household_composition.py:194-205`; `attributed.py:341` | MODELING | M | S |
| 4.12 | MATSim **iteration count fixed at 100/200** with the eqasim termination criterion registered but CLI-overridden; with mode choice off the system converges far sooner. Let the criterion stop (safety-cap the max). | `run.py:55`; `RunAdaptConfig.java:98` | PERF | M | S |
| 4.13 | QSim `flowCap/storageCap = sampleSize`, but cordon injects external agents **on top** → at 1%/25% the gates (where external demand concentrates) may be mis-capacitated. Audit injection sampling vs cap factor. | `GenerateConfig.java:81`; cordon inject | MODELING | M | M |

Also flagged (low priority): dead-code check on `assign_by_radius` (education_gravity_model.py:140); `.replace(dict)`→`.map(dict)` robustness in IPF prepare/model; vectorise `age_to_level`/lambda maps (education_gravity.py:117); per-RS7 slope coverage warnings when origin scope widens (gravity + education); zero-income placeholder stage (`synthesis/income.py:25`); hard-coded income class map + magic `2800` fallback (`household_income.py:41`, `enriched.py:710`); `restrict_to_modes`/`assign_fixed_mode` per-agent loop (cordon plans.py:21).

---

## Recommended sequencing

1. ~~Decide P0~~ **RETRACTED** — DMC already runs in-loop; no fixed-mode problem. Modal-split calibration is a normal post-run validation question (0.2), not a structural gap.
2. **Land P1 quick wins** (all low-risk, mostly output-identical): 1.1–1.8. Immediate runtime/memory relief for the 100% run, no re-baseline needed.
3. **Fix P2 hazards** as one deliberate, documented re-baseline (2.1–2.6) — they make runs reproducible and remove latent bugs.
4. **P3 performance refactors** before/with the 100% run if synthesis time/memory is the bottleneck (3.1, 3.3, 3.5 are the big three).
5. **P4 modeling realism** as scoped follow-ups, each flag-gated + calibrated (4.1, 4.2, 4.7, 4.8 are the most material).
