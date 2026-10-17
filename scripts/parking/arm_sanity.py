"""Per-arm sanity check of the parking cost zones v3 comparison runs (issue #436, Part C, rulings R-C-3 and R-C-5).

An analysis aid, never a pipeline stage and never a model input. Before an arm of the comparison is compared with the others it
has to prove that the intended model acted in it: a run whose overlay did not arrive, whose jar lacks the parking package or
whose zone release differs from the install list would otherwise be compared as if it were the arm it is named after. The
script reads the arm's RESOLVED config (``<working_directory>/.merged_config.yml``: the base with the overlay merged), the log(s)
of the run, optionally the MATSim log and the inputs report, and the outcome report of ONE iteration, runs the checks below,
prints one line per check and exits 0 (sane) or 1 (the first failing check is named last, with its evidence). Exit 2 is an input
error (a file that is missing, empty, undecodable or no YAML/JSON, a config key that is absent): nothing is defaulted silently.
A sane arm is no validation of the model: this is a plausibility check of the run's mechanics, and convergence or a green check
is not agreement with observed data.

Arm kinds (``--arm-kind``, default ``v3``)
    ``v3``: the arms with the zone model (checks 1-6 below). ``off`` and ``legacy`` (ruling R-C-5): the arms WITHOUT the zone
    model, which must prove that it did NOT act (see the last section). Same exit codes and line format for every kind.

Where the evidence lives (the stages are cached, so a log only holds the lines of the stages that EXECUTED in its run)
    * ``--log`` is repeatable and its ORDER MATTERS: the first ``--log`` is the arm's own log, every further ``--log`` is the log
      of an EARLIER arm. The arm's own log alone supplies the run-time evidence of THIS run: the run check line (check 1), the
      Java startup lines (check 2; ``--java-log``, if given, supplies them instead) and the NO_FEASIBLE_OPTION warning line.
      All given logs, the NEWEST (file modification time) first, supply only the CACHED-stage evidence: the EV lines, the
      free-parking draw line and the log fallback of the zone release / prepared inputs lines (population, zones and prepare
      stage, executed once and shared by several arms). Every output line names the log that supplied the matched line. The
      caller is responsible for passing only earlier logs of runs whose cached stage is the one this arm used.
    * ``--inputs-report`` is the PERSISTENT record of the prepare stage: ``<output_prefix>parking_inputs_report.json``, written
      by ``_write_parking_inputs`` of ``braunschweig/matsim/simulation/prepare.py`` into the stage directory
      ``<working_directory>/matsim.simulation.prepare__<hash>.cache/`` and copied by ``matsim.output.copy_parking_inputs`` to
      ``<output_path>/<output_prefix>parking_inputs_report.json``. It holds the sources with the FULL SHA-256, the cost factor, the
      option price coefficient, both decay lengths and the tariff model file name (which carries the snapshot date). With it,
      checks 2 (Python side) and 3 need no log line; WITHOUT it they fall back to the log lines, and the output says so.

Checks of the kind ``v3`` (in this order; each keys on the exact production log line named here)
    1. ``module_active``: the log has the INFO line of ``braunschweig.parking.runtime_checks.require_parking_outcomes``
       ("[parking] run check: jar contains the parking package; outcomes written for iteration N"; written by the run stage,
       so it is in this arm's own log), N equals the iteration in the file name of ``--report`` (``N.parking_outcomes.csv``;
       pass the report of the last iteration), and the report header is exactly ``REPORT_V4_COLUMNS`` of
       ``scripts/parking/compare_parking_targets.py`` (imported, not copied: one source of the Java header).
    2. ``config_echo``: the values the run used equal the arm's resolved config. Python side: the inputs report, else the line
       "[parking] prepared inputs: ..." of ``_write_parking_inputs`` (cost factor, option price coefficient, decay length,
       commuter decay length, and the snapshot date inside the file name ``braunschweig.parking.tariff_export.
       tariff_model_file_name``; the prepare stage is cached too, so this line can be missing in a log). Java side (always a log
       line, written at every MATSim start): ``ParkingOutcomeReportListener.notifyStartup`` ("[parking] parking cost factor X
       (braunschweigParking.costFactor ...") and ``ParkingOptionPriceBetaCheck.notifyStartup`` ("[parking] option price coefficient
       X per EUR equals ..."), searched in ``--java-log`` or, without it, in the first (own) ``--log`` only. An absent
       ``parking_cost_utility_factor`` in the config means 1.0, the pipeline default (``PARKING_DEFAULTS`` of prepare.py); every
       other key must be present.
    3. ``sources``: the 12-character prefixes of the sources equal ``--expected-sources`` (JSON: dataset -> 12 hex characters,
       the install list of R-C-2): the prefixes of the FULL SHA-256 of the inputs report, else the zone release line of
       ``braunschweig.parking.zones_stage`` ("[parking] zone release: ...; sources <dataset> <prefix>, ...", which only an
       EXECUTED zones stage logs). Datasets the install list does not name are not checked.
    4. ``ev_exemption``: ``braunschweig.parking.attach.attach_ev_exemption`` (population stage; the line may come from the log of
       an earlier arm that executed the stage this arm reuses). The log that supplies the line must be ONE log: with
       ``parking_ev_exemption_enabled`` true it has the headline ("EV exemption: N of M car owners ...") with N > 0 and the
       basis line, and NOT the warning "has no 'powertrain' column"; with false it has "EV exemption is disabled" and no
       headline. Any OTHER supplied log that has the line of the opposite setting is named in a NOTE; it is a FAIL only when
       that log is the arm's own (first) log. The evidence for a working powertrain column is N > 0 together with the
       ABSENCE of that warning: the basis line is printed regardless and only shows that the attach ran to its end.
    5. ``no_feasible_option``: from the v4 report, per zone group, ``no_feasible_option_count`` over the priced stays (every
       row but NO_ZONE, as ``ParkingOutcomeReportListener.pricedStays`` counts them). The two city-centre zones ``bs_zone_ia``
       and ``bs_zone_ib`` are judged one by one and FAIL above 1 % (exactly 1 % passes). bh_*, se_am_markt, wob_area_*,
       ``bs_zone_ia_sued``, ``bs_zone_ii`` and every other zone are reported groups OUTSIDE the hard stop and never fail. The
       count is of pricing calls (car alternatives evaluated), not of persons or trips. When a log carries the Java warning of
       ``noFeasibleOptionWarning`` its last line is quoted next to the rates.
    6. ``free_share`` (report only): the realised share of EMPLOYER_FREE and of all no-charge outcomes among the work and
       education pricing calls in zones (an outcome rate of CALLS), next to the configured
       ``parking_workplace_free_share_shift`` and next to the person-level draw that ``attach`` logs ("free-parking draw ...:
       N/M persons ... have a work/education activity in a street_paid or resident_zone zone, K of them ... park free", from
       whichever log supplies it; it is a population-stage line). A logged shift that differs from the configured one is noted.

Checks of the kinds ``off`` and ``legacy`` (ruling R-C-5; --report, --expected-sources, --inputs-report and the Java log are not
required and not used)
    * ``arm_kind_config``: ``parking_zones_enabled`` is false; ``enable_urban_parking`` is false for ``off`` and true for
      ``legacy``. The legacy ring rests on the CONFIG FLAG ONLY: it is applied in the plan writer and leaves no log line or output
      file that this script could check, and the output line says so.
    * ``no_parking_log_lines``: none of the pipeline logs has a "[parking] run check", "[parking] prepared inputs", "[parking] zone
      release", "[parking] EV exemption:" or "[parking] EV exemption is disabled" line; the Java log (else the pipeline logs) has
      no "[parking] parking cost factor" and no "[parking] option price coefficient" line. Pass only logs of THIS arm: a log of
      a zone arm contains these lines by design.
    * ``no_parking_outputs`` (only with ``--simulation-output``, the MATSim ``simulation_output`` directory): no
      ``ITERS/it.N/N.parking_outcomes.csv`` and the ``output_config.xml`` of the run (written by MATSim as OUTPUT_PREFIX
      "output_" + ``Controler.DefaultFiles.config`` "config.xml"; ``.gz`` accepted) does not ENABLE the module
      ``braunschweigParking``. The module may be PRESENT with
      ``enabled=false``: the typed Java group is registered for every run by ``BraunschweigConfigurator``.

``--zone-groups`` (YAML or JSON) maps zone-id patterns (``fnmatch``, first match in file order) to group names and replaces the
default grouping; it must map ``bs_zone_ia`` and ``bs_zone_ib`` to groups of exactly those names (they are the hard-stop
groups), and an id that matches no pattern belongs to the group ``other``.

Usage (repository root)::

    python scripts/parking/arm_sanity.py --arm-config <wd>/.merged_config.yml --log <pipeline log> [--log <earlier arm log>] \
        --java-log <matsim log> --inputs-report <output_path>/<prefix>parking_inputs_report.json \
        --report <simulation_output>/ITERS/it.60/60.parking_outcomes.csv --expected-sources expected_sources.json
    python scripts/parking/arm_sanity.py --arm-kind off --arm-config <...> --log <pipeline log> \
        [--simulation-output <simulation_output>]

Reads only; never writes, never pushes anything anywhere.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import fnmatch
import gzip
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import NamedTuple, Sequence

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.matsim import config_modules  # noqa: E402
from braunschweig.parking import cost, runtime_checks  # noqa: E402

COMPARE_MODULE_NAME = "compare_parking_targets_for_arm_sanity"


def _load_compare_script():
    """Load ``compare_parking_targets.py`` (the scripts are no package) for the report header and the strict report reader."""
    spec = importlib.util.spec_from_file_location(COMPARE_MODULE_NAME, SCRIPTS_DIR / "compare_parking_targets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[COMPARE_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


compare = _load_compare_script()
REPORT_V4_COLUMNS = compare.REPORT_V4_COLUMNS

PASS, FAIL, INFO = "PASS", "FAIL", "INFO"
ARM_KINDS = ("v3", "off", "legacy")

#: Hard stop (review focus 1 of the plan): NO_FEASIBLE_OPTION above this share of the priced stays in percent in Ia or Ib.
HARD_STOP_MAXIMUM_PERCENT = 1
HARD_STOP_GROUPS = ("bs_zone_ia", "bs_zone_ib")
OTHER_GROUP = "other"
#: Default zone groups: (fnmatch pattern, group), first match wins; unmatched zones are ``OTHER_GROUP`` (e.g. wob_tarifzone_*).
#: Only ``HARD_STOP_GROUPS`` can fail; every other group, ``bs_zone_ia_sued`` and ``bs_zone_ii`` included, is reported.
DEFAULT_ZONE_GROUPS = (("bs_zone_ia", "bs_zone_ia"), ("bs_zone_ib", "bs_zone_ib"), ("bs_zone_ia_sued", "bs_zone_ia_sued"),
                       ("bs_zone_ii", "bs_zone_ii"), ("bh_*", "bh"), ("se_am_markt", "se_am_markt"),
                       ("wob_area_*", "wob_area"))
WORK_EDUCATION_PURPOSES = tuple(sorted(cost.COMMUTER_PURPOSES))
#: Outcomes that charge nothing by their own rule and make up the free share of work and education stays (the four of the
#: ``free_share_work_education_*`` metrics of compare_parking_targets.py; EV_EXEMPT is no workplace free share).
NO_CHARGE_OUTCOMES = (cost.EMPLOYER_FREE, cost.RESIDENT_FREE, cost.OUTSIDE_FEE_HOURS, cost.FREE_WITHIN_LIMIT)
SOURCE_PREFIX_PATTERN = re.compile(r"[0-9a-f]{12}")
#: The pipeline default of ``parking_cost_utility_factor`` (PARKING_DEFAULTS of braunschweig/matsim/simulation/prepare.py).
COST_FACTOR_DEFAULT = 1.0
#: The outcome report of iteration N is ``N.parking_outcomes.csv`` (ParkingOutcomeReportListener.FILE_NAME with the iteration
#: prefix of OutputDirectoryHierarchy.getIterationFilename).
REPORT_FILE_NAME = re.compile(r"(\d+)\.parking_outcomes\.csv")
#: The MATSim config of a finished run: ``Controler.OUTPUT_PREFIX`` ("output_") + ``Controler.DefaultFiles.config`` ("config.xml"),
#: MATSim 2026.0 (org.matsim.core.controler.Controler).
OUTPUT_CONFIG_FILE_NAME = "output_config.xml"

# The production log formats this script keys on; tests/test_arm_sanity.py pins every fragment to the source that writes it.
RUN_CHECK = re.compile(r"\[parking\] run check: jar contains the parking package; outcomes written for iteration (\d+)")
PREPARED_INPUTS = re.compile(r"\[parking\] prepared inputs:")
PREPARED_COST_FACTOR = re.compile(r"cost factor (\S+?),")
PREPARED_BETA = re.compile(r"option price coefficient (\S+) per EUR")
PREPARED_DECAY = re.compile(r"(?<!commuter )decay length (\S+) m \(")
PREPARED_COMMUTE_DECAY = re.compile(r"commuter decay length (\S+) m \(")
SNAPSHOT_IN_FILE_NAME = re.compile(r"parking_tariffs_(\d{4}-\d{2}-\d{2})\.json")
JAVA_COST_FACTOR = re.compile(r"\[parking\] parking cost factor (\S+) \(")
JAVA_BETA = re.compile(r"\[parking\] option price coefficient (\S+) per EUR equals the magnitude of the mode choice cost")
JAVA_BETA_ANY = re.compile(r"\[parking\] option price coefficient")
JAVA_BETA_SKIPPED = re.compile(r"\[parking\] option price coefficient check skipped")
ZONE_RELEASE = re.compile(r"\[parking\] zone release: .*; sources (.+)$")
EV_DISABLED = re.compile(r"\[parking\] EV exemption is disabled")
EV_HEADLINE = re.compile(r"\[parking\] EV exemption: (\d+) of (\d+) car owners")
EV_BASIS = re.compile(r"\[parking\] EV exemption basis.*powertrains of the car vehicles:")
EV_NO_POWERTRAIN_COLUMN = re.compile(r"\[parking\] EV exemption: .*has no 'powertrain' column")
FREE_DRAW = re.compile(r"\[parking\] free-parking draw \(RandomState\(random_seed \d+ \+ \d+\), shift ([+-][\d.]+)\): "
                       r"(\d+)/(\d+) persons \([\d.]+ %\) have a work/education activity in a street_paid or resident_zone "
                       r"zone, (\d+) of them \(([\d.]+) %\) park free")
NO_FEASIBLE_WARNING = re.compile(r"\[parking\] it (\d+): (\d+) of (\d+) priced stays \(([\d.]+) %\) have no feasible "
                                 r"parking option")
#: Lines that only an arm WITH the zone model writes; none may occur in the logs of an ``off`` or ``legacy`` arm.
FORBIDDEN_PIPELINE_LINES = (
    ("[parking] run check", re.compile(r"\[parking\] run check")),
    ("[parking] prepared inputs", PREPARED_INPUTS),
    ("[parking] zone release", re.compile(r"\[parking\] zone release")),
    ("[parking] EV exemption:", re.compile(r"\[parking\] EV exemption:")),
    ("[parking] EV exemption is disabled", EV_DISABLED),
)
FORBIDDEN_JAVA_LINES = (
    ("[parking] parking cost factor", re.compile(r"\[parking\] parking cost factor")),
    ("[parking] option price coefficient", JAVA_BETA_ANY),
)


class InputError(Exception):
    """A CLI input that is missing or unusable (exit code 2): never defaulted, never turned into a failed check."""


class CheckResult(NamedTuple):
    """One check: ``status`` is PASS, FAIL or INFO (report only); ``evidence`` is one line of plain text."""

    name: str
    status: str
    evidence: str

    @property
    def ok(self) -> bool:
        return self.status != FAIL


class LogSource(NamedTuple):
    """The lines of one log and the name that the output uses for it."""

    name: str
    lines: Sequence[str]


class Found(NamedTuple):
    """A line that matched, the match object and the name of the log that supplied it."""

    match: re.Match
    log: str


class ArmConfig(NamedTuple):
    """The values of the arm's resolved config that the checks of the kind ``v3`` compare with the logs."""

    snapshot_date: str
    garage_decay_m: float
    garage_decay_commute_m: float
    option_price_beta_u_per_eur: float
    cost_utility_factor: float
    ev_exemption_enabled: bool
    workplace_free_share_shift: float


