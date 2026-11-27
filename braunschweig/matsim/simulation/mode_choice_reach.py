"""Mode choice reach settings in the final MATSim config (#678).

Three settings of the ``DiscreteModeChoice`` module decide which trips the mode choice can change: the tour finder
(``ActivityBased`` or ``IsolatedOutsideTrips``), the vehicle constraint name inside the comma-separated
``tourConstraints`` (``VehicleContinuity`` or ``BorderTourVehicleContinuity``) and the ``maximumLength`` of the
``tourFilter:TourLength`` parameter set. ``apply`` edits them in place as text (byte-preserving, see
``braunschweig.matsim.config_modules``); with the stock values (finder off, length 6) the file is unchanged.
The one initial mode choice before iteration 0 uses a second config file with its own settings. Defaults and
their reasons live in the config-key comments and the ADR of #678, not here.
"""
from __future__ import annotations

import logging
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from braunschweig.matsim import config_modules

logger = logging.getLogger(__name__)
_LOG_TAG = "[mode_choice_reach]"

KEY_LOOP_ISOLATE = "braunschweig.mode_choice.isolate_outside_trips"
KEY_LOOP_MAX_TOUR_LENGTH = "braunschweig.mode_choice.max_tour_length"
KEY_INITIAL_ISOLATE = "braunschweig.initial_mode_choice.isolate_outside_trips"
KEY_INITIAL_MAX_TOUR_LENGTH = "braunschweig.initial_mode_choice.max_tour_length"
DEFAULT_LOOP_ISOLATE, DEFAULT_LOOP_MAX_TOUR_LENGTH = False, 11
DEFAULT_INITIAL_ISOLATE, DEFAULT_INITIAL_MAX_TOUR_LENGTH = True, 11

DMC_MODULE = "DiscreteModeChoice"
TOUR_LENGTH_SET = "tourFilter:TourLength"
TOUR_FINDER_COARSE, TOUR_FINDER_ISOLATED = "ActivityBased", "IsolatedOutsideTrips"
STOCK_VEHICLE_CONSTRAINT, BORDER_VEHICLE_CONSTRAINT = "VehicleContinuity", "BorderTourVehicleContinuity"
BORDER_VEHICLE_CONSTRAINT_CLASS = "org/eqasim/braunschweig/mode_choice/constraints/BorderTourVehicleConstraint.class"
INITIAL_CONFIG_SUFFIX = "_initial_mode_choice"


@dataclass(frozen=True)
class ReachSettings:
    isolate_outside_trips: bool
    max_tour_length: int


def configure(context) -> None:
    """Declare the four config keys with their defaults (synpp: execute later reads them with one argument)."""
    context.config(KEY_LOOP_ISOLATE, DEFAULT_LOOP_ISOLATE)
    context.config(KEY_LOOP_MAX_TOUR_LENGTH, DEFAULT_LOOP_MAX_TOUR_LENGTH)
    context.config(KEY_INITIAL_ISOLATE, DEFAULT_INITIAL_ISOLATE)
    context.config(KEY_INITIAL_MAX_TOUR_LENGTH, DEFAULT_INITIAL_MAX_TOUR_LENGTH)


