# PopulationSim environment of the popsim stage

The uv project the popsim stage launches (`uv run --no-sync populationsim -w <batch folder>`, cwd =
`braunschweig.population.popsim.popsimprep_dir`). It pins the group's PopulationSim fork and the
dependency versions of the production environment. Install it once per checkout on the run server:

```bash
cd environments/populationsim
uv sync --frozen --python /usr/bin/python3.12
```

The stage checks the installed version at start (`braunschweig/popsim/tool_version.py`). Branch
model, releases and why the versions are pinned: `docs/codebase/notes/populationsim-fork.md`.