class InputsReport(NamedTuple):
    """The fields of ``<prefix>parking_inputs_report.json`` that the checks use."""

    name: str
    source_sha256: dict[str, str]
    cost_utility_factor: float
    option_price_beta_u_per_eur: float
    garage_decay_m: float
    garage_decay_commute_m: float
    tariff_model_name: str


# ----------------------------------------------------------------------------------------------------------- the inputs


def _require_file(path, what: str) -> Path:
    path = Path(path)
    if not path.is_file():
        raise InputError(f"{what} {path} does not exist or is no file")
    return path


def read_log_lines(path) -> list[str]:
    """The lines of a log. Undecodable bytes are replaced: a log is evidence text, not a data input."""
    return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()


def load_logs(paths: Sequence) -> list[LogSource]:
    """The logs in the ORDER GIVEN: the first is the arm's own log, the others are logs of earlier arms."""
    return [LogSource(str(path), read_log_lines(path)) for path in (_require_file(path, "log") for path in paths)]


def newest_first(logs: Sequence[LogSource]) -> list[LogSource]:
    """The logs NEWEST first by file modification time (stable: equal times keep the order given)."""
    return sorted(logs, key=lambda log: Path(log.name).stat().st_mtime, reverse=True)


def find_last(logs: Sequence[LogSource], pattern: re.Pattern):
    """The last line of the first (newest) log with a line matching ``pattern`` as a ``Found``, or None."""
    for log in logs:
        for line in reversed(log.lines):
            match = pattern.search(line)
            if match:
                return Found(match, log.name)
    return None