def parse_bool(key: str, value) -> bool:
    """A real bool, or the text ``true``/``false`` (any case, e.g. from a command-line override); else ValueError."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValueError(f"{_LOG_TAG} config key {key} must be true or false, got {value!r}")


def _parse_length(key: str, value) -> int:
    """A whole number >= 1 (an int or its text, e.g. from an override); else ValueError."""
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            raise ValueError(f"{_LOG_TAG} config key {key} must be a whole number >= 1, got {value!r}") from None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{_LOG_TAG} config key {key} must be a whole number >= 1, got {value!r}")
    return value


def loop_settings(context) -> ReachSettings:
    return ReachSettings(parse_bool(KEY_LOOP_ISOLATE, context.config(KEY_LOOP_ISOLATE)),
                         _parse_length(KEY_LOOP_MAX_TOUR_LENGTH, context.config(KEY_LOOP_MAX_TOUR_LENGTH)))


def initial_settings(context) -> ReachSettings:
    return ReachSettings(parse_bool(KEY_INITIAL_ISOLATE, context.config(KEY_INITIAL_ISOLATE)),
                         _parse_length(KEY_INITIAL_MAX_TOUR_LENGTH, context.config(KEY_INITIAL_MAX_TOUR_LENGTH)))


def _read_dmc_param(config_path, name: str) -> str:
    for module in ET.parse(str(config_path)).getroot().findall("module"):
        if module.get("name") == DMC_MODULE:
            for param in module.findall("param"):
                if param.get("name") == name:
                    return param.get("value")
    raise ValueError(f"{config_path}: no parameter {name!r} in module {DMC_MODULE!r}")


def _swap_token(constraints: str, old: str, new: str) -> str | None:
    """``constraints`` with the token ``old`` replaced by ``new``, every other character kept; None without ``old``."""
    pattern = r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(old)
    return re.sub(pattern, new, constraints) if re.search(pattern, constraints) else None


def apply(config_path, settings: ReachSettings) -> dict:
    """Set the tour finder, the vehicle constraint and the tour length limit; return ``{param: (old, new)}`` of
    the values that changed (empty when the file already has these settings and is left byte-identical).

    Raises ``ValueError`` when the finer rule is selected and ``tourConstraints`` holds neither
    ``VehicleContinuity`` nor ``BorderTourVehicleContinuity`` (nothing is skipped silently), or when it holds both
    (ambiguous); nothing is written in either case.
    """
    changes: dict = {}
    finder = TOUR_FINDER_ISOLATED if settings.isolate_outside_trips else TOUR_FINDER_COARSE
    constraints = _read_dmc_param(config_path, "tourConstraints")
    wanted, other = ((BORDER_VEHICLE_CONSTRAINT, STOCK_VEHICLE_CONSTRAINT) if settings.isolate_outside_trips
                     else (STOCK_VEHICLE_CONSTRAINT, BORDER_VEHICLE_CONSTRAINT))
    if (_swap_token(constraints, STOCK_VEHICLE_CONSTRAINT, STOCK_VEHICLE_CONSTRAINT) is not None
            and _swap_token(constraints, BORDER_VEHICLE_CONSTRAINT, BORDER_VEHICLE_CONSTRAINT) is not None):
        raise ValueError(f"{config_path}: parameter 'tourConstraints' of module {DMC_MODULE!r} holds both "
                         f"{STOCK_VEHICLE_CONSTRAINT} and {BORDER_VEHICLE_CONSTRAINT}, which is ambiguous: "
                         f"{constraints!r}")
    swapped = _swap_token(constraints, other, wanted)
    if swapped is None and settings.isolate_outside_trips and _swap_token(constraints, wanted, wanted) is None:
        raise ValueError(f"{config_path}: parameter 'tourConstraints' of module {DMC_MODULE!r} holds neither "
                         f"{STOCK_VEHICLE_CONSTRAINT} nor {BORDER_VEHICLE_CONSTRAINT}: {constraints!r}")

    old = _read_dmc_param(config_path, "tourFinder")
    if old != finder:  # no write (and no mtime change) when the value already matches
        config_modules.set_param(config_path, DMC_MODULE, "tourFinder", finder)
        changes["tourFinder"] = (old, finder)
    if swapped is not None and swapped != constraints:
        config_modules.set_param(config_path, DMC_MODULE, "tourConstraints", swapped)
        changes["tourConstraints"] = (constraints, swapped)
    length = str(int(settings.max_tour_length))
    old = config_modules.set_parameterset_param(config_path, DMC_MODULE, TOUR_LENGTH_SET, "maximumLength", length)
    if old != length:
        changes["maximumLength"] = (old, length)
    for param, (before, after) in changes.items():
        logger.info("%s %s: %s -> %s in %s", _LOG_TAG, param, before, after, config_path)
    return changes


def write_initial_config(config_path, settings: ReachSettings) -> Path:
    """Write ``<stem>_initial_mode_choice.xml`` next to the config with ``settings`` applied and return it.

    The config itself is not changed. When ``settings`` already equal the config's values no second file is
    kept and ``config_path`` is returned.
    """
    path = Path(config_path)
    second = path.with_name(path.stem + INITIAL_CONFIG_SUFFIX + path.suffix)
    shutil.copyfile(path, second)
    try:
        changed = bool(apply(second, settings))
    except Exception:
        second.unlink()
        raise
    if not changed:
        second.unlink()
        logger.info("%s the initial mode choice uses the final config unchanged", _LOG_TAG)
        return path
    return second


def require_border_vehicle_constraint(jar_path) -> None:
    """Raise ``RuntimeError`` naming ``BORDER_VEHICLE_CONSTRAINT_CLASS`` when the eqasim jar lacks it.

    A jar built before the border tour vehicle constraint (#678) would fail at run time when the config names
    ``BorderTourVehicleContinuity``; fail at prepare instead. ``FileNotFoundError`` for a missing jar.
    """
    path = Path(jar_path)
    if not path.is_file():
        raise FileNotFoundError(f"{_LOG_TAG} the eqasim jar to check for the border vehicle constraint does not "
                                f"exist: {path}")
    try:
        with zipfile.ZipFile(path) as jar:
            names = set(jar.namelist())
    except zipfile.BadZipFile as error:
        raise RuntimeError(f"{_LOG_TAG} cannot read the eqasim jar {path} as a zip archive: {error}") from error
    if BORDER_VEHICLE_CONSTRAINT_CLASS not in names:
        raise RuntimeError(f"{_LOG_TAG} the eqasim jar {path} does not contain {BORDER_VEHICLE_CONSTRAINT_CLASS}; "
                           "rebuild the jar from an eqasim-java-bs revision with the border tour vehicle "
                           "constraint, see eqasim-bs#678")
    logger.info("%s the eqasim jar %s contains the border tour vehicle constraint", _LOG_TAG, path)
