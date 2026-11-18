#!/usr/bin/env bash
# Install one commit of the PopulationSim fork into the evidence environment and print what the
# environment itself reports (issues #646 and #656).
# Usage: install_candidate.sh <env_dir> <fork_clone> <bundle> <branch> <commit>
#   env_dir     a copy of environments/populationsim (pyproject.toml, uv.lock) after
#               `uv sync --frozen --python /usr/bin/python3.12`: every package but the fork keeps the
#               version of the tracked lock
#   fork_clone  a clone of the fork with its tags; created from <env_dir>/../populationsim_ci if missing
#   bundle      git bundle that holds <commit> on <branch>
# The fork package is replaced by <commit> (--no-deps), so tool.json of the runs reports the fork's own
# version string and that commit. The package list of the environment goes to <env_dir>.freeze.txt.
set -euo pipefail
ENVDIR="$1"; CLONE="$2"; BUNDLE="$3"; BRANCH="$4"; COMMIT="$5"
UV="$HOME/.local/bin/uv"
if [ ! -d "$CLONE" ]; then git clone -q "$(dirname "$ENVDIR")/populationsim_ci" "$CLONE"; fi
git -C "$CLONE" fetch -q "$BUNDLE" "$BRANCH"
git -C "$CLONE" checkout -q "$COMMIT"
[ "$(git -C "$CLONE" rev-parse HEAD)" = "$COMMIT" ] || { echo "clone is not at $COMMIT" >&2; exit 1; }
[ -z "$(git -C "$CLONE" status --porcelain)" ] || { echo "clone is not clean" >&2; exit 1; }
cd "$ENVDIR"
"$UV" pip install --no-deps --force-reinstall "populationsim @ git+file://$CLONE@$COMMIT" > "$ENVDIR.install.log" 2>&1
tail -3 "$ENVDIR.install.log"
"$UV" run --no-sync python -c "import importlib.metadata as m, json; d=m.distribution('populationsim'); print(json.dumps({'populationsim': d.version, 'direct_url': d.read_text('direct_url.json')}))"
"$UV" run --no-sync python -c "import populationsim, ortools, scipy, numba, numpy, pandas, sys; print(populationsim.__file__); print(sys.version.split()[0], 'ortools', ortools.__version__, 'scipy', scipy.__version__, 'numba', numba.__version__, 'numpy', numpy.__version__, 'pandas', pandas.__version__)"
"$UV" pip freeze 2>/dev/null | sort > "$ENVDIR.freeze.txt"
wc -l "$ENVDIR.freeze.txt"
