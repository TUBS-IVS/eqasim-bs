"""Per-arm sanity check of the parking cost zones v3 comparison runs (issue #436, Part C, ruling R-C-3).

An analysis aid, never a pipeline stage and never a model input. Before an arm of the comparison is compared with the others it
has to prove that the intended model acted in it: a run whose overlay did not arrive, whose jar lacks the parking package or
whose zone release differs from the install list would otherwise be compared as if it were the arm it is named after. The
script reads the arm's RESOLVED config (``<working_directory>/.merged_config.yml``: the base with the overlay merged), the
pipeline log, optionally the MATSim log, and the outcome report of ONE iteration, runs the checks below, prints one line per
check and exits 0 (sane) or 1 (the first failing check is named last, with its evidence). Exit 2 is an input error (a file that
is missing or unusable, a config key that is absent): nothing is defaulted silently. A sane arm is no validation of the model:
this is a plausibility check of the run's mechanics, and convergence or a green check is not agreement with observed data.

Checks (in this order; each keys on the exact production log line named here)
    1. ``module_active``: the pipeline log has the INFO line of ``braunschweig.parking.runtime_checks.
       require_parking_outcomes`` ("[parking] run check: jar contains the parking package; outcomes written for iteration N"),
       and the header of the report is exactly ``REPORT_V4_COLUMNS`` of ``scripts/parking/compare_parking_targets.py``
       (imported, not copied: one source of the Java header).
    2. ``config_echo``: the values the run logged equal the arm's resolved config. Python side, the line
       "[parking] prepared inputs: ..." that ``_write_parking_inputs`` of ``braunschweig/matsim/simulation/prepare.py`` prints
       (cost factor, option price coefficient, decay length, commuter decay length, and the snapshot date inside the file name
       ``braunschweig.parking.tariff_export.tariff_model_file_name``); Java side, the startup lines of
       ``ParkingOutcomeReportListener.notifyStartup`` ("[parking] parking cost factor X (braunschweigParking.costFactor ...") and
       ``ParkingOptionPriceBetaCheck.notifyStartup`` ("[parking] option price coefficient X per EUR equals ..."). The Java lines
       are searched in ``--java-log`` or, without it, in the pipeline log (the MATSim output may be part of it). An absent
       ``parking_cost_utility_factor`` in the config means 1.0, the pipeline default (``PARKING_DEFAULTS`` of prepare.py); every
       other key must be present.
    3. ``sources``: the 12-character hash prefixes of the zone release line of ``braunschweig.parking.zones_stage`` ("[parking]
       zone release: ...; sources <dataset> <prefix>, ...") equal ``--expected-sources`` (JSON: dataset -> 12 hex characters, the
       install list of R-C-2). Datasets the file does not name are not checked.
    4. ``ev_exemption``: ``braunschweig.parking.attach.attach_ev_exemption`` logged, for ``parking_ev_exemption_enabled`` true,
       the headline ("EV exemption: N of M car owners ...") with N > 0 and the basis line ("EV exemption basis ... powertrains of
       the car vehicles: ...", the evidence that the vehicles frame has a powertrain column) and NOT the warning "has no
       'powertrain' column"; for false the line "EV exemption is disabled" and no headline.
    5. ``no_feasible_option``: from the v4 report, per zone group, ``no_feasible_option_count`` over the priced stays (every
       row but NO_ZONE, as ``ParkingOutcomeReportListener.pricedStays`` counts them). The two city-centre zones ``bs_zone_ia``
       and ``bs_zone_ib`` are judged one by one and FAIL above 1 % (exactly 1 % passes); bh_*, se_am_markt, wob_area_* and every
       other zone are reported and never fail. The count is of pricing calls (car alternatives evaluated), not of persons or
       trips. When the log carries the Java warning of ``noFeasibleOptionWarning`` its last line is quoted next to the rates.
    6. ``free_share`` (report only): the realised share of EMPLOYER_FREE and of all no-charge outcomes among the work and
       education pricing calls in zones, next to the configured ``parking_workplace_free_share_shift``. The realised share is an
       outcome rate of pricing calls, not the share of persons that the free-parking draw gave a free place.

``--zone-groups`` (YAML or JSON) maps zone-id patterns (``fnmatch``, first match in file order) to group names and replaces the
default grouping; it must map ``bs_zone_ia`` and ``bs_zone_ib`` to groups of exactly those names (they are the hard-stop
groups), and an id that matches no pattern belongs to the group ``other``.

Usage (repository root)::

    python scripts/parking/arm_sanity.py --arm-config <wd>/.merged_config.yml --log <pipeline log> --java-log <matsim log> \
        --report <simulation_output>/ITERS/it.60/60.parking_outcomes.csv --expected-sources expected_sources.json

Reads only; never writes, never pushes anything anywhere. The arms ``off`` and ``legacy`` have no parking module and no report:
this check does not apply to them.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import fnmatch
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.parking import cost  # noqa: E402

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
CHECK_NAMES = ("module_active", "config_echo", "sources", "ev_exemption", "no_feasible_option", "free_share")

#: Hard stop (review focus 1 of the plan): NO_FEASIBLE_OPTION above this share of the priced stays in Ia or Ib in percent.
HARD_STOP_MAXIMUM_PERCENT = 1
HARD_STOP_GROUPS = ("bs_zone_ia", "bs_zone_ib")
OTHER_GROUP = "other"
#: Default zone groups: (fnmatch pattern, group), first match wins; unmatched zones are ``OTHER_GROUP`` (e.g. wob_tarifzone_*).
DEFAULT_ZONE_GROUPS = (("bs_zone_ia", "bs_zone_ia"), ("bs_zone_ib", "bs_zone_ib"), ("bh_*", "bh"),
                       ("se_am_markt", "se_am_markt"), ("wob_area_*", "wob_area"))
WORK_EDUCATION_PURPOSES = tuple(sorted(cost.COMMUTER_PURPOSES))
#: Outcomes that charge nothing by their own rule and make up the free share of work and education stays (the four of the
#: ``free_share_work_education_*`` metrics of compare_parking_targets.py; EV_EXEMPT is no workplace free share).
NO_CHARGE_OUTCOMES = (cost.EMPLOYER_FREE, cost.RESIDENT_FREE, cost.OUTSIDE_FEE_HOURS, cost.FREE_WITHIN_LIMIT)
SOURCE_PREFIX_PATTERN = re.compile(r"[0-9a-f]{12}")
#: The pipeline default of ``parking_cost_utility_factor`` (PARKING_DEFAULTS of braunschweig/matsim/simulation/prepare.py).
COST_FACTOR_DEFAULT = 1.0

# The production log formats this script keys on; tests/test_arm_sanity.py pins every fragment to the source that writes it.
RUN_CHECK = re.compile(r"\[parking\] run check: jar contains the parking package; outcomes written for iteration (\d+)")
PREPARED_INPUTS = "[parking] prepared inputs:"
PREPARED_COST_FACTOR = re.compile(r"cost factor (\S+?),")
PREPARED_BETA = re.compile(r"option price coefficient (\S+) per EUR")
PREPARED_DECAY = re.compile(r"(?<!commuter )decay length (\S+) m \(")
PREPARED_COMMUTE_DECAY = re.compile(r"commuter decay length (\S+) m \(")
PREPARED_SNAPSHOT = re.compile(r"parking_tariffs_(\d{4}-\d{2}-\d{2})\.json")
JAVA_COST_FACTOR = re.compile(r"\[parking\] parking cost factor (\S+) \(")
JAVA_BETA = re.compile(r"\[parking\] option price coefficient (\S+) per EUR equals the magnitude of the mode choice cost")
JAVA_BETA_SKIPPED = "[parking] option price coefficient check skipped"
ZONE_RELEASE = re.compile(r"\[parking\] zone release: .*; sources (.+)$")
EV_DISABLED = "[parking] EV exemption is disabled"
EV_HEADLINE = re.compile(r"\[parking\] EV exemption: (\d+) of (\d+) car owners")
EV_BASIS = "[parking] EV exemption basis"
EV_BASIS_POWERTRAINS = "powertrains of the car vehicles:"
EV_NO_POWERTRAIN_COLUMN = "has no 'powertrain' column"
NO_FEASIBLE_WARNING = re.compile(r"\[parking\] it (\d+): (\d+) of (\d+) priced stays \(([\d.]+) %\) have no feasible "
                                 r"parking option")


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


class ArmConfig(NamedTuple):
    """The values of the arm's resolved config that the checks compare with the logs."""

    snapshot_date: str
    garage_decay_m: float
    garage_decay_commute_m: float
    option_price_beta_u_per_eur: float
    cost_utility_factor: float
    ev_exemption_enabled: bool
    workplace_free_share_shift: float