def _log_names(logs: Sequence[LogSource]) -> str:
    return ", ".join(log.name for log in logs) or "no log"


def _read_yaml(path, what: str):
    """The parsed YAML/JSON of ``path``; ``InputError`` for a missing, undecodable or malformed file."""
    path = _require_file(path, what)
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise InputError(f"{what} {path} is not UTF-8 text: {error}") from error
    except yaml.YAMLError as error:
        raise InputError(f"{what} {path} is no valid YAML: {error}") from error


def _config_section(path) -> tuple[dict, Path]:
    """The ``config:`` mapping of the resolved config (``.merged_config.yml`` of ``run_synpp.py``), or the flat mapping."""
    document = _read_yaml(path, "arm config")
    if not isinstance(document, dict):
        raise InputError(f"{path}: the resolved config must be a YAML mapping, found {type(document).__name__}")
    return (document["config"] if isinstance(document.get("config"), dict) else document), Path(path)


def _required(config: dict, key: str, path):
    if key not in config or config[key] is None:
        raise InputError(f"{path}: the resolved config has no value for {key}; the arm cannot be checked against an "
                         "absent key (pass the file written by run_synpp.py, the base with the overlay merged)")
    return config[key]


def _number(config: dict, key: str, path) -> float:
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{path}: {key} must be a number, found {value!r}")
    return float(value)


