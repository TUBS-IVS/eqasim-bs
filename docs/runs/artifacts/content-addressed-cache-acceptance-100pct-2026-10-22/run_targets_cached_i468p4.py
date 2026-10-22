"""Run synpp targets WITHOUT re-executing cached targets (#468 Task 18 early-cutoff scenarios).

The production launcher (scripts/run_synpp.py -> synpp run_from_yaml) always passes rerun_required=True,
so a run target re-executes on every launch whatever its parents produced. To observe early cutoff on
real stages, the spared stages must not be re-executed for that reason: this driver runs the same
composed configuration through the installed fork (braunschweig.synpp_fork) with rerun_required=False,
inside cache_share.track_run (provenance envelopes, as the launcher does). Same definitions, config,
externals and aliases as the launcher (braunschweig.synpp_fork.registry).
Usage: python run_targets_cached_i468p4.py <base.yml> <overlay.yml>
"""
import logging
import os
import sys

# The driver lives next to the worktrees, not inside one: import the checkout it is run from (cwd).
sys.path.insert(0, os.getcwd())
from braunschweig import cache_share, synpp_fork  # noqa: E402
from braunschweig.synpp_fork import registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
base, overlay = sys.argv[1:3]
settings = registry.load_settings(base, overlay)
synpp_fork.install()
import synpp  # noqa: E402  (after install: synpp.run is the fork's run)

working_directory = settings["working_directory"]
with cache_share.track_run(working_directory):
    synpp.run(registry.definitions_from_settings(settings), settings.get("config", {}) or {},
              working_directory=working_directory, rerun_required=False,
              externals=settings.get("externals", {}) or {}, aliases=settings.get("aliases", {}) or {})
print("[i468p4] driver done")