# ----------------------------------------------------------------------------------------------------------- the inputs


def _require_file(path, what: str) -> Path:
    path = Path(path)
    if not path.is_file():
        raise InputError(f"{what} {path} does not exist or is no file")
    return path


def read_log_lines(path) -> list[str]:
    """The lines of a log. Undecodable bytes are replaced: a log is evidence text, not a data input."""
    return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()


def _number(config: dict, key: str, path) -> float:
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{path}: {key} must be a number, found {value!r}")
    return float(value)


def load_arm_config(path) -> ArmConfig:
    """Read the resolved config. The values live in its ``config:`` section (``.merged_config.yml`` of ``run_synpp.py``); a flat
    mapping is accepted as well. Raises ``InputError`` for a missing key or a value of the wrong type."""
    path = _require_file(path, "arm config")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise InputError(f"{path}: the resolved config must be a YAML mapping, found {type(document).__name__}")
    config = document["config"] if isinstance(document.get("config"), dict) else document

    def required(key: str):
        if key not in config or config[key] is None:
            raise InputError(f"{path}: the resolved config has no value for {key}; the arm cannot be checked against an "
                             "absent key (pass the file written by run_synpp.py, the base with the overlay merged)")
        return config[key]

    snapshot = required("parking_tariff_snapshot_date")
    snapshot = snapshot.isoformat() if isinstance(snapshot, (datetime.date, datetime.datetime)) else str(snapshot)
    enabled = required("parking_ev_exemption_enabled")
    if not isinstance(enabled, bool):
        raise InputError(f"{path}: parking_ev_exemption_enabled must be true or false, found {enabled!r}")
    for key in ("parking_garage_decay_m", "parking_garage_decay_commute_m", "parking_option_price_beta_u_per_eur",
                "parking_workplace_free_share_shift"):
        required(key)
    cost_factor = _number(config, "parking_cost_utility_factor", path) if config.get(
        "parking_cost_utility_factor") is not None else COST_FACTOR_DEFAULT
    return ArmConfig(snapshot, _number(config, "parking_garage_decay_m", path),
                     _number(config, "parking_garage_decay_commute_m", path),
                     _number(config, "parking_option_price_beta_u_per_eur", path), cost_factor, enabled,
                     _number(config, "parking_workplace_free_share_shift", path))