def _boolean(config: dict, key: str, path) -> bool:
    value = _required(config, key, path)
    if not isinstance(value, bool):
        raise InputError(f"{path}: {key} must be true or false, found {value!r}")
    return value


def load_arm_config(path) -> ArmConfig:
    """Read the values of the kind ``v3`` from the resolved config. Raises ``InputError`` for a missing key or a value of the
    wrong type."""
    config, path = _config_section(path)
    snapshot = _required(config, "parking_tariff_snapshot_date", path)
    snapshot = snapshot.isoformat() if isinstance(snapshot, (datetime.date, datetime.datetime)) else str(snapshot)
    enabled = _boolean(config, "parking_ev_exemption_enabled", path)
    for key in ("parking_garage_decay_m", "parking_garage_decay_commute_m", "parking_option_price_beta_u_per_eur",
                "parking_workplace_free_share_shift"):
        _required(config, key, path)
    cost_factor = _number(config, "parking_cost_utility_factor", path) if config.get(
        "parking_cost_utility_factor") is not None else COST_FACTOR_DEFAULT
    return ArmConfig(snapshot, _number(config, "parking_garage_decay_m", path),
                     _number(config, "parking_garage_decay_commute_m", path),
                     _number(config, "parking_option_price_beta_u_per_eur", path), cost_factor, enabled,
                     _number(config, "parking_workplace_free_share_shift", path))


def load_arm_flags(path) -> tuple[bool, bool]:
    """``(parking_zones_enabled, enable_urban_parking)`` of the resolved config, both required booleans."""
    config, path = _config_section(path)
    return _boolean(config, "parking_zones_enabled", path), _boolean(config, "enable_urban_parking", path)


def load_expected_sources(path) -> dict[str, str]:
    """The install list: dataset -> 12 lowercase hex characters (the prefix of the SHA-256 the zones stage logs)."""
    path = _require_file(path, "expected-sources file")
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise InputError(f"expected sources {path} is not UTF-8 text: {error}") from error
    except json.JSONDecodeError as error:
        raise InputError(f"expected sources {path} is no JSON: {error}") from error
    if not isinstance(expected, dict) or not expected:
        raise InputError(f"expected sources {path} must be a non-empty JSON object dataset -> 12-character hash prefix")
    for dataset, prefix in expected.items():
        if not isinstance(prefix, str) or not SOURCE_PREFIX_PATTERN.fullmatch(prefix):
            raise InputError(f"expected sources {path}: the prefix of {dataset!r} must be 12 lowercase hex characters, found "
                             f"{prefix!r}")
    return expected


def load_inputs_report(path) -> InputsReport:
    """The prepare stage's ``<prefix>parking_inputs_report.json``; ``InputError`` unless every field the checks need is there."""
    path = _require_file(path, "inputs report")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise InputError(f"inputs report {path} is not UTF-8 text: {error}") from error
    except json.JSONDecodeError as error:
        raise InputError(f"inputs report {path} is no JSON: {error}") from error
    if not isinstance(report, dict):
        raise InputError(f"inputs report {path} must be a JSON object")
    numbers = {}
    for key in ("parking_cost_utility_factor", "option_price_beta_u_per_eur", "garage_decay_m", "garage_decay_commute_m"):
        value = report.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InputError(f"inputs report {path}: {key} must be a number, found {value!r}")
        numbers[key] = float(value)
    sources = report.get("sources")
    if not isinstance(sources, list) or not sources or not all(
            isinstance(entry, dict) and isinstance(entry.get("source_id"), str) and isinstance(entry.get("sha256"), str)
            for entry in sources):
        raise InputError(f"inputs report {path}: sources must be a non-empty list of objects with source_id and sha256")
    files = report.get("parking_input_files")
    if not isinstance(files, list) or not files or not isinstance(files[0], str):
        raise InputError(f"inputs report {path}: parking_input_files must list the tariff model file name first")
    return InputsReport(path.name, {entry["source_id"]: entry["sha256"] for entry in sources},
                        numbers["parking_cost_utility_factor"], numbers["option_price_beta_u_per_eur"],
                        numbers["garage_decay_m"], numbers["garage_decay_commute_m"], files[0])


def load_zone_groups(path=None) -> tuple[tuple[str, str], ...]:
    """The ordered (pattern, group) pairs: the default, or those of the YAML/JSON mapping in ``path``."""
    if path is None:
        return DEFAULT_ZONE_GROUPS
    mapping = _read_yaml(path, "zone-groups file")
    if not isinstance(mapping, dict) or not mapping or not all(
            isinstance(pattern, str) and isinstance(group, str) for pattern, group in mapping.items()):
        raise InputError(f"zone groups {path} must be a non-empty mapping of zone-id pattern -> group name (both text)")
    groups = tuple(mapping.items())
    for zone_id in HARD_STOP_GROUPS:
        if zone_group(zone_id, groups) != zone_id:
            raise InputError(f"zone groups {path}: the zone {zone_id} must map to a group named {zone_id} (a hard-stop group), "
                             f"it maps to {zone_group(zone_id, groups)!r}")
    return groups


def zone_group(zone_id: str, groups) -> str:
    """The group of the first pattern that matches ``zone_id`` (``fnmatch``, case-sensitive), else ``OTHER_GROUP``."""
    for pattern, group in groups:
        if fnmatch.fnmatchcase(zone_id, pattern):
            return group
    return OTHER_GROUP


def _report_header(path) -> list[str]:
    """The header row of the report. ``InputError`` for undecodable text or a file without any header line."""
    try:
        with Path(path).open(newline="", encoding="utf-8") as stream:
            header = next(csv.reader(stream), [])
    except UnicodeDecodeError as error:
        raise InputError(f"outcome report {path} is not UTF-8 text: {error}") from error
    if not header:
        raise InputError(f"outcome report {path} is empty (no header line)")
    return header


# ------------------------------------------------------------------------------------------------------ the checks, v3


