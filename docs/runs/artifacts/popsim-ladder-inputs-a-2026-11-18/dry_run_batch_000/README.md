# Dry run of the input-consistency arm driver (issue #658, Task A7)

A check of `scripts/prepare_input_arm.py` on real data before any arm of #658 is prepared or run.
It is NOT an arm: no PopulationSim run, no pre-registration. The arm-4 folders it is meant for
do not exist yet (Part T, #657), so the base here is the arm-3 folder of batch_000
(`popsim-ladder-arm3-2026-11-18`, rounding "controlled", search). Code: eqasim-bs commit
`9ae42a0d` (git archive on the run server), run 2026-11-18.

Command (paths relative to the server home):

    PYTHONPATH=<code> python docs/runs/artifacts/popsim-ladder-inputs-a-2026-11-18/scripts/prepare_input_arm.py \
        --base ~/i646/ev/controlled_off/batch_000 --out <dry-run dir>/batch_000 \
        --steps kreis_universe,kreis_follow_grids --prod-work-dir ~/prod100_20261031/popsim_work \
        --cells-100m <eqasim-data>/data/braunschweig/popsim/cells/zensus2022_grid_100m_de_prepared.parquet \
        --commit 9ae42a0d

The input-consistency tables were then written with
`docs/runs/artifacts/popsim-input-consistency-2026-11-18/scripts/run_input_consistency.py` for the
base folder (`ic_base.csv`) and the prepared folder (`ic_dry.csv`).

| File | Content |
|---|---|
| `prepare_log.json` | steps `kreis_universe,kreis_follow_grids`: exit 0, `unexpected_changed_files` empty, only `data/control_totals_KREIS.csv` changed; trip_class partition of the rebuilt Kreis targets within 1 person of the Kreis persons |
| `ic_base.csv`, `ic_dry.csv` | input-consistency table of the base and the prepared folder (one Kreis part, 03153) |
| `prepare_log_all_three_steps.json`, `prepare_all_three_steps.log` | the same with `employment_level` added: exit 0, only the KREIS table changed, the bridge and the reconciliation counts are in the log; the log warns that the arm-3 seed differs from the production seed (the arm folders carry the resolved seed of the earlier arms), so the bridge comes from the base batch's seed |

What the two tables show (read them for the values): after the two steps the KREIS car, bicycle
and employed targets of the part equal the batch sums of their grids (share 0); the persons 0-17
and 18+ pairs shrink to the rounding residual; `persons_all` and every cell check are unchanged,
as the driver changes only the KREIS table.