def load_expected_sources(path) -> dict[str, str]:
    """The install list: dataset -> 12 lowercase hex characters (the prefix of the SHA-256 the zones stage logs)."""
    path = _require_file(path, "expected-sources file")
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise InputError(f"expected sources {path} is no JSON: {error}") from error
    if not isinstance(expected, dict) or not expected:
        raise InputError(f"expected sources {path} must be a non-empty JSON object dataset -> 12-character hash prefix")
    for dataset, prefix in expected.items():
        if not isinstance(prefix, str) or not SOURCE_PREFIX_PATTERN.fullmatch(prefix):
            raise InputError(f"expected sources {path}: the prefix of {dataset!r} must be 12 lowercase hex characters, found "
                             f"{prefix!r}")
    return expected


def load_zone_groups(path=None) -> tuple[tuple[str, str], ...]:
    """The ordered (pattern, group) pairs: the default, or those of the YAML/JSON mapping in ``path``."""
    if path is None:
        return DEFAULT_ZONE_GROUPS
    path = _require_file(path, "zone-groups file")
    mapping = yaml.safe_load(path.read_text(encoding="utf-8"))
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


# ------------------------------------------------------------------------------------------------------------- the checks


def _last_match(lines, pattern):
    """The last ``re.Match`` of ``pattern`` over ``lines`` (a log may hold earlier runs), or None."""
    found = None
    for line in lines:
        match = pattern.search(line)
        if match:
            found = match
    return found


def _report_header(path) -> list[str]:
    with Path(path).open(newline="", encoding="utf-8") as stream:
        return next(csv.reader(stream), [])


def check_module_active(pipeline_lines, report_path) -> CheckResult:
    """Check 1: the run check line is in the log and the report header is exactly the v4 header."""
    problems = []
    run_check = _last_match(pipeline_lines, RUN_CHECK)
    if run_check is None:
        problems.append("the pipeline log has no '[parking] run check: jar contains the parking package; outcomes written for "
                        "iteration N' line (the parking module did not price this run, or the run stopped before its end)")
    header = _report_header(report_path)
    if header != list(REPORT_V4_COLUMNS):
        problems.append(f"the report header {header} is not the v4 header {list(REPORT_V4_COLUMNS)} (exact columns and order)")
    if problems:
        return CheckResult("module_active", FAIL, "; ".join(problems))
    return CheckResult("module_active", PASS, f"[parking] run check line for iteration {run_check.group(1)}; report header is "
                       f"the v4 header ({len(REPORT_V4_COLUMNS)} columns)")


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