def check_module_active(logs: Sequence[LogSource], report_path) -> CheckResult:
    """Check 1: the run check line is in a log, its iteration is that of the report file, the header is exactly v4."""
    problems = []
    run_check = find_last(logs, RUN_CHECK)
    if run_check is None:
        problems.append("no log has a '[parking] run check: jar contains the parking package; outcomes written for iteration N' "
                        f"line (the parking module did not price this run, or the run stopped before its end); logs: "
                        f"{_log_names(logs)}")
    file_name = REPORT_FILE_NAME.fullmatch(Path(report_path).name)
    if file_name is None:
        problems.append(f"the report file name {Path(report_path).name!r} is not N.parking_outcomes.csv, so its iteration "
                        "cannot be compared with the run check")
    elif run_check is not None and int(run_check.match.group(1)) != int(file_name.group(1)):
        problems.append(f"the run check line says the last iteration was {run_check.match.group(1)} but the report is of "
                        f"iteration {file_name.group(1)}: pass the report of the last iteration")
    header = _report_header(report_path)
    if header != list(REPORT_V4_COLUMNS):
        problems.append(f"the report header {header} is not the v4 header {list(REPORT_V4_COLUMNS)} (exact columns and order)")
    if problems:
        return CheckResult("module_active", FAIL, "; ".join(problems))
    return CheckResult("module_active", PASS, f"[parking] run check line for iteration {run_check.match.group(1)} [from "
                       f"{run_check.log}], equal to the iteration of the report; report header is the v4 header "
                       f"({len(REPORT_V4_COLUMNS)} columns)")


def _logged_float(line: str, pattern, description: str, problems: list[str]):
    match = pattern.search(line)
    if match is None:
        problems.append(f"{description}: not found in the line {line[:160]!r}")
        return None
    try:
        return float(match.group(1))
    except ValueError:
        problems.append(f"{description}: {match.group(1)!r} is no number")
        return None


def _echo_python_side_from_report(report: InputsReport, config: ArmConfig, problems: list[str], echoed: list[str]) -> None:
    for description, logged, expected in (
            ("parking_cost_utility_factor", report.cost_utility_factor, config.cost_utility_factor),
            ("parking_option_price_beta_u_per_eur", report.option_price_beta_u_per_eur, config.option_price_beta_u_per_eur),
            ("parking_garage_decay_m", report.garage_decay_m, config.garage_decay_m),
            ("parking_garage_decay_commute_m", report.garage_decay_commute_m, config.garage_decay_commute_m)):
        if logged != expected:
            problems.append(f"{description} in the inputs report {report.name} is {logged:g} but the arm's config has "
                            f"{expected:g}")
        else:
            echoed.append(f"{description} {logged:g}")
    snapshot = SNAPSHOT_IN_FILE_NAME.search(report.tariff_model_name)
    if snapshot is None:
        problems.append(f"tariff snapshot date: no parking_tariffs_<date>.json file name in the inputs report "
                        f"({report.tariff_model_name!r})")
    elif snapshot.group(1) != config.snapshot_date:
        problems.append(f"tariff snapshot date of the inputs report is {snapshot.group(1)} but the arm's config "
                        f"parking_tariff_snapshot_date has {config.snapshot_date}")
    else:
        echoed.append(f"snapshot {snapshot.group(1)}")


def _echo_python_side_from_log(logs: Sequence[LogSource], config: ArmConfig, problems: list[str], echoed: list[str]) -> str:
    prepared = find_last(logs, PREPARED_INPUTS)
    if prepared is None:
        problems.append("without --inputs-report the Python side needs the '[parking] prepared inputs:' line, which only an "
                        "EXECUTED prepare stage logs, and no log has it (the stage was cached: pass the inputs report of the "
                        f"arm or the log of the run that executed the stage); logs: {_log_names(logs)}")
        return "no prepared-inputs line"
    line = prepared.match.string
    # The preparation line formats with %g (6 significant digits) except the decay length (%s of the float): compare each with
    # the config value in the same format, so a rounding of the log is no mismatch and a real difference is.
    for description, pattern, expected, exact in (
            ("parking_cost_utility_factor", PREPARED_COST_FACTOR, config.cost_utility_factor, False),
            ("parking_option_price_beta_u_per_eur", PREPARED_BETA, config.option_price_beta_u_per_eur, False),
            ("parking_garage_decay_m", PREPARED_DECAY, config.garage_decay_m, True),
            ("parking_garage_decay_commute_m", PREPARED_COMMUTE_DECAY, config.garage_decay_commute_m, False)):
        logged = _logged_float(line, pattern, description, problems)
        if logged is None:
            continue
        if logged != (expected if exact else float("%g" % expected)):
            problems.append(f"{description} logged {logged:g} but the arm's config has {expected:g}")
        else:
            echoed.append(f"{description} {logged:g}")
    snapshot = SNAPSHOT_IN_FILE_NAME.search(line)
    if snapshot is None:
        problems.append("tariff snapshot date: no parking_tariffs_<date>.json file name in the prepared inputs line")
    elif snapshot.group(1) != config.snapshot_date:
        problems.append(f"tariff snapshot date logged {snapshot.group(1)} but the arm's config parking_tariff_snapshot_date "
                        f"has {config.snapshot_date}")
    else:
        echoed.append(f"snapshot {snapshot.group(1)}")
    return f"log lines [from {prepared.log}] (FALLBACK: no --inputs-report given)"


def check_config_echo(logs: Sequence[LogSource], java_logs: Sequence[LogSource], config: ArmConfig,
                      inputs_report: InputsReport | None = None) -> CheckResult:
    """Check 2: the values the run used equal the arm's resolved config (see the module doc for the evidence and its sources)."""
    problems, echoed = [], []
    if inputs_report is not None:
        _echo_python_side_from_report(inputs_report, config, problems, echoed)
        python_source = f"inputs report {inputs_report.name}"
    else:
        python_source = _echo_python_side_from_log(logs, config, problems, echoed)
    cost_line = find_last(java_logs, JAVA_COST_FACTOR)
    if cost_line is None:
        problems.append("the Java startup line '[parking] parking cost factor X (braunschweigParking.costFactor ...' (costFactor) "
                        f"is absent: the jar did not log the factor it applies; logs: {_log_names(java_logs)}")
    elif float(cost_line.match.group(1)) != config.cost_utility_factor:
        problems.append(f"Java costFactor logged {cost_line.match.group(1)} but the arm's config parking_cost_utility_factor "
                        f"has {config.cost_utility_factor:g}")
    else:
        echoed.append(f"Java costFactor {cost_line.match.group(1)} [from {cost_line.log}]")
    beta_line = find_last(java_logs, JAVA_BETA)
    if beta_line is None:
        skipped = find_last(java_logs, JAVA_BETA_SKIPPED) is not None
        problems.append("the Java startup line '[parking] option price coefficient X per EUR equals ...' is absent"
                        + (" (the check was skipped: the tariff model has a schema before 5)" if skipped else ""))
    elif float(beta_line.match.group(1)) != config.option_price_beta_u_per_eur:
        problems.append(f"Java option price coefficient logged {beta_line.match.group(1)} but the arm's config "
                        f"parking_option_price_beta_u_per_eur has {config.option_price_beta_u_per_eur:g}")
    else:
        echoed.append(f"Java option price coefficient {beta_line.match.group(1)} [from {beta_line.log}]")
    if problems:
        return CheckResult("config_echo", FAIL, "; ".join(problems))
    return CheckResult("config_echo", PASS, f"values equal the arm's config (Python side from {python_source}): "
                       + ", ".join(echoed))


