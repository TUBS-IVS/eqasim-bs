"""Checks that a MATSim run with zone-based parking costs actually used the parking module (issue #436).

MATSim reads a config module it has no registered group for as an untyped group without error, and the pipeline
builds the jar from whatever eqasim-java-bs checkout ``eqasim_source_path`` points to. A scenario prepared with the
zones on (``braunschweig.matsim.simulation.prepare`` writes the module ``braunschweigParking`` with ``enabled`` =
``true``) but run with a jar that lacks the package ``org.eqasim.braunschweig.parking`` would therefore price no
parking at all and still look healthy: a silent fallback across the two repositories. ``matsim.simulation.run``
calls the three functions below at the point of use:

1. :func:`parking_module_enabled`, the trigger: the PREPARED config (the file the run is started with) carries the
   module, enabled. No pipeline config key is involved; with the module absent or disabled the stage calls
   nothing else, so the OFF path runs MATSim exactly as before.
2. :func:`require_parking_package`, before the run: the jar contains the classes of the Java ``ParkingConfigGroup`` and
   ``GarageOptionModel`` (the schema-3 tariff model the pipeline always writes).
3. :func:`require_parking_outcomes`, after the run: the last iteration that ran wrote the outcome report of the
   Java ``ParkingOutcomeReportListener``, which the parking module writes at the end of EVERY iteration, so its
   absence proves that the module did not price parking.

Each check raises instead of warning: a run that silently priced no parking cannot be scientifically defended. The
functions read files only; the last one also logs one INFO line when both checks passed.
"""
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

from braunschweig.matsim import config_modules

log = logging.getLogger(__name__)

#: MATSim config module of the Java ``ParkingConfigGroup`` (``GROUP_NAME``), the name
#: ``braunschweig.matsim.simulation.prepare.PARKING_MODULE`` writes. Kept here rather than imported from that stage
#: module, which would pull the preparation's import chain into the run stage; ``tests/test_parking_run_checks.py``
#: pins the two names equal.
PARKING_MODULE = "braunschweigParking"
#: Jar entry of ``org.eqasim.braunschweig.parking.ParkingConfigGroup``: present exactly when the jar has the package.
PARKING_CONFIG_GROUP_CLASS_ENTRY = "org/eqasim/braunschweig/parking/ParkingConfigGroup.class"
#: Jar entry of ``org.eqasim.braunschweig.parking.GarageOptionModel`` (the garage options, tariff model schema 3). The
#: Python pipeline always writes schema 3 (``tariff_export.build_tariff_model``), so a jar that has the package but
#: predates the garage options would still fail at startup; the check names it before the run instead.
GARAGE_OPTION_MODEL_CLASS_ENTRY = "org/eqasim/braunschweig/parking/GarageOptionModel.class"
#: The classes a jar must contain for the run: the parking package and its schema-3 garage option model.
REQUIRED_PARKING_CLASS_ENTRIES = (PARKING_CONFIG_GROUP_CLASS_ENTRY, GARAGE_OPTION_MODEL_CLASS_ENTRY)
#: File name of the per-iteration outcome report (Java ``ParkingOutcomeReportListener.FILE_NAME``); MATSim writes it
#: as ``ITERS/it.N/N.parking_outcomes.csv`` (no run id is set: the run stage expects ``output_events.xml.gz``).
OUTCOME_REPORT_FILE_NAME = "parking_outcomes.csv"

_ITERATION_DIRECTORY = re.compile(r"it\.(\d+)")
#: The literal values the Java ParkingConfigGroup accepts for ``enabled``.
_ENABLED_TRUE = "true"
_ENABLED_FALSE = "false"