def check_config_echo(pipeline_lines, java_lines, config: ArmConfig) -> CheckResult:
    """Check 2: the logged values equal the arm's resolved config (see the module doc for the lines and their sources)."""
    problems, echoed = [], []
    prepared = [line for line in pipeline_lines if PREPARED_INPUTS in line]
    if not prepared:
        problems.append(f"the pipeline log has no '{PREPARED_INPUTS}' line (the parking inputs were not prepared in this run)")
    else:
        line = prepared[-1]
        # The preparation line formats with %g (6 significant digits) except the decay length (%s of the float): compare each
        # with the config value in the same format, so a rounding of the log is no mismatch and a real difference is.
        for description, pattern, expected, exact in (
                ("parking_cost_utility_factor (prepared inputs)", PREPARED_COST_FACTOR, config.cost_utility_factor, False),
                ("parking_option_price_beta_u_per_eur (prepared inputs)", PREPARED_BETA,
                 config.option_price_beta_u_per_eur, False),
                ("parking_garage_decay_m (prepared inputs)", PREPARED_DECAY, config.garage_decay_m, True),
                ("parking_garage_decay_commute_m (prepared inputs)", PREPARED_COMMUTE_DECAY,
                 config.garage_decay_commute_m, False)):
            logged = _logged_float(line, pattern, description, problems)
            if logged is None:
                continue
            reference = expected if exact else float("%g" % expected)
            if logged != reference:
                problems.append(f"{description} logged {logged:g} but the arm's config has {expected:g}")
            else:
                echoed.append(f"{description.split(' (')[0]} {logged:g}")
        snapshot = PREPARED_SNAPSHOT.search(line)
        if snapshot is None:
            problems.append("tariff snapshot date: no parking_tariffs_<date>.json file name in the prepared inputs line")
        elif snapshot.group(1) != config.snapshot_date:
            problems.append(f"tariff snapshot date logged {snapshot.group(1)} but the arm's config "
                            f"parking_tariff_snapshot_date has {config.snapshot_date}")
        else:
            echoed.append(f"snapshot {snapshot.group(1)}")
    cost_line = _last_match(java_lines, JAVA_COST_FACTOR)
    if cost_line is None:
        problems.append("the Java startup line '[parking] parking cost factor X (braunschweigParking.costFactor ...' (costFactor) "
                        "is absent: the jar did not log the factor it applies")
    elif float(cost_line.group(1)) != config.cost_utility_factor:
        problems.append(f"Java costFactor logged {cost_line.group(1)} but the arm's config parking_cost_utility_factor has "
                        f"{config.cost_utility_factor:g}")
    else:
        echoed.append(f"Java costFactor {cost_line.group(1)}")
    beta_line = _last_match(java_lines, JAVA_BETA)
    if beta_line is None:
        skipped = any(JAVA_BETA_SKIPPED in line for line in java_lines)
        problems.append("the Java startup line '[parking] option price coefficient X per EUR equals ...' is absent"
                        + (" (the check was skipped: the tariff model has a schema before 5)" if skipped else ""))
    elif float(beta_line.group(1)) != config.option_price_beta_u_per_eur:
        problems.append(f"Java option price coefficient logged {beta_line.group(1)} but the arm's config "
                        f"parking_option_price_beta_u_per_eur has {config.option_price_beta_u_per_eur:g}")
    else:
        echoed.append(f"Java option price coefficient {beta_line.group(1)}")
    if problems:
        return CheckResult("config_echo", FAIL, "; ".join(problems))
    return CheckResult("config_echo", PASS, "logged values equal the arm's config: " + ", ".join(echoed))


def check_sources(pipeline_lines, expected: dict[str, str]) -> CheckResult:
    """Check 3: the 12-character source hash prefixes of the zone release line equal the install list."""
    release = _last_match(pipeline_lines, ZONE_RELEASE)
    if release is None:
        return CheckResult("sources", FAIL, "the pipeline log has no '[parking] zone release: ...; sources ...' line of the "
                           "zones stage")
    logged = {}
    for entry in release.group(1).split(", "):
        parts = entry.split()
        if len(parts) == 2:
            logged[parts[0]] = parts[1]
    problems = []
    for dataset, prefix in expected.items():
        if dataset not in logged:
            problems.append(f"{dataset} is not in the logged sources {sorted(logged)}")
        elif logged[dataset] != prefix:
            problems.append(f"{dataset} logged {logged[dataset]} but the install list has {prefix}")
    if problems:
        return CheckResult("sources", FAIL, "; ".join(problems))
    return CheckResult("sources", PASS, "logged prefixes equal the install list: "
                       + ", ".join(f"{dataset} {prefix}" for dataset, prefix in expected.items()))