def check_sources(logs: Sequence[LogSource], expected: dict[str, str], inputs_report: InputsReport | None = None) -> CheckResult:
    """Check 3: the 12-character source hash prefixes equal the install list (inputs report first, else the zone release line)."""
    if inputs_report is not None:
        logged = {dataset: sha256[:12] for dataset, sha256 in inputs_report.source_sha256.items()}
        origin = f"prefixes of the full SHA-256 of the inputs report {inputs_report.name}"
    else:
        release = find_last(logs, ZONE_RELEASE)
        if release is None:
            return CheckResult("sources", FAIL, "without --inputs-report the sources need the '[parking] zone release: ...; "
                               "sources ...' line, which only an EXECUTED zones stage logs, and no log has it (the stage was "
                               "cached: pass the inputs report of the arm or the log of the run that executed the zones "
                               f"stage); logs: {_log_names(logs)}")
        logged = {}
        for entry in release.match.group(1).split(", "):
            parts = entry.split()
            if len(parts) == 2:
                logged[parts[0]] = parts[1]
        origin = f"zone release line [from {release.log}] (FALLBACK: no --inputs-report given)"
    problems = []
    for dataset, prefix in expected.items():
        if dataset not in logged:
            problems.append(f"{dataset} is not in the sources {sorted(logged)}")
        elif logged[dataset] != prefix:
            problems.append(f"{dataset} has {logged[dataset]} but the install list has {prefix}")
    if problems:
        return CheckResult("sources", FAIL, "; ".join(problems) + f" ({origin})")
    return CheckResult("sources", PASS, f"{origin} equal the install list: "
                       + ", ".join(f"{dataset} {prefix}" for dataset, prefix in expected.items()))


def _lines_matching(log: LogSource, pattern: re.Pattern) -> bool:
    return any(pattern.search(line) for line in log.lines)


def check_ev_exemption(logs: Sequence[LogSource], enabled: bool, own: str | None = None) -> CheckResult:
    """Check 4: the EV attach lines of ONE log agree with ``parking_ev_exemption_enabled`` (see the module doc).

    ``own`` is the name of the arm's own log. A supplied log other than the supplier that has the line of the OPPOSITE setting
    is named in a NOTE, and fails the check when it is the arm's own log (a log of an earlier arm may legitimately show the
    other setting)."""
    wanted, opposite = (EV_HEADLINE, EV_DISABLED) if enabled else (EV_DISABLED, EV_HEADLINE)
    opposite_text = "'EV exemption is disabled' line" if enabled else "exemption headline"
    supplier = next((log for log in logs if _lines_matching(log, wanted)), None)
    if supplier is None:
        found_other = any(_lines_matching(log, opposite) for log in logs)
        what = ("the '[parking] EV exemption: N of M car owners' headline" if enabled
                else "the '[parking] EV exemption is disabled' line")
        return CheckResult("ev_exemption", FAIL, f"no log has {what}"
                           + (" but a log has the line of the OTHER setting: the overlay did not arrive" if found_other else
                              " (the population stage was cached: pass the log of the arm that executed the stage this arm "
                              "reuses)") + f"; logs: {_log_names(logs)}")
    problems, notes = [], []
    if _lines_matching(supplier, opposite):
        problems.append(f"the config {'enables' if enabled else 'disables'} the EV exemption but {supplier.name} has both the "
                        f"disabled line and the exemption headline")
    for log in logs:
        if log is supplier or not _lines_matching(log, opposite):
            continue
        if log.name == own:
            problems.append(f"the arm's own log {log.name} has the line of the OTHER setting ({opposite_text}): the overlay "
                            "did not arrive in this run")
        else:
            notes.append(f"NOTE: {log.name} (an earlier arm) has the {opposite_text} of the other setting")
    if enabled:
        headline = find_last([supplier], EV_HEADLINE).match
        if int(headline.group(1)) == 0:
            problems.append(f"no exempt car owner: {headline.group(0)}")
        if not _lines_matching(supplier, EV_BASIS):
            problems.append("the log has no '[parking] EV exemption basis ... powertrains of the car vehicles:' line (the "
                            "attach did not run to its end)")
        if _lines_matching(supplier, EV_NO_POWERTRAIN_COLUMN):
            problems.append("the log warns that the vehicles frame has no 'powertrain' column, so nobody can be exempt")
        evidence = (f"{headline.group(0)} (exempt > 0) and no warning that the vehicles frame lacks the powertrain column "
                    f"[from {supplier.name}]")
    else:
        evidence = f"EV exemption disabled in the config and logged as disabled [from {supplier.name}]"
    if problems:
        return CheckResult("ev_exemption", FAIL, "; ".join([*problems, *notes]))
    return CheckResult("ev_exemption", PASS, "; ".join([evidence, *notes]))


def read_report(path):
    """The report as a frame via the strict reader of the comparison script; ``ValueError`` when it is not a v4 report."""
    header = _report_header(path)
    if header != list(REPORT_V4_COLUMNS):
        raise ValueError(f"the report header is not the v4 header (see module_active): {header}")
    frame, _ = compare.read_parking_outcomes(path)
    return frame


