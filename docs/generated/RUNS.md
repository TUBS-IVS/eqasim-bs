<!-- THIS FILE IS GENERATED. DO NOT EDIT MANUALLY.
     Rebuild: python -m braunschweig.documentation build
     Sources: docs/registry/** + docs/decisions/ + docs/runs/ -->

# Run manifests (generated)

One row per manifest under `docs/runs/`. Classification says what a run
WAS; a completed run is not validation and convergence is not validation.

| Run | Date | Classification | Sampling | Execution | First validation entry |
|---|---|---|---|---|---|
| [25pct-parking](../runs/25pct-parking.yml) | unknown | ab_test | 25% | unknown | MiD validation report (parking vs no-parking): referenced in docs/features/run-analysis.md; date unknown |
| [smoke-control-fit-03101-v2-2026-08-19](../runs/smoke-control-fit-03101-v2-2026-08-19.yml) | 2026-08-19 | smoke | balancing covers the full population | completed | license_underage plausibility violations (has_license=True below age 16): 0/251,853 (0.0000%), against 7,088/252,045 (2.81%) in the v1 run and 42,707/1,13 |
| [smoke-control-fit-03101-2026-08-19](../runs/smoke-control-fit-03101-2026-08-19.yml) | 2026-08-19 | smoke | balancing always covers the full pop | completed | PopulationSim convergence and integerizer feasibility with the 28 new control columns: 8/8 batches converged, 0 error signatures in the log. Integerizer: 3937/4075 zon |
| [i307-validation-baseline-2026-08-18](../runs/i307-validation-baseline-2026-08-18.yml) | 2026-08-18 | validation | 100% (1,130,141 persons / 558,284 ho | completed | control fit, all 10 registered controls (SRMSE, mean absolute delta in pp): 10 controls, grades 4 very good / 6 good, none moderate or worse. Largest mean \| |
| [i307-license-pt-measure-2026-08-18](../runs/i307-license-pt-measure-2026-08-18.yml) | 2026-08-18 | validation | 100% (1,130,141 persons; 1,130,139 w | completed | has_driving_license share, ZGB, per universe (share of persons in the universe): all ages 78.72%; 14+ 85.84%; 17+ 89.97%; 18+ 91.32%. Like-for-like: 14+ vs MiD 1 |
| [srv262-AB-5pct-2026-08-12](../runs/srv262-AB-5pct-2026-08-12.yml) | 2026-08-12/13 | ab_test, validation | 5% (~56k persons; popsim skipped via | completed | 9 drawn SrV category shares: all within 1.8pp; marginal fallback leisure 1.2% / other 17.1% (<20% warn) |
| [escort-anchorfix-5pct-2026-08-12](../runs/escort-anchorfix-5pct-2026-08-12.yml) | 2026-08-12 | ab_test, validation | 5% (~56k persons; popsim skipped via | completed | consecutive zero-distance escort legs: 674 -> 96 (-86%), zero share 9.0% -> 1.3%, all 96 residual exact-same-facility;  |
| [escort-AB-5pct-2026-08-11](../runs/escort-AB-5pct-2026-08-11.yml) | 2026-08-11/12 | ab_test, validation | 5% (~56k persons; popsim batches reu | completed | escort relabel + linking: relabel exact; escorter<=17 share 20.2% -> 9.1%; household link 58.5%; 0 NaN |
| [synth-100pct-2.2.0-2026-07-23](../runs/synth-100pct-2.2.0-2026-07-23.yml) | 2026-07-23 | production_candidate, validation | 100% (expansion ~558,284 households  | completed | control fit (category shares, apples-to-apples): census-scale families mean ~0.25pp; KREIS/SrV families mean ~1.1pp (max <2.7pp); |
| [matsim-e2e-2.2.0-kreis03101-2026-07-23](../runs/matsim-e2e-2.2.0-kreis03101-2026-07-23.yml) | 2026-07-23 | wiring_proof | 1% (sampling_rate 0.01) | completed | e2e pipeline reachability on eqasim-java 2.2.0: 8/8 stages green: synthesis -> in-commuters (times imputed) -> routing -> cutter |
| [config-cleanup-synth-smoke-2026-07-22](../runs/config-cleanup-synth-smoke-2026-07-22.yml) | 2026-07-22 | smoke, wiring_proof | 1 Kreis 03101 (full-pop synth) | completed | composition/runtime proof: the batch settings.yaml PopulationSim actually used has FLOAT_SEED_WEIGHTS: fals |
| [config-cleanup-killed-100pct-2026-07-22](../runs/config-cleanup-killed-100pct-2026-07-22.yml) | 2026-07-22 | production_candidate | 100% (1.13M, 8 Kreise) | killed | post-mortem (timing_log.csv): sub_balancing.geography=ZENSUS100m 8.5h/batch, balancer converged=False on every |
| [placement-income-l2-gate-2026-07-18](../runs/placement-income-l2-gate-2026-07-18.yml) | 2026-07-18 | ab_test, validation | 1% (113,968 HH / 228,585 persons per | completed | invariants on real output: economic_status/cars x Kreis, economic_status x CELL, HH x CELL, age x sex_raw x |
| [anchor-holdout-2026-07-17](../runs/anchor-holdout-2026-07-17.yml) | 2026-07-17 | ab_test, calibration, validation | 100% all-features popsim cache (15,1 | completed | pre-registered rule v2 gates (k=5 folds, seeds 20260716+42): (i') AO srmse 0.1300->0.1316 neutral-within-noise; (ii) P13-by-RS7 EMD 5/6 class |
| [verbindungen-ab-2026-07-16](../runs/verbindungen-ab-2026-07-16.yml) | 2026-07-16 | ab_test, validation | 100% all-features popsim_mid cache ( | completed | realised work OD vs VerBindungen 2019 QZM: check-B weighted TVD 0.137, band EMD 0.080, intra-cell 0.4694 vs ref 0.4687; che |
| [sector-aware-ab-2026-07-15](../runs/sector-aware-ab-2026-07-15.yml) | 2026-07-15 | ab_test, validation | 1% legacy simple_ipf path (census-le | completed | within-Kreis inflow total variation: OFF 0.009 vs ON 0.087 (9x worse); distance bands TV 0.003 unchanged; offline fun |
| [incommuter-mode-bundesland-smoke-2026-07-15](../runs/incommuter-mode-bundesland-smoke-2026-07-15.yml) | 2026-07-15 | smoke, wiring_proof | 25% (wd cache_bs_25pct_allfeat_popsi | partial | primary-path coverage: primary path 17105/17105 (100%), fallback 0%, 16/16 source Laender; ON vs OFF PT |
| [empstatus-control-smoke-2026-07-14](../runs/empstatus-control-smoke-2026-07-14.yml) | 2026-07-14 | smoke, calibration | 1 Kreis (Braunschweig 03101), max_ce | completed | in_ausbildung (14+) control rake: 2.98% (unraked kreis5) -> 2.09%, target 2.01%; 14+ universe confirmed |
| [empstatus-measure-2026-07-13](../runs/empstatus-measure-2026-07-13.yml) | 2026-07-13 | validation | 100% (1,124,108 persons, 8 Kreise) | completed | employment_status vs MiD 2023 P9 (independent): SRMSE 0.194, mean\|d\| 1.88pp, grade good, 100% cells <10pp, r^2=0.979 |
| [100pct-kreis5-2026-07-10](../runs/100pct-kreis5-2026-07-10.yml) | 2026-07-10 | production_candidate | 100% (~1.13M persons, 558k household | completed | 1km household totals: exact |
| [1pct-allfeat-full-2026-06-22](../runs/1pct-allfeat-full-2026-06-22.yml) | 2026-06-22 | smoke, wiring_proof | 1% | completed | wiring/convergence smoke: see docs/runs/2026-06-22_1pct_allfeat_full_smoke_findings.md |
| [popsim-smokes-2026-06-11](../runs/popsim-smokes-2026-06-11.yml) | 2026-06-11 | smoke | 1% mini (BS city cells) | completed | three-case comparability: fast suite 1504 passed; three-case comparability confirmed |
| [25pct-allfeat-2026-06-08](../runs/25pct-allfeat-2026-06-08.yml) | 2026-06-08 (approx) | production_candidate, validation | 25% | completed | commute distance EMD vs MiD P13: ~0.065 |
| [100pct-2026-06-06](../runs/100pct-2026-06-06.yml) | 2026-06-06 | production | 100% (~1.13M persons) | completed | run-level MiD validation (post-run): see docs/runs/2026-06-06_100pct_run_monitor.md |
