# The PopulationSim fork and its wiring into the popsim stage

Decision and reasons: [ADR-0197](../../decisions/ADR-0197-the-populationsim-fork-branches-from-tag-v0-10-0.md) (base) and [ADR-0199](../../decisions/ADR-0199-populationsim-fork-with-fractional-weights-and-proportional-draw.md) (what the fork adds). Issue #645.

## Branch model

The fork is the public repository `TUBS-IVS/populationsim`. `master` is an untouched mirror of upstream; `bs/main` is branched from upstream tag v0.10.0 and holds the group's changes. A release is the tag `v0.10.0+bs.N` on `bs/main`, with the same string as the package version.

## Cutting a release `v0.10.0+bs.N`

1. Only after the evidence run (the arm-1 manifest `popsim-ladder-arm1-2026-11-17` for `bs.1`).
2. In the fork: set the version in `pyproject.toml` and in the fork's `uv.lock` entry, commit, tag `v0.10.0+bs.N`.
3. Here: switch the fork source in `environments/populationsim/pyproject.toml` from `rev` to `tag`, relock on the run server (`uv lock`), commit `uv.lock`.
4. Set `braunschweig.population.popsim.populationsim_version` in `configs/base_bs.yml` to the same string.

## The tracked environment

`environments/populationsim/` is a uv project; the stage runs `uv run --no-sync populationsim -w <batch folder>` with `popsimprep_dir` (the project directory) as cwd. Its constraints are the production freeze: the fork changes PopulationSim only, so every other package keeps the version of the environment the baseline runs used. Install once per checkout on the server: `uv sync --frozen --python /usr/bin/python3.12` (see its README). The stage checks the installed version against `populationsim_version` and the installed git commit against the one `uv.lock` pins at start (a relative `popsimprep_dir` is resolved against the repository root), and fails with a message naming the asked environment (`braunschweig/popsim/tool_version.py`).

## Open at the next fork release

The fork README says the draw is "weight-proportional in both integerizers"; that holds for the single integerizer only (ADR-0199, decision 5). Soften the README wording when the next release is cut.

## Neutral-mode guarantee

With `keep_fractional_sample_weights` false and `integerizer_draw` "off" the fork behaves like v0.10.0 (differential test in the fork; run R1 of the arm-1 manifest). The stage always sets the timeout and switches the path record on.

## Where each fact lives

| Fact | Owner |
|---|---|
| Fork settings, defaults, path-record contract | fork README and `populationsim/integerizing/path_record.py` on [`bs/main`](https://github.com/TUBS-IVS/populationsim/tree/bs/main) |
| Config keys of the stage (meaning) | `braunschweig/popsim/stage/config_keys.py`; values in `configs/base_bs.yml` |
| Per-batch integerizer seed, settings overrides | `braunschweig/popsim/folders.py` (`batch_integerizer_seed`) |
| Solver-path gate | `braunschweig/popsim/stage/solver_path_gate.py` |
| Production value of draw and fractional weights | outcome of the arm-1 manifest |
| Why | ADR-0197, ADR-0199 |