def check_no_feasible_option(frame, zone_groups, warning_logs: Sequence[LogSource]) -> CheckResult:
    """Check 5: NO_FEASIBLE_OPTION per zone group; Ia and Ib above ``HARD_STOP_MAXIMUM_PERCENT`` fail, other groups are reported."""
    priced = frame[frame["outcome"] != cost.NO_ZONE]
    groups = priced["zone_id"].map(lambda zone_id: zone_group(zone_id, zone_groups))
    per_group = priced.assign(group=groups).groupby("group").agg(priced=("count", "sum"),
                                                                 infeasible=("no_feasible_option_count", "sum"))
    problems, parts = [], []
    for group in [*HARD_STOP_GROUPS, *sorted(set(per_group.index) - set(HARD_STOP_GROUPS))]:
        n_priced = int(per_group.loc[group, "priced"]) if group in per_group.index else 0
        if n_priced == 0:
            if group in HARD_STOP_GROUPS:
                problems.append(f"{group} has no priced stay in the report, so the hard stop cannot be judged")
            elif group in per_group.index:
                parts.append(f"{group} no priced stay")
            continue
        n_infeasible = int(per_group.loc[group, "infeasible"])
        parts.append(f"{group} {n_infeasible}/{n_priced} ({100 * n_infeasible / n_priced:.2f} %)")
        if group in HARD_STOP_GROUPS and n_infeasible * 100 > HARD_STOP_MAXIMUM_PERCENT * n_priced:
            problems.append(f"{group} has NO_FEASIBLE_OPTION on {n_infeasible} of {n_priced} priced stays "
                            f"({100 * n_infeasible / n_priced:.2f} %), above {HARD_STOP_MAXIMUM_PERCENT} %")
    evidence = "NO_FEASIBLE_OPTION / priced stays per zone group: " + "; ".join(parts)
    warning = find_last(warning_logs, NO_FEASIBLE_WARNING)
    if warning is not None:
        evidence += (f"; last MATSim warning [from {warning.log}]: it {warning.match.group(1)}: {warning.match.group(2)} of "
                     f"{warning.match.group(3)} ({warning.match.group(4)} %)")
    if problems:
        return CheckResult("no_feasible_option", FAIL, "; ".join(problems) + "; " + evidence)
    return CheckResult("no_feasible_option", PASS, f"Ia and Ib at most {HARD_STOP_MAXIMUM_PERCENT} %; others reported only; "
                       + evidence)


def report_free_share(frame, shift: float, logs: Sequence[LogSource] = ()) -> CheckResult:
    """Check 6 (report only): the realised free share of work and education pricing calls next to the configured shift and the
    person-level free-parking draw of the population stage."""
    draw = find_last(logs, FREE_DRAW)
    if draw is None:
        person_level = ("person-level free draw: no 'free-parking draw' line in the logs (the population stage was cached: "
                        "pass the log of the arm that executed it)")
    else:
        match = draw.match
        person_level = (f"person-level free draw [from {draw.log}]: {match.group(4)} of {match.group(2)} persons with a paid-zone "
                        f"work/education activity park free ({match.group(5)} %), drawn with shift {match.group(1)}")
        if float(match.group(1)) != shift:
            person_level += f" (NOTE: this differs from the configured shift {shift:+.3f})"
    work_education = frame[frame["purpose"].isin(WORK_EDUCATION_PURPOSES) & (frame["outcome"] != cost.NO_ZONE)]
    total = int(work_education["count"].sum())
    if total == 0:
        return CheckResult("free_share", INFO, "no work or education pricing call in a zone in this report: the realised free "
                           f"share of calls is not defined; configured workplace free-share shift {shift:+.3f}; {person_level}")
    employer_free = int(work_education.loc[work_education["outcome"] == cost.EMPLOYER_FREE, "count"].sum())
    no_charge = int(work_education.loc[work_education["outcome"].isin(NO_CHARGE_OUTCOMES), "count"].sum())
    return CheckResult("free_share", INFO, f"work and education pricing calls in zones {total}: employer_free {employer_free}/"
                       f"{total} ({100 * employer_free / total:.2f} %), no_charge {no_charge}/{total} "
                       f"({100 * no_charge / total:.2f} %) (an outcome rate of CALLS); configured workplace free-share shift "
                       f"{shift:+.3f} (parking_workplace_free_share_shift); {person_level}")


def run_checks(config: ArmConfig, logs: Sequence[LogSource], java_logs: Sequence[LogSource] | None, report_path,
               expected_sources, zone_groups, inputs_report: InputsReport | None = None) -> list[CheckResult]:
    """All six checks in order. ``logs`` is in the order given: the first is the arm's own log (run check, Java lines), all
    of them supply the cached-stage evidence. A report that is no usable v4 report fails check 5 and leaves check 6 not
    evaluated."""
    own, cached = logs[:1], newest_first(logs)
    java_logs = java_logs if java_logs else own
    results = [check_module_active(own, report_path),
               check_config_echo(cached, java_logs, config, inputs_report),
               check_sources(cached, expected_sources, inputs_report),
               check_ev_exemption(cached, config.ev_exemption_enabled, own=own[0].name)]
    try:
        frame = read_report(report_path)
    except (ValueError, FileNotFoundError) as error:
        results.append(CheckResult("no_feasible_option", FAIL, f"cannot be evaluated, the report is unusable: {error}"))
        results.append(CheckResult("free_share", INFO, "not evaluated, the report is unusable (see no_feasible_option)"))
        return results
    results.append(check_no_feasible_option(frame, zone_groups, java_logs))
    results.append(report_free_share(frame, config.workplace_free_share_shift, cached))
    return results


# ------------------------------------------------------------------------------------------- the checks, off and legacy


def check_arm_kind_config(kind: str, zones_enabled: bool, urban_parking: bool) -> CheckResult:
    """The config flags of an arm without the zone model (R-C-5)."""
    problems = []
    if zones_enabled:
        problems.append("parking_zones_enabled is true")
    if kind == "off" and urban_parking:
        problems.append("enable_urban_parking is true")
    if kind == "legacy" and not urban_parking:
        problems.append("enable_urban_parking is false")
    if problems:
        return CheckResult("arm_kind_config", FAIL, f"arm kind {kind}: " + " and ".join(problems))
    evidence = f"arm kind {kind}: parking_zones_enabled false, enable_urban_parking {str(urban_parking).lower()}"
    if kind == "legacy":
        evidence += ("; the legacy ring rests on the config flag only: it is applied in the plan writer and leaves no log line "
                     "or output file that this check could verify")
    return CheckResult("arm_kind_config", PASS, evidence)


def _forbidden_hits(logs: Sequence[LogSource], forbidden) -> list[str]:
    hits = []
    for label, pattern in forbidden:
        found = find_last(logs, pattern)
        if found is not None:
            hits.append(f"'{label}' [in {found.log}]")
    return hits


def check_no_parking_logs(logs: Sequence[LogSource], java_logs: Sequence[LogSource] | None) -> CheckResult:
    """No log line of the zone model in the logs of an arm that has none."""
    hits = _forbidden_hits(logs, FORBIDDEN_PIPELINE_LINES) + _forbidden_hits(java_logs if java_logs else logs,
                                                                             FORBIDDEN_JAVA_LINES)
    if hits:
        return CheckResult("no_parking_log_lines", FAIL, "a log line of the zone model is present: " + ", ".join(hits))
    return CheckResult("no_parking_log_lines", PASS, f"none of the zone model's log lines in {_log_names(logs)}"
                       + (f" and {_log_names(java_logs)}" if java_logs else ""))