def check_ev_exemption(pipeline_lines, enabled: bool) -> CheckResult:
    """Check 4: the EV attach lines agree with ``parking_ev_exemption_enabled`` (see the module doc)."""
    disabled = any(EV_DISABLED in line for line in pipeline_lines)
    headline = _last_match(pipeline_lines, EV_HEADLINE)
    if not enabled:
        if headline is not None:
            return CheckResult("ev_exemption", FAIL, "the config disables the EV exemption but the log has the exemption "
                               f"headline '{headline.group(0)}': the overlay did not arrive")
        if not disabled:
            return CheckResult("ev_exemption", FAIL, "the config disables the EV exemption but the log has no "
                               f"'{EV_DISABLED}' line")
        return CheckResult("ev_exemption", PASS, "EV exemption disabled in the config and logged as disabled")
    problems = []
    if disabled:
        problems.append(f"the config enables the EV exemption but the log says '{EV_DISABLED}'")
    exempt = None
    if headline is None:
        problems.append("the log has no '[parking] EV exemption: N of M car owners' headline line")
    else:
        exempt = int(headline.group(1))
        if exempt == 0:
            problems.append(f"no exempt car owner: {headline.group(0)}")
    basis = [line for line in pipeline_lines if EV_BASIS in line and EV_BASIS_POWERTRAINS in line]
    if not basis:
        problems.append(f"the log has no '{EV_BASIS}' line naming '{EV_BASIS_POWERTRAINS}' (the powertrain column)")
    if any(EV_NO_POWERTRAIN_COLUMN in line for line in pipeline_lines):
        problems.append("the log warns that the vehicles frame has no 'powertrain' column, so nobody can be exempt")
    if problems:
        return CheckResult("ev_exemption", FAIL, "; ".join(problems))
    return CheckResult("ev_exemption", PASS, f"{headline.group(0)}; the basis line names the powertrain column "
                       f"('{EV_BASIS_POWERTRAINS}')")


def read_report(path):
    """The report as a frame via the strict reader of the comparison script; ``ValueError`` when it is not a v4 report."""
    header = _report_header(path)
    if header != list(REPORT_V4_COLUMNS):
        raise ValueError(f"the report header is not the v4 header (see module_active): {header}")
    frame, _ = compare.read_parking_outcomes(path)
    return frame


def check_no_feasible_option(frame, zone_groups, warning_lines) -> CheckResult:
    """Check 5: NO_FEASIBLE_OPTION per zone group; Ia and Ib above ``HARD_STOP_MAXIMUM_PERCENT`` fail, other groups are reported."""
    priced = frame[frame["outcome"] != cost.NO_ZONE]
    groups = priced["zone_id"].map(lambda zone_id: zone_group(zone_id, zone_groups))
    per_group = priced.assign(group=groups).groupby("group").agg(priced=("count", "sum"),
                                                                 infeasible=("no_feasible_option_count", "sum"))
    problems, parts = [], []
    for group in [*HARD_STOP_GROUPS, *sorted(set(per_group.index) - set(HARD_STOP_GROUPS))]:
        if group not in per_group.index:
            if group in HARD_STOP_GROUPS:
                problems.append(f"{group} has no priced stay in the report, so the hard stop cannot be judged")
            continue
        n_priced, n_infeasible = int(per_group.loc[group, "priced"]), int(per_group.loc[group, "infeasible"])
        parts.append(f"{group} {n_infeasible}/{n_priced} ({100 * n_infeasible / n_priced:.2f} %)")
        if group in HARD_STOP_GROUPS and n_infeasible * 100 > HARD_STOP_MAXIMUM_PERCENT * n_priced:
            problems.append(f"{group} has NO_FEASIBLE_OPTION on {n_infeasible} of {n_priced} priced stays "
                            f"({100 * n_infeasible / n_priced:.2f} %), above {HARD_STOP_MAXIMUM_PERCENT} %")
    evidence = "NO_FEASIBLE_OPTION / priced stays per zone group: " + "; ".join(parts)
    warning = _last_match(warning_lines, NO_FEASIBLE_WARNING)
    if warning is not None:
        evidence += f"; last MATSim warning: it {warning.group(1)}: {warning.group(2)} of {warning.group(3)} ({warning.group(4)} %)"
    if problems:
        return CheckResult("no_feasible_option", FAIL, "; ".join(problems) + "; " + evidence)
    return CheckResult("no_feasible_option", PASS, f"Ia and Ib at most {HARD_STOP_MAXIMUM_PERCENT} %; others reported only; "
                       + evidence)


