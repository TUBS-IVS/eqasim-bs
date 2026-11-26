"""Select the same-facility leisure trip constraint in the prepared MATSim config (eqasim-bs#679, ADR-0207).

Strolls and dog walks are placed at the activity they start from (#679), so their legs have no length, and the
zero-length way home after them has none either. A mode choice that is free to pick a car for such a trip produces a
car trip between two identical locations. The Java trip constraint ``SameFacilityLeisureNonMotorised`` (eqasim-java-bs,
``SameFacilityLeisureTripConstraint``) allows only walk or bicycle for a trip whose two activities share a facility
and one of which is ``leisure``. The constraint is registered in the jar but selected by name in the
``tripConstraints`` parameter of the ``DiscreteModeChoice`` config module; this module adds the name to the final
config, behind the switch ``KEY_ENABLED`` (default off: the config keeps its bytes and the jar is not opened).

Side effects are confined to ``apply`` (one byte-preserving text edit of one parameter value, see
``braunschweig.matsim.config_modules.set_param``) and to the read of the jar in ``require_constraint_in_jar``.
"""
from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

from braunschweig.matsim import config_modules

logger = logging.getLogger(__name__)

_LOG_TAG = "[loop mode rule]"

#: Switch of the prepare stage; the code default is off, the production config turns it on (configs/base_bs.yml).
KEY_ENABLED = "braunschweig.mode_choice.loop_trips_non_motorised"
DEFAULT_ENABLED = False

DMC_MODULE = "DiscreteModeChoice"
TRIP_CONSTRAINTS_PARAM = "tripConstraints"
#: Name the Java constraint is registered under (eqasim-java-bs) and selected by in ``tripConstraints``.
CONSTRAINT_NAME = "SameFacilityLeisureNonMotorised"
#: Report of the selection in the stage folder; written only when the switch is on.
REPORT_FILE_NAME = "loop_mode_rule_report.json"
#: Jar entry of the constraint class; a jar built before the constraint existed does not contain it.
CONSTRAINT_CLASS = "org/eqasim/braunschweig/mode_choice/constraints/SameFacilityLeisureTripConstraint.class"

_FIX = (f"build the eqasim-java-bs jar from a commit that contains SameFacilityLeisureTripConstraint, or set "
        f"{KEY_ENABLED} to false")


def configure(context) -> None:
    """Declare the switch with its default, unconditionally (synpp: ``execute`` reads only declared keys)."""
    context.config(KEY_ENABLED, DEFAULT_ENABLED)


def enabled(context) -> bool:
    """Whether the prepare stage selects the constraint."""
    return bool(context.config(KEY_ENABLED))


def apply(config_path) -> tuple[str, str] | None:
    """Add ``CONSTRAINT_NAME`` to the ``tripConstraints`` of the ``DiscreteModeChoice`` module of the config.

    Returns ``(old, new)``, the value of the parameter before and after, or None when the name is already selected
    (nothing is written, so a second call changes nothing). The existing names and their separator style (``,`` or
    ``, ``) are kept and the file is edited as text, so every other byte stays. Raises ``ValueError`` naming the module
    or the parameter when the config has no ``DiscreteModeChoice`` module or no ``tripConstraints`` parameter: the
    config was not generated the way this rule expects and a silently created parameter would hide that.
    """
    path = Path(config_path)
    # The generated module has parameter sets after its flat parameters, so it is read parameter by parameter.
    try:
        old = config_modules.read_param(path, DMC_MODULE, TRIP_CONSTRAINTS_PARAM)
    except ValueError as error:
        raise ValueError(f"{_LOG_TAG} cannot read {TRIP_CONSTRAINTS_PARAM!r} of module {DMC_MODULE!r}, so the trip "
                         f"constraint {CONSTRAINT_NAME} cannot be selected: {error}; check the generated config or "
                         f"set {KEY_ENABLED} to false") from error
    if old is None:
        raise ValueError(f"{_LOG_TAG} module {DMC_MODULE!r} of {path} has no parameter {TRIP_CONSTRAINTS_PARAM!r}, so "
                         f"the trip constraint {CONSTRAINT_NAME} cannot be selected; check the generated config or "
                         f"set {KEY_ENABLED} to false")
    names = [name.strip() for name in old.split(",") if name.strip()]
    if CONSTRAINT_NAME in names:
        return None
    separator = ", " if ", " in old else ","
    new = f"{old}{separator}{CONSTRAINT_NAME}" if names else CONSTRAINT_NAME
    config_modules.set_param(path, DMC_MODULE, TRIP_CONSTRAINTS_PARAM, new)
    return old, new


def write_report(report_path, config_path, jar_path, changed: tuple[str, str] | None) -> dict:
    """Write the citable record of the selection to ``report_path`` (JSON) and return it.

    ``changed`` is the return value of ``apply``. The report names the constraint, the class checked in the jar, the
    jar's file name (no directories, so the report does not depend on where the run lives), the value of
    ``tripConstraints`` before and after and ``already_selected`` (true when ``apply`` changed nothing, then before and
    after are equal). The "after" value is read back from the config on disk. Side effect: writes ``report_path``.
    """
    after = config_modules.read_param(config_path, DMC_MODULE, TRIP_CONSTRAINTS_PARAM)
    report = {"constraint": CONSTRAINT_NAME, "constraint_class_checked": CONSTRAINT_CLASS,
              "jar_file_name": Path(jar_path).name, "module": DMC_MODULE, "parameter": TRIP_CONSTRAINTS_PARAM,
              "trip_constraints_before": changed[0] if changed else after, "trip_constraints_after": after,
              "already_selected": changed is None}
    Path(report_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def require_constraint_in_jar(jar_path) -> None:
    """Raise unless the eqasim jar at ``jar_path`` contains the class of the constraint.

    Selecting a constraint name that the jar does not know fails only when the simulation loads the config, i.e. after
    the long synthesis; a jar built before the constraint existed is refused here instead. Raises
    ``FileNotFoundError`` for a missing jar and ``RuntimeError`` for a file that is no zip archive or a jar without
    ``CONSTRAINT_CLASS``; the messages name the jar and the fix. Reads the jar's entry list; no other side effect.
    """
    path = Path(jar_path)
    if not path.is_file():
        raise FileNotFoundError(f"{_LOG_TAG} the eqasim jar to check for {CONSTRAINT_CLASS} does not exist: {path}; "
                                f"{_FIX}")
    try:
        with zipfile.ZipFile(path) as jar:
            present = CONSTRAINT_CLASS in set(jar.namelist())
    except zipfile.BadZipFile as error:
        raise RuntimeError(f"{_LOG_TAG} {path} is not a zip archive, so it cannot contain {CONSTRAINT_CLASS}; "
                           f"{_FIX}") from error
    if not present:
        raise RuntimeError(f"{_LOG_TAG} the eqasim jar {path} lacks {CONSTRAINT_CLASS}, so the trip constraint "
                           f"{CONSTRAINT_NAME} cannot be selected (eqasim-bs#679); {_FIX}")
    logger.info("%s the eqasim jar %s contains %s", _LOG_TAG, path, CONSTRAINT_CLASS)
