# PopulationSim environment of the popsim stage

The uv project the popsim stage launches (`uv run --no-sync populationsim -w <batch folder>`, cwd =
`braunschweig.population.popsim.popsimprep_dir`). It pins the group's PopulationSim fork and the
dependency versions of the production environment. Install it once per checkout on the run server:

```bash
cd environments/populationsim
uv sync --frozen --python /usr/bin/python3.12
```

The stage checks at start that the installed environment equals `uv.lock` (`braunschweig/popsim/tool_version.py`);
after a pull that changes `uv.lock`, run the command above again. The start check needs a uv with `uv sync --check` (measured with uv 0.11.21 on the
run server: run manifest `docs/runs/popsim-env-security-update-2026-11-27.yml`). Branch model, releases, why the versions are pinned
and the rule for security updates: `docs/codebase/notes/populationsim-fork.md`.