def report_free_share(frame, shift: float) -> CheckResult:
    """Check 6 (report only): the realised free share of work and education pricing calls next to the configured shift."""
    work_education = frame[frame["purpose"].isin(WORK_EDUCATION_PURPOSES) & (frame["outcome"] != cost.NO_ZONE)]
    total = int(work_education["count"].sum())
    if total == 0:
        return CheckResult("free_share", INFO, "no work or education pricing call in a zone in this report: the realised free "
                           f"share is not defined; configured workplace free-share shift {shift:+.3f}")
    employer_free = int(work_education.loc[work_education["outcome"] == cost.EMPLOYER_FREE, "count"].sum())
    no_charge = int(work_education.loc[work_education["outcome"].isin(NO_CHARGE_OUTCOMES), "count"].sum())
    return CheckResult("free_share", INFO, f"work and education pricing calls in zones {total}: employer_free {employer_free}/"
                       f"{total} ({100 * employer_free / total:.2f} %), no_charge {no_charge}/{total} "
                       f"({100 * no_charge / total:.2f} %); configured workplace free-share shift {shift:+.3f} "
                       "(parking_workplace_free_share_shift); an outcome rate of pricing calls, not the share of persons")


def run_checks(config: ArmConfig, pipeline_lines, java_lines, report_path, expected_sources, zone_groups) -> list[CheckResult]:
    """All six checks in order. A report that is no usable v4 report fails check 5 and leaves check 6 not evaluated."""
    explicit_java = java_lines is not None
    java_lines = java_lines if explicit_java else pipeline_lines
    results = [check_module_active(pipeline_lines, report_path),
               check_config_echo(pipeline_lines, java_lines, config),
               check_sources(pipeline_lines, expected_sources),
               check_ev_exemption(pipeline_lines, config.ev_exemption_enabled)]
    try:
        frame = read_report(report_path)
    except (ValueError, FileNotFoundError) as error:
        results.append(CheckResult("no_feasible_option", FAIL, f"cannot be evaluated, the report is unusable: {error}"))
        results.append(CheckResult("free_share", INFO, "not evaluated, the report is unusable (see no_feasible_option)"))
        return results
    results.append(check_no_feasible_option(frame, zone_groups, java_lines))
    results.append(report_free_share(frame, config.workplace_free_share_shift))
    return results


# ------------------------------------------------------------------------------------------------------------ the command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--arm-config", required=True, help="the arm's resolved config (.merged_config.yml)")
    parser.add_argument("--log", required=True, help="the pipeline log of the arm")
    parser.add_argument("--java-log", help="the MATSim log; without it the Java lines are searched in --log")
    parser.add_argument("--report", required=True, help="the v4 outcome report of one iteration (N.parking_outcomes.csv)")
    parser.add_argument("--expected-sources", required=True,
                        help="JSON: dataset -> 12-character SHA-256 prefix (the install list)")
    parser.add_argument("--zone-groups", help="YAML/JSON: zone-id pattern -> group (replaces the default grouping)")
    return parser


def main(argv=None) -> int:
    """Run the checks; 0 = sane, 1 = a check failed, 2 = an input is missing or unusable (nothing is printed on stdout)."""
    arguments = build_parser().parse_args(argv)
    try:
        config = load_arm_config(arguments.arm_config)
        pipeline_lines = read_log_lines(_require_file(arguments.log, "pipeline log"))
        java_lines = read_log_lines(_require_file(arguments.java_log, "MATSim log")) if arguments.java_log else None
        report = _require_file(arguments.report, "outcome report")
        expected_sources = load_expected_sources(arguments.expected_sources)
        zone_groups = load_zone_groups(arguments.zone_groups)
    except InputError as error:
        print(f"arm_sanity: input error: {error}", file=sys.stderr)
        return 2
    results = run_checks(config, pipeline_lines, java_lines, report, expected_sources, zone_groups)
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
