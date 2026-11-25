# The PopulationSim fork and its wiring into the popsim stage

Decision and reasons: [ADR-0197](../../decisions/ADR-0197-the-populationsim-fork-branches-from-tag-v0-10-0.md) (base), [ADR-0199](../../decisions/ADR-0199-populationsim-fork-with-fractional-weights-and-proportional-draw.md) (fractional weights and draw, issue #645) and [ADR-0201](../../decisions/ADR-0201-populationsim-rounds-to-whole-households-with-controlled-rounding.md) (controlled rounding, issue #646).

## Branch model

The fork is the public repository `TUBS-IVS/populationsim`. `master` is an untouched mirror of upstream; `bs/main` is branched from upstream tag v0.10.0 and holds the group's changes. A release is the tag `v0.10.0+bs.N` on `bs/main`, with the same string as the package version.

## Cutting a release `v0.10.0+bs.N`

1. Only after the evidence run (the arm-1 manifest `popsim-ladder-arm1-2026-11-17` for `bs.1`, the arm-3 manifest `popsim-ladder-arm3-2026-11-18` for `bs.2`, the release manifest `popsim-bs3-release-2026-11-25` for `bs.3`).
2. In the fork: set the version in `pyproject.toml` and in the fork's `uv.lock` entry, commit, tag `v0.10.0+bs.N`.
3. Here: switch the fork source in `environments/populationsim/pyproject.toml` from `rev` to `tag`, relock on the run server (`uv lock`), commit `uv.lock`.
4. Set `braunschweig.population.popsim.populationsim_version` in `configs/base_bs.yml` to the same string.

## The tracked environment

`environments/populationsim/` is a uv project; the stage runs `uv run --no-sync populationsim -w <batch folder>` with `popsimprep_dir` (the project directory) as cwd. Its constraints are the production freeze: the fork changes PopulationSim only, so every other package keeps the version of the environment the baseline runs used. Install once per checkout on the server: `uv sync --frozen --python /usr/bin/python3.12` (see its README). The stage checks the installed version against `populationsim_version` and the installed git commit against the one `uv.lock` pins at start (a relative `popsimprep_dir` is resolved against the repository root), and fails with a message naming the asked environment (`braunschweig/popsim/tool_version.py`).

## Fork CI: red on Python 3.9 and 3.10

The fork's CI runs its suite on Python 3.9 to 3.12. At both releases (`bs.1`, `bs.2`) the jobs for 3.11 and 3.12 pass and the jobs for 3.9 and 3.10 fail in the same six tests: upstream's `tests/test_steps.py::test_full_run1` (the expanded households differ from upstream's stored expectation), four tests that only fail because that test then leaves the pipeline open, and the fork's `tests/test_bs_neutral_equivalence.py::test_differential_detects_a_change`. What is known (2026-11-19, read from the CI logs and two local reproductions, not a committed measurement): the fork did not change the upstream test or its expectation; the fork's equivalence test against upstream `v0.10.0` passes in the failing jobs; with the Python and package versions of the failing jobs all six tests pass on the run server and on a second machine. What makes those CI jobs compute differently is not identified (owner decision 2026-11-19: release without it). The production environment is Python 3.12, so a red 3.9 or 3.10 job with exactly these six tests does not block a release; any other failure does.

## Neutral-mode guarantee

With `keep_fractional_sample_weights` false and `integerizer_draw` "off" the fork behaves like v0.10.0 (differential test in the fork; run R1 of the arm-1 manifest). With `integerizer_rounding` "round" release `0.10.0+bs.2` gives the output of `0.10.0+bs.1` (differential test in the fork; neutral-equivalence check of the arm-3 manifest). The stage always sets the timeout and switches the path record on.

## Run-time options and the environment rule (issue #656)

`store_positive_weight_rows_only` and `fast_paths` switch on two settings of fork release `0.10.0+bs.2` (the pinned release is `0.10.0+bs.3`, evidence: manifest `popsim-bs3-release-2026-11-25`); decision and acceptance rule: [ADR-0200](../../decisions/ADR-0200-populationsim-keeps-only-positive-weight-rows-and-takes-bit-identical-fast-paths.md).

Rule: before a run with `fast_paths` on in a changed environment (another numba or LLVM version in `environments/populationsim`, or another run machine), run the fork's balancer test file in that environment. The reason is decision 5 of ADR-0200. How: in a clone of the fork at the pinned tag, with the clone first on `PYTHONPATH`, `uv run --project <path to environments/populationsim> --no-sync --with pytest python -m pytest tests/test_bs_balancer_fast_path.py`. A failure means the option must stay off in that environment.

## Where each fact lives

| Fact | Owner |
|---|---|
| Fork settings, defaults, path-record contract | fork README and `populationsim/integerizing/path_record.py` on [`bs/main`](https://github.com/TUBS-IVS/populationsim/tree/bs/main) |
| Config keys of the stage (meaning) | `braunschweig/popsim/stage/config_keys.py`; values in `configs/base_bs.yml` |
| Per-batch integerizer seed, settings overrides | `braunschweig/popsim/folders.py` (`batch_integerizer_seed`) |
| Solver-path gate | `braunschweig/popsim/stage/solver_path_gate.py` |
| Rate of balancer calls on the fast kernels | `braunschweig/popsim/stage/fast_path_rate.py` |
| Production value of draw and fractional weights | outcome of the arm-1 manifest |
| Production value of the rounding | decision 5 of ADR-0201, on the outcome of the arm-3 manifest |
| Production value of the run-time options, worker memory | outcome of the manifest `popsim-run-performance-2026-11-18` (memory of release `bs.3` checked in `popsim-bs3-release-2026-11-25`); values in `configs/base_bs.yml` and `configs/overlays/test_100pct.yml` |
| Why | ADR-0197, ADR-0199, ADR-0200, ADR-0201 |