def parking_module_enabled(config_path) -> bool:
    """Whether the prepared MATSim config at ``config_path`` enables the ``braunschweigParking`` module.

    False when the config has no such module, when ``enabled`` is ``false`` or when the parameter is absent (the
    Java default is false). Raises ``ValueError`` for any other value: the typed Java group accepts only the
    literal texts true and false, while a jar without the package would ignore the value silently. Raises what
    ``braunschweig.matsim.config_modules.read_module`` raises for a file that is no MATSim config. Reads the file.
    """
    params = config_modules.read_module(config_path, PARKING_MODULE)
    if params is None:
        return False
    enabled = params.get("enabled")
    if enabled is None or enabled == _ENABLED_FALSE:
        return False
    if enabled == _ENABLED_TRUE:
        return True
    raise ValueError(f"{PARKING_MODULE}.enabled must be the literal text {_ENABLED_TRUE} or {_ENABLED_FALSE}, as the "
                     f"Java ParkingConfigGroup requires, got {enabled!r} in {config_path}")


def require_parking_package(jar_path) -> None:
    """Raise unless the jar at ``jar_path`` contains the classes of the Java ``ParkingConfigGroup`` and
    ``GarageOptionModel`` (``REQUIRED_PARKING_CLASS_ENTRIES``).

    Called before the run when :func:`parking_module_enabled` is true. Raises ``FileNotFoundError`` for a missing
    jar and ``RuntimeError`` for a file that is not a zip archive or a jar without one of the classes, naming the
    jar, every missing class, the config module and the fix. Reads only the jar's central directory.
    """
    path = Path(jar_path)
    if not path.is_file():
        raise FileNotFoundError(f"[parking] the jar to check for the parking package does not exist: {path}")
    try:
        with zipfile.ZipFile(path) as jar:
            entries = set(jar.namelist())
    except zipfile.BadZipFile as error:
        raise RuntimeError(f"[parking] cannot read the jar {path} as a zip archive: {error}") from error
    missing = [entry for entry in REQUIRED_PARKING_CLASS_ENTRIES if entry not in entries]
    if missing:
        raise RuntimeError(
            f"[parking] the prepared config enables the MATSim config module {PARKING_MODULE}, but the jar {path} "
            f"does not contain {', '.join(missing)}: without the package org.eqasim.braunschweig.parking "
            "MATSim reads the module as an untyped config group and the run would price no parking at all, and "
            "without GarageOptionModel the jar predates the garage options (tariff model schema 3, which the pipeline "
            "always writes). Check out an eqasim-java-bs revision that contains both in the tree eqasim_source_path "
            "points to (the jar is rebuilt from it on the next run), or supply a jar built from such a revision as "
            "eqasim_path.")


def _last_iteration(iters_directory: Path) -> int:
    """The highest N of the ``it.N`` directories in ``iters_directory``; raises when there is none."""
    iterations = []
    if iters_directory.is_dir():
        for entry in iters_directory.iterdir():
            match = _ITERATION_DIRECTORY.fullmatch(entry.name)
            if match and entry.is_dir():
                iterations.append(int(match.group(1)))
    if not iterations:
        raise RuntimeError(f"[parking] {iters_directory} holds no iteration directory it.N, so the run check cannot "
                           "find the parking outcome report of the last iteration")
    return max(iterations)


def require_parking_outcomes(simulation_output) -> int:
    """Raise unless the last iteration that ran wrote the parking outcome report; return that iteration.

    The last iteration is the highest ``ITERS/it.N`` in ``simulation_output``, not the configured
    ``controler.lastIteration``: the eqasim mode-share termination criterion can end a run earlier. synpp empties
    the stage directory before a run, so no directory of an earlier run remains. Call it after
    :func:`require_parking_package` passed, as ``matsim.simulation.run`` does: it logs the one INFO line that both
    checks passed. Reads the directory listing only.
    """
    iteration = _last_iteration(Path(simulation_output) / "ITERS")
    report = Path(simulation_output) / "ITERS" / f"it.{iteration}" / f"{iteration}.{OUTCOME_REPORT_FILE_NAME}"
    if not report.is_file():
        raise RuntimeError(
            f"[parking] the run wrote no parking outcome report {report}: the Java ParkingOutcomeReportListener "
            f"writes one at the end of every iteration when the {PARKING_MODULE} module is active, so the module "
            "did not price parking in this run although the prepared config enables it. Check the jar "
            "(eqasim_source_path) and the [parking] lines of the MATSim log.")
    log.info("[parking] run check: jar contains the parking package; outcomes written for iteration %d", iteration)
    return iteration