def _read_output_config_module_enabled(simulation_output: Path) -> bool:
    plain = simulation_output / OUTPUT_CONFIG_FILE_NAME
    compressed = simulation_output / (OUTPUT_CONFIG_FILE_NAME + ".gz")
    if plain.is_file():
        return runtime_checks.parking_module_enabled(plain)
    if compressed.is_file():
        with gzip.open(compressed, "rt", encoding="utf-8") as stream:
            root = ElementTree.parse(stream).getroot()
        for module in root.findall("module"):
            if module.get("name") == runtime_checks.PARKING_MODULE:
                return any(param.get("name") == "enabled" and param.get("value") == "true" for param in module)
        return False
    raise InputError(f"{simulation_output} has no {OUTPUT_CONFIG_FILE_NAME}(.gz), the config MATSim writes at the end of a run "
                     "(is this the simulation_output directory of a finished run?)")


def check_no_parking_outputs(simulation_output) -> CheckResult:
    """No outcome report under ``ITERS`` and no enabled ``braunschweigParking`` module in the run's output config."""
    simulation_output = Path(simulation_output)
    if not simulation_output.is_dir():
        raise InputError(f"simulation output {simulation_output} does not exist or is no directory")
    reports = sorted((simulation_output / "ITERS").glob("it.*/*.parking_outcomes.csv"))
    try:
        enabled = _read_output_config_module_enabled(simulation_output)
    except (ElementTree.ParseError, ValueError, OSError) as error:
        return CheckResult("no_parking_outputs", FAIL, f"the output config of {simulation_output} is unusable: {error}")
    problems = []
    if reports:
        problems.append(f"{len(reports)} parking outcome report(s) exist, e.g. {reports[0]}")
    if enabled:
        problems.append(f"the output config enables the module {runtime_checks.PARKING_MODULE}")
    if problems:
        return CheckResult("no_parking_outputs", FAIL, "; ".join(problems))
    return CheckResult("no_parking_outputs", PASS, f"no *.parking_outcomes.csv under ITERS and the module "
                       f"{runtime_checks.PARKING_MODULE} is not enabled in {OUTPUT_CONFIG_FILE_NAME}")


def run_checks_without_parking(kind: str, flags: tuple[bool, bool], logs: Sequence[LogSource],
                               java_logs: Sequence[LogSource] | None, simulation_output=None) -> list[CheckResult]:
    """The checks of the kinds ``off`` and ``legacy`` (R-C-5)."""
    results = [check_arm_kind_config(kind, *flags), check_no_parking_logs(logs, java_logs)]
    if simulation_output is not None:
        results.append(check_no_parking_outputs(simulation_output))
    return results


# ------------------------------------------------------------------------------------------------------------ the command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--arm-kind", choices=ARM_KINDS, default="v3",
                        help="v3 (zone model, default), or off / legacy (no zone model)")
    parser.add_argument("--arm-config", required=True, help="the arm's resolved config (.merged_config.yml)")
    parser.add_argument("--log", action="append", required=True,
                        help="a pipeline log; repeatable and ORDER MATTERS: the FIRST --log is the arm's own log (run check "
                             "line, Java lines), every further --log is the log of an earlier arm that may supply only the "
                             "cached-stage evidence (EV lines, free draw, zone release / prepared inputs fallback)")
    parser.add_argument("--java-log", help="the MATSim log of the arm; without it the Java lines are read from the first --log")
    parser.add_argument("--inputs-report", help="<prefix>parking_inputs_report.json of the prepare stage (full SHA-256 sources, "
                                                "cost factor, beta, decays): the persistent evidence of checks 2 and 3")
    parser.add_argument("--report", help="kind v3: the v4 outcome report of one iteration (N.parking_outcomes.csv)")
    parser.add_argument("--expected-sources", help="kind v3: JSON dataset -> 12-character SHA-256 prefix (the install list)")
    parser.add_argument("--zone-groups", help="kind v3: YAML/JSON zone-id pattern -> group (replaces the default grouping)")
    parser.add_argument("--simulation-output", help="kinds off/legacy: the simulation_output directory of the run (optional)")
    return parser


def _ignored(arguments, names: Sequence[str], kind: str) -> None:
    given = [f"--{name.replace('_', '-')}" for name in names if getattr(arguments, name) is not None]
    if given:
        print(f"arm_sanity: note: {', '.join(given)} not used for arm kind {kind} (ignored)", file=sys.stderr)


def _evaluate(arguments) -> list[CheckResult]:
    logs = load_logs(arguments.log)
    java_logs = load_logs([arguments.java_log]) if arguments.java_log else None
    if arguments.arm_kind != "v3":
        flags = load_arm_flags(arguments.arm_config)
        _ignored(arguments, ("report", "expected_sources", "inputs_report", "zone_groups"), arguments.arm_kind)
        return run_checks_without_parking(arguments.arm_kind, flags, logs, java_logs, arguments.simulation_output)
    for name in ("report", "expected_sources"):
        if getattr(arguments, name) is None:
            raise InputError(f"--{name.replace('_', '-')} is required for the arm kind v3")
    _ignored(arguments, ("simulation_output",), "v3")
    config = load_arm_config(arguments.arm_config)
    report = _require_file(arguments.report, "outcome report")
    expected_sources = load_expected_sources(arguments.expected_sources)
    zone_groups = load_zone_groups(arguments.zone_groups)
    inputs_report = load_inputs_report(arguments.inputs_report) if arguments.inputs_report else None
    return run_checks(config, logs, java_logs, report, expected_sources, zone_groups, inputs_report)


def main(argv=None) -> int:
    """Run the checks; 0 = sane, 1 = a check failed, 2 = an input is missing or unusable (nothing is printed on stdout)."""
    arguments = build_parser().parse_args(argv)
    try:
        results = _evaluate(arguments)
    except InputError as error:
        print(f"arm_sanity: input error: {error}", file=sys.stderr)
        return 2
    except (yaml.YAMLError, UnicodeDecodeError, pd.errors.EmptyDataError) as error:
        print(f"arm_sanity: input error: an input is empty, undecodable or malformed: {error}", file=sys.stderr)
        return 2
    for result in results:
        print(f"[{result.status}] {result.name}: {result.evidence}")
    failed = [result for result in results if result.status == FAIL]
    if failed:
        print(f"FIRST FAILURE: {failed[0].name}: {failed[0].evidence}")
        return 1
    print(f"ARM SANE: {len(results)} checks, none failed (a plausibility check of the run mechanics, not a validation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
