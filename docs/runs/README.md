# Run records

One **YAML manifest per significant run**: `docs/runs/<run_id>.yml`
(schema: `braunschweig/documentation/schema.py::parse_manifest`; strict keys).
Run manifests are the authoritative record of executed runs and their
validation evidence — a feature may only claim `validation.state:
measured_vs_reference` / `behaviourally_validated` by pointing at manifests
here. The browsable table is generated to
[`docs/generated/RUNS.md`](../generated/RUNS.md); never edit that file.

Manifest rules (carried over from the retired `RUNS.md` ledger, archived at
[`docs/archive/RUNS_ledger_2026-08-13.md`](../archive/RUNS_ledger_2026-08-13.md)):

- Fields not recoverable from a committed source are the literal `unknown` —
  no invented values.
- `classification` says what the run WAS (`smoke`, `wiring_proof`, `ab_test`,
  `calibration`, `validation`, `production_candidate`, `production`); a
  completed run is not validation, a smoke is not validation, and convergence
  (mode shares stabilising) is never validation.
- `validation` entries name the observed reference a result was compared to;
  keep the honest labels ("draw coherence", "control fit", "NOT a behavioural
  validation") in `notes`.

The Markdown files in this directory (`2026-06-06_100pct_run_monitor.md`,
`2026-06-11_popsim_bugfix_wave.md`, `2026-06-22_1pct_allfeat_full_smoke_findings.md`)
are per-run monitor/finding artifacts referenced by the manifests.

## Run artifacts on the run server (snapshot 2026-06-28, read-only discovery)

Authoritative run artifacts live on the Linux run server under
`/home/felix/eqasim-bs/eqasim-data/`. Discovered there on 2026-06-28 (date =
directory mtime, size = `du -sh`; the per-run `*_meta.json` was found
inconsistent — sampling/hts below come from the directory NAME):

| artifact | date | size | what it is (from dir name) |
|---|---|---|---|
| `cache_bs_100pct_allfeat_synth` | 2026-06-27 | 11G | 100% all-features synthesis cache (synthesis-only; no MATSim output dir alongside) |
| `cache_bs_25pct_allfeat_popsim` | 2026-06-24 | 13G | 25% all-features popsim cache |
| `output_bs_25pct_allfeat_popsim` | 2026-06-27 | 2.3G | 25% all-features popsim output (has `analysis/cordon`; no `mid_validation` report) |
| `cache_bs_1pct_allfeat_fit` | 2026-06-26 | 2.3G | 1% all-features fit cache |
| `cache_bs_1pct_allfeat_popsim` | 2026-06-22 | 3.3G | 1% all-features popsim cache |
| `output_bs_1pct_allfeat_popsim` | 2026-06-26 | 19M | 1% all-features popsim output |
| `output_bs_100pct_popsim_t3` | 2026-06-17 | 810M | 100% popsim tier-3 output (older code) |
| `output_full_allfeatures` | 2026-06-17 | 828M | full all-features output (older code) |

The "100% production run on newest code" work item (issue #22) is re-scoped by
this snapshot: a 100% all-features **synthesis** cache already exists — confirm
MATSim on top of it rather than synthesising from scratch, and verify against
the server before launching a fresh full run.
