"""Guard the synpp per-stage config contract (issue #229) for the matsim.runtime
build stages AND their consumer stages: a stage's execute() may only read config
options the SAME stage's configure() declared. pt2matsim.execute reads git/maven/java
helper options via git.run/maven.run/java.run; eqasim.execute reads git/maven helper
options. A missing declaration crashes real runs ("Config option X is not requested")
as soon as the stage cache is devalidated -- see #222/#223 for the same class.

CONSUMER coverage (night-run regression, 2026-08-20): the original tests guarded only
the runtime modules' own configure(), not the stages that CALL pt2matsim.run() in their
execute(). All three supply stages (gtfs / osm / processed) read pt2matsim's keys
through that helper while declaring none of them, so the 100 % run died with
"Config option pt2matsim_version is not requested" the moment the eqasim-java 2.3.0
version bump devalidated those long-lived caches. The consumer test below DISCOVERS
its subjects by scanning for the helper call, so a new caller is covered on arrival
instead of waiting for the next cache devalidation to expose it.
"""
import importlib
import re
from pathlib import Path

import pytest

import matsim.runtime.eqasim as eqasim
import matsim.runtime.java as java
import matsim.runtime.pt2matsim as pt2matsim


class _RecordingContext:
    """Minimal configure()-time context: records declared config options."""

    def __init__(self):
        self.declared = set()

    def stage(self, name, *args, **kwargs):
        pass

    def config(self, name, *args, **kwargs):
        self.declared.add(name)


def test_pt2matsim_declares_all_helper_config_options():
    ctx = _RecordingContext()
    pt2matsim.configure(ctx)
    required = {"git_binary",                                   # git.run
                "maven_binary", "maven_skip_tests", "java_home",  # maven.run
                "java_binary", "java_memory"}                   # java.run
    assert required <= ctx.declared, (
        f"pt2matsim.configure missing declarations: {sorted(required - ctx.declared)}")


def test_eqasim_declares_git_and_maven_helper_options():
    ctx = _RecordingContext()
    eqasim.configure(ctx)
    required = {"git_binary", "maven_binary", "maven_skip_tests", "java_home"}
    assert required <= ctx.declared, (
        f"eqasim.configure missing declarations: {sorted(required - ctx.declared)}")


# --- Consumer stages: every module that calls pt2matsim.run() in execute() -------

#: Keys ``java.run`` reads from the CALLING stage's context. The hang-watchdog keys
#: (issue #330) are declared volatile, and synpp does NOT propagate volatile options
#: to downstream stages, so every caller must declare them explicitly -- an omission
#: is exactly the "Config option ... is not requested" crash class guarded here.
JAVA_RUN_KEYS = {"java_binary", "java_memory",
                 java.KEY_HANG_TIMEOUT, java.KEY_HANG_MIN_CPU}

#: Keys ``pt2matsim.run`` reads from the CALLING stage's context (its own version key
#: plus everything ``java.run`` needs); a caller must declare all of them.
PT2MATSIM_RUN_KEYS = {"pt2matsim_version"} | JAVA_RUN_KEYS

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _pt2matsim_run_callers() -> list:
    """Dotted module names whose source calls ``pt2matsim.run(`` (excluding the helper)."""
    found = []
    for path in sorted((_REPO_ROOT / "matsim").rglob("*.py")):
        if path.parts[-2:] == ("runtime", "pt2matsim.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bpt2matsim\.run\(", text):
            found.append(".".join(path.relative_to(_REPO_ROOT).with_suffix("").parts))
    return found


def test_pt2matsim_run_callers_are_discovered():
    """The discovery must not silently return an empty subject list (vacuous green)."""
    callers = _pt2matsim_run_callers()
    assert len(callers) >= 3, f"expected the supply stages among the callers, got {callers}"
    assert "matsim.scenario.supply.gtfs" in callers


@pytest.mark.parametrize("module_name", _pt2matsim_run_callers())
def test_pt2matsim_run_caller_declares_the_helper_options(module_name):
    module = importlib.import_module(module_name)
    ctx = _RecordingContext()
    module.configure(ctx)
    missing = PT2MATSIM_RUN_KEYS - ctx.declared
    assert missing == set(), (
        f"{module_name}.configure does not declare {sorted(missing)} but its execute() "
        "calls pt2matsim.run(); the run raises 'Config option ... is not requested' as "
        "soon as this stage's cache is devalidated. Delegate to pt2matsim.configure(context).")


# --- Consumer stages: every module that calls eqasim.run() in execute() ----------

#: ``matsim.runtime.eqasim`` only DEFINES the ``eqasim.run`` wrapper (its own execute()
#: builds the JAR through maven and never calls java.run), so it is not a consumer.
_EQASIM_RUN_HELPER_MODULES = {"matsim.runtime.eqasim"}


def _eqasim_run_callers() -> list:
    """Dotted module names whose source calls ``eqasim.run(`` (excluding the helper)."""
    found = []
    for package in ("matsim", "braunschweig"):
        for path in sorted((_REPO_ROOT / package).rglob("*.py")):
            module_name = ".".join(path.relative_to(_REPO_ROOT).with_suffix("").parts)
            if module_name in _EQASIM_RUN_HELPER_MODULES:
                continue
            if re.search(r"\beqasim\.run\(", path.read_text(encoding="utf-8")):
                found.append(module_name)
    return found


def test_eqasim_run_callers_are_discovered():
    """The discovery must not silently return an empty subject list (vacuous green)."""
    callers = _eqasim_run_callers()
    assert "matsim.simulation.run" in callers, callers
    assert "matsim.simulation.prepare" in callers, callers


@pytest.mark.parametrize("module_name", _eqasim_run_callers())
def test_eqasim_run_caller_declares_the_java_options(module_name):
    module = importlib.import_module(module_name)
    ctx = _RecordingContext()
    module.configure(ctx)
    missing = JAVA_RUN_KEYS - ctx.declared
    assert missing == set(), (
        f"{module_name}.configure does not declare {sorted(missing)} but its execute() "
        "calls eqasim.run() -> java.run(); the run raises 'Config option ... is not "
        "requested' as soon as this stage's cache is devalidated. Delegate to "
        "matsim.runtime.java.configure(context).")
