"""Pure inputs of the student flows (issue #140, Phase 4): the committed tables, no synpp, no stage.

Later tasks (the university facilities, the day-absence cells, the residents' assignment, the
in-commuters, the acceptance table) all read the same few tables; this module loads and checks them
once:

* the origin-destination table T (``schools/student_od_table.csv``, heads, no day factor): the seed
  of the residents' assignment (:func:`region_seed`), the share of each destination
  (:func:`destination_shares`), the outside rows (:func:`outside_seed`) and the commuter weights of
  its header (:func:`od_weights`);
* the level table (:func:`load_level`): the students of each home Kreis in two age ranges;
* the SrV shares of the day factor (:func:`education_leg_share`, :func:`away_share_by_area`) and the
  factor itself (:func:`day_factor_by_kreis`, :func:`effective_day_factor`);
* the home areas (:func:`area_by_kreis`) and the home Kreis of a household
  (:func:`home_kreis_by_household`).

Import boundary (Phase 4 global constraints): the module is a TRUE LEAF. It imports only
``braunschweig.data.csv_header`` and loads no ``braunschweig.popsim``, ``braunschweig.calibration`` or
``braunschweig.analysis`` module (a stage that imports it must not inherit their closure, which names
the VG250 archive). The name and default of the home-area key, the header key of the education-leg
share, the student status code, the region and Wolfsburg codes, the columns of T and the age groups
and sexes of the level are therefore repeated here ONCE; ``tests/test_student_flows_phase3_pins.py``
pins every repeated value against the module that owns it.

Every table is read with ``encoding="utf-8"``, ``comment="#"`` and Kreis codes as ``str``. Nothing
here has a fallback: an absent header line, a missing cell or an invalid area map raises.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.data.csv_header import read_header_value

logger = logging.getLogger(__name__)

_LOG_TAG = "[student flows inputs]"

#: Switch of the student flows (Phase 4); the default (ON) lives with the stages that read it.
KEY_FLOWS_ENABLED = "braunschweig.students.flows_enabled"
#: The home-area key of Phase 3 (``config_keys.KEY_STUDENT_HOME_AREA_KREISE``); repeated here
#: because of the import boundary and pinned by ``tests/test_student_flows_phase3_pins.py``.
KEY_HOME_AREA_KREISE = "braunschweig.population.popsim.student_home_area_kreise"
#: Header key of the education-leg share of the day factor in ``srv2023_student_parameters.csv``
#: (``student_targets.EDUCATION_SHARE_HEADER_KEY``; pinned).
EDUCATION_LEG_SHARE_HEADER_KEY = "education_leg_share_control_definition"

#: The age groups and sexes of the level table (``student_level.AGE_LABELS`` / ``SEXES``; pinned) and
#: the groups that make up the age range 18 to 29.
LEVEL_AGE_GROUPS = ("18_19", "20_24", "25_29", "30plus")
LEVEL_SEXES = ("female", "male")
YOUNG_AGE_GROUPS = ("18_19", "20_24", "25_29")
AGE_RANGE_YOUNG = "18_29"
AGE_RANGE_OLD = "30plus"

# Literals repeated from their owners so that this module stays a TRUE LEAF (importing the owners
# would load braunschweig.calibration and, through it, the popsim and analysis packages, whose
# closure names the VG250 archive). Each is pinned against its owner by
# tests/test_student_flows_phase3_pins.py.
#: Owner ``braunschweig.popsim.attributes.STUDENT_TAET``: the P_TAET code of a student.
STUDENT_P_TAET = 10
#: Owner ``braunschweig.calibration.srv_student_reference.AREA_WOLFSBURG``.
AREA_WOLFSBURG = "wolfsburg"
#: Owner ``braunschweig.calibration.srv_distance_targets.ZGB_KREISE``: the study region.
ZGB_KREISE = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")
#: Owner ``braunschweig.calibration.srv_distance_targets.WOLFSBURG_KREIS``.
WOLFSBURG_KREIS = "03103"
#: Owners ``srv_student_reference.HOME_AREAS`` and ``config_keys.DEFAULT_STUDENT_HOME_AREA_KREISE``.
_DEFAULT_HOME_AREA_KREISE = {
    "braunschweig": ["03101"],
    "surrounding": ["03102", "03151", "03153", "03154", "03157", "03158"],
}

#: Owner ``braunschweig.data.education.student_od_table.T_COLUMNS``: the columns of the table T
#: (importing it would load student_geometry, whose lazy import of braunschweig.analysis.spatial names
#: the VG250 archive).
T_COLUMNS = ["home_ars5", "home_in_region", "destination_id", "institution", "site_kreis_ars5", "scope",
             "enrollment_persons", "distance_km", "coeff_short", "coeff_middle", "coeff_long",
             "origin_persons_scaled", "upper_bound_persons_scaled", "weight", "students_persons", "movers_persons",
             "cell_kind"]

ABSENCE_MODE_CELLS = "cells"
ABSENCE_MODE_OFF = "off"
ABSENCE_MODES = (ABSENCE_MODE_CELLS, ABSENCE_MODE_OFF)

PARAMETERS_TABLE = "srv2023_student_parameters.csv"
PARAMETERS_BY_AREA_TABLE = "srv2023_student_parameters_by_area.csv"
_AWAY_ROW = {"parameter": "away_all_day", "base": "students_all", "label": "yes"}

_SEED_COLUMNS = ["home_ars5", "destination_id", "seed"]
_KREIS_PATTERN = r"\d{5}"
_NUMBER = r"[0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?"


# --------------------------------------------------------------------------- home areas
def default_home_area_kreise() -> dict[str, list[str]]:
    """The default home areas ``{area: [Kreis codes]}`` (a fresh copy).

    Equals ``srv_student_reference.HOME_AREAS`` and ``config_keys.DEFAULT_STUDENT_HOME_AREA_KREISE``
    (both pinned by the Phase 3 pin test).
    """
    return {area: list(kreise) for area, kreise in _DEFAULT_HOME_AREA_KREISE.items()}


def area_by_kreis(home_areas: Mapping[str, Sequence[str]], region_kreise: Sequence[str]) -> dict[str, str]:
    """``{Kreis: home area}`` for every Kreis of ``region_kreise``; Wolfsburg maps to ``AREA_WOLFSBURG``.

    Pass the STUDY region (all Kreise of the level table), not the Kreise of the run: the areas are
    checked against the region, so a run over fewer Kreise keeps the default areas.

    The rule of Phase 3's validator (``student_targets.area_of_kreis_from_home_areas``): ``home_areas``
    (the value of :data:`KEY_HOME_AREA_KREISE`) must partition the region WITHOUT Wolfsburg, which SrV
    does not survey and which takes the value of the region (ADR-0205). Raises ``ValueError`` naming
    the key when a value is no list of Kreis codes, a Kreis lies in more than one area, Wolfsburg lies
    in an area, an area holds a Kreis outside the region or a Kreis of the region other than
    Wolfsburg lies in no area.
    """
    key = KEY_HOME_AREA_KREISE
    if not isinstance(home_areas, Mapping) or not home_areas:
        raise ValueError(f"{key} must be a non-empty mapping {{area: [Kreis codes]}}, got {home_areas!r}")
    area_of: dict[str, str] = {}
    for area, kreise in home_areas.items():
        if isinstance(kreise, str) or not isinstance(kreise, Sequence) or not all(
                isinstance(kreis, str) for kreis in kreise):
            raise ValueError(f"{key}: area {area!r} must be a list of Kreis codes, got {kreise!r}")
        for kreis in kreise:
            if kreis in area_of:
                raise ValueError(f"{key}: Kreis {kreis} lies in more than one area "
                                 f"({area_of[kreis]!r} and {area!r})")
            area_of[kreis] = str(area)
    if WOLFSBURG_KREIS in area_of:
        raise ValueError(f"{key}: Wolfsburg ({WOLFSBURG_KREIS}) is not surveyed by SrV and takes the value of "
                         f"the region; remove it from area {area_of[WOLFSBURG_KREIS]!r}")
    region = [str(kreis) for kreis in region_kreise]
    outside = sorted(set(area_of) - set(region))
    if outside:
        raise ValueError(f"{key}: Kreis(e) {outside} lie outside the region {region}")
    uncovered = [kreis for kreis in region if kreis != WOLFSBURG_KREIS and kreis not in area_of]
    if uncovered:
        raise ValueError(f"{key}: Kreis(e) {uncovered} of the region lie in no home area")
    return {kreis: (AREA_WOLFSBURG if kreis == WOLFSBURG_KREIS else area_of[kreis]) for kreis in region}


def home_kreis_by_household(home_zones: pd.DataFrame) -> pd.Series:
    """``Series[household_id -> 5-digit Kreis code]`` of the stage ``synthesis.population.spatial.home.zones``.

    The column is ``departement_id``. Evidence (read from the code, no cache available at the time of
    writing; the checkpoint run on the server confirms it and this function raises if it does not
    hold): ``braunschweig.synthesis.spatial.home_zones`` passes ``departement_id`` and ``commune_id``
    of ``synthesis.population.sampled`` through, one row per household; ``sampled`` copies the persons
    of ``data.census.filtered``, which ``configs/base_bs.yml`` aliases to ``braunschweig.popsim.stage``;
    its persons frame is built by ``braunschweig.popsim.assembly.derive_zone_ids``, which sets
    ``commune_id`` = the 12-digit ARS and ``departement_id = commune_id[:5]``, the 5-digit Kreis
    (the rule of ``braunschweig.ipf.prepare``). ``commune_id`` itself is the 12-digit ARS and no Kreis.

    Raises ``ValueError`` (no fallback) when a household id is duplicated, when a ``departement_id`` is
    missing or not 5 digits, when it is no Kreis of the region ``ZGB_KREISE``
    ("a household without a Kreis of the region") or when a present ``commune_id`` does not start
    with it.
    """
    for column in ("household_id", "departement_id"):
        if column not in home_zones.columns:
            raise ValueError(f"{_LOG_TAG} home zones: column {column!r} is absent; found {list(home_zones.columns)}")
    kreis = home_zones["departement_id"].astype(object)
    # astype(str) would turn a missing value into the text 'nan': test the object column itself.
    well_formed = kreis.map(lambda value: isinstance(value, str) and re.fullmatch(_KREIS_PATTERN, value) is not None)
    if not well_formed.all():
        bad = kreis[~well_formed]
        raise ValueError(f"{_LOG_TAG} home zones: {len(bad)} household(s) whose departement_id is no 5-digit "
                         f"Kreis code (departement_id was expected to hold commune_id[:5]); first values "
                         f"{bad.head(5).tolist()}")
    outside = kreis[~kreis.isin(ZGB_KREISE)]
    if len(outside) > 0:
        raise ValueError(f"{_LOG_TAG} home zones: {len(outside)} household(s) with a Kreis outside the region "
                         f"{list(ZGB_KREISE)}: {sorted(set(outside))[:12]}")
    if "commune_id" in home_zones.columns:
        commune = home_zones["commune_id"].astype(object)
        mismatch = commune.map(lambda value: isinstance(value, str)) & ~(
            commune.map(lambda value: str(value)[:5]) == kreis)
        if mismatch.any():
            raise ValueError(f"{_LOG_TAG} home zones: departement_id differs from commune_id[:5] in "
                             f"{int(mismatch.sum())} household(s); departement_id does not hold the Kreis")
    result = pd.Series(kreis.to_numpy(dtype=object), index=pd.Index(home_zones["household_id"], name="household_id"),
                       name="home_kreis", dtype=object)
    if result.index.duplicated().any():
        raise ValueError(f"{_LOG_TAG} home zones: household_id is not unique "
                         f"({int(result.index.duplicated().sum())} duplicate(s))")
    return result


# --------------------------------------------------------------------------- the table T
def load_od_table(path) -> pd.DataFrame:
    """The origin-destination table T with the columns of ``T_COLUMNS``.

    ``home_ars5`` and ``site_kreis_ars5`` are ``str``; ``home_in_region`` is ``bool``. Raises
    ``ValueError`` on a missing column, a null key column, ``scope`` or ``site_kreis_ars5``, or a
    negative or non-finite ``students_persons``.
    """
    path = Path(path)
    table = pd.read_csv(path, comment="#", encoding="utf-8", dtype={"home_ars5": str, "site_kreis_ars5": str})
    missing = [column for column in T_COLUMNS if column not in table.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} {path.name}: missing column(s) {missing}; found {list(table.columns)}")
    table = table[T_COLUMNS].copy()
    if table["home_in_region"].dtype != bool:
        raise ValueError(f"{_LOG_TAG} {path.name}: home_in_region must be True/False, got dtype "
                         f"{table['home_in_region'].dtype}")
    keys = table[["home_ars5", "destination_id", "cell_kind", "scope", "site_kreis_ars5"]]
    null_columns = [column for column in keys.columns if keys[column].isna().any()]
    if null_columns:
        raise ValueError(f"{_LOG_TAG} {path.name}: null value(s) in the key column(s) {null_columns} "
                         f"(home_ars5, destination_id, cell_kind, scope and site_kreis_ars5 must be filled)")
    students = table["students_persons"]
    if not (np.isfinite(students) & (students >= 0)).all():
        raise ValueError(f"{_LOG_TAG} {path.name}: non-finite or negative students_persons")
    return table.reset_index(drop=True)


def _seed(rows: pd.DataFrame) -> pd.DataFrame:
    seed = rows.groupby(["home_ars5", "destination_id"], sort=True)["students_persons"].sum()
    return seed.rename("seed").reset_index()[_SEED_COLUMNS]


def region_seed(od: pd.DataFrame) -> pd.DataFrame:
    """``[home_ars5, destination_id, seed]``: the heads living in the region, summed over ``cell_kind``.

    A pair can hold several rows (Braunschweig -> Wolfenbuettel has a commuter and a mover row);
    the seed of the residents' assignment is their sum.
    """
    return _seed(od[od["home_in_region"]])


def outside_seed(od: pd.DataFrame) -> pd.DataFrame:
    """``[home_ars5, destination_id, seed]``: the heads of the rows with ``home_in_region == False``."""
    return _seed(od[~od["home_in_region"]])


def destination_shares(od: pd.DataFrame) -> tuple[pd.Series, list[str]]:
    """``(Series[destination_id -> share], dropped)``: each destination's share of the region's heads.

    A destination without a student of the region (share 0) is dropped from the series and returned,
    sorted, in the list, so the caller can log it.
    """
    region = od[od["home_in_region"]]
    per_destination = region.groupby("destination_id")["students_persons"].sum()
    # Destinations of T without a region row at all count as share 0 as well.
    per_destination = per_destination.reindex(sorted(set(od["destination_id"])), fill_value=0.0)
    total = float(per_destination.sum())
    if not total > 0:
        raise ValueError(f"{_LOG_TAG} the table T holds no student living in the region")
    shares = per_destination / total
    dropped = sorted(shares.index[shares == 0.0])
    shares = shares[shares > 0.0].rename("share")
    shares.index.name = "destination_id"
    return shares, dropped


def _leading_header_lines(path: Path) -> list[str]:
    lines = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            lines.append(line)
    return lines


def _header_line(lines: list[str], prefix: str, path: Path) -> str:
    found = [line for line in lines if line.startswith(prefix)]
    if len(found) != 1:
        raise ValueError(f"{_LOG_TAG} {path.name}: expected exactly one header line starting with {prefix!r}, "
                         f"found {len(found)}")
    return found[0]


def _line_number(line: str, key: str, path: Path) -> float:
    found = re.findall(r"(?<![\w])" + re.escape(key) + "=(" + _NUMBER + r")(?![\w.])", line)
    if len(found) != 1:
        raise ValueError(f"{_LOG_TAG} {path.name}: header key {key!r} found {len(found)} time(s) in "
                         f"{line.strip()[:80]!r}, expected once")
    value = float(found[0])
    if not math.isfinite(value):
        raise ValueError(f"{_LOG_TAG} {path.name}: header key {key!r} is not finite")
    return value


def od_weights(path) -> dict[str, float]:
    """``{short, middle, long, max_commute_km}`` of the header lines ``# Weights:`` and ``# Weight rule:``.

    The header of T is the one place the commuter weights of its construction are recorded; there is no
    default. Raises ``ValueError`` when a line or key is absent or repeated, or a weight lies outside [0, 1]
    or ``max_commute_km`` is not positive.
    """
    path = Path(path)
    lines = _leading_header_lines(path)
    weights_line = _header_line(lines, "# Weights:", path)
    rule_line = _header_line(lines, "# Weight rule:", path)
    weights = {key: _line_number(weights_line, key, path) for key in ("short", "middle", "long")}
    for key, value in weights.items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{_LOG_TAG} {path.name}: weight {key}={value} lies outside [0, 1]")
    max_commute = _line_number(rule_line, "max_commute_km", path)
    if not max_commute > 0:
        raise ValueError(f"{_LOG_TAG} {path.name}: max_commute_km={max_commute} must be positive")
    return {**weights, "max_commute_km": max_commute}


# --------------------------------------------------------------------------- the level table
def load_level(path, region_kreise: Sequence[str]) -> pd.DataFrame:
    """``[code, age_range, students_persons]`` of the level table for ``region_kreise``.

    ``age_range`` is ``18_29`` (the groups 18_19, 20_24 and 25_29 summed over both sexes) or ``30plus``.
    Every Kreis x sex x age group of the region must be present exactly once; rows of other Kreise are
    ignored. Raises ``ValueError`` on a missing column, an unknown sex or age group, a missing or
    repeated cell or a negative or non-finite count.
    """
    path = Path(path)
    table = pd.read_csv(path, comment="#", encoding="utf-8", dtype={"code": str, "sex": str, "age_group": str})
    needed = ["code", "sex", "age_group", "students_persons"]
    missing = [column for column in needed if column not in table.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} {path.name}: missing column(s) {missing}; found {list(table.columns)}")
    unknown_sex = sorted(set(table["sex"].dropna()) - set(LEVEL_SEXES))
    unknown_age = sorted(set(table["age_group"].dropna()) - set(LEVEL_AGE_GROUPS))
    if unknown_sex or unknown_age:
        raise ValueError(f"{_LOG_TAG} {path.name}: unknown sex {unknown_sex} / age group {unknown_age}")
    region = [str(kreis) for kreis in region_kreise]
    table = table[table["code"].isin(region)].copy()
    counts = table.groupby(["code", "sex", "age_group"]).size()
    repeated = [key for key, n in counts.items() if n > 1]
    if repeated:
        raise ValueError(f"{_LOG_TAG} {path.name}: cell(s) present more than once: {repeated[:12]}")
    absent = [(kreis, sex, age) for kreis in region for sex in LEVEL_SEXES for age in LEVEL_AGE_GROUPS
              if (kreis, sex, age) not in counts.index]
    if absent:
        raise ValueError(f"{_LOG_TAG} {path.name}: {len(absent)} (Kreis, sex, age group) cell(s) of the region "
                         f"are missing: {absent[:12]}")
    students = pd.to_numeric(table["students_persons"], errors="coerce")
    if not (np.isfinite(students) & (students >= 0)).all():
        raise ValueError(f"{_LOG_TAG} {path.name}: non-finite or negative students_persons")
    table["students_persons"] = students.astype(float)
    table["age_range"] = np.where(table["age_group"].isin(YOUNG_AGE_GROUPS), AGE_RANGE_YOUNG, AGE_RANGE_OLD)
    level = table.groupby(["code", "age_range"], sort=True)["students_persons"].sum().reset_index()
    logger.info("%s %s: %d Kreise, %.1f students (%d cells summed to %d rows)", _LOG_TAG, path.name, len(region),
                float(level["students_persons"].sum()), len(table), len(level))
    return level[["code", "age_range", "students_persons"]]


# --------------------------------------------------------------------------- the day factor
def education_leg_share(srv_dir) -> float:
    """The share of the students with an education leg (control definition), header of the SrV table."""
    path = Path(srv_dir) / PARAMETERS_TABLE
    value = float(read_header_value(path, EDUCATION_LEG_SHARE_HEADER_KEY))
    if not 0.0 < value <= 1.0:
        raise ValueError(f"{_LOG_TAG} {path.name}: {EDUCATION_LEG_SHARE_HEADER_KEY}={value} lies outside (0, 1]")
    return value


def _single_away_share(table: pd.DataFrame, path: Path, **keys: str) -> float:
    selected = table
    for column, value in {**_AWAY_ROW, **keys}.items():
        selected = selected[selected[column] == value]
    if len(selected) != 1:
        raise ValueError(f"{_LOG_TAG} {path.name}: expected exactly one away_all_day row of the students "
                         f"({keys or 'region'}), found {len(selected)}")
    share = float(selected["share"].iloc[0])
    if not 0.0 <= share < 1.0:
        raise ValueError(f"{_LOG_TAG} {path.name}: away_all_day share {share} lies outside [0, 1) ({keys})")
    return share


def away_share_by_area(srv_dir) -> dict[str, float]:
    """``{area: share of the students away from home all day}``.

    The surveyed areas (the keys of :func:`default_home_area_kreise`) come from the row
    ``(day, away_all_day, students_all, yes)`` of ``srv2023_student_parameters_by_area.csv``;
    ``AREA_WOLFSBURG`` takes the same row of ``srv2023_student_parameters.csv`` (the region; ADR-0205).
    """
    by_area_path = Path(srv_dir) / PARAMETERS_BY_AREA_TABLE
    by_area = pd.read_csv(by_area_path, comment="#", encoding="utf-8")
    by_area = by_area[by_area["group"] == "day"]
    result = {area: _single_away_share(by_area, by_area_path, area=area) for area in _DEFAULT_HOME_AREA_KREISE}
    region_path = Path(srv_dir) / PARAMETERS_TABLE
    region = pd.read_csv(region_path, comment="#", encoding="utf-8")
    region = region[region["group"] == "day"]
    result[AREA_WOLFSBURG] = _single_away_share(region, region_path)
    return result


def day_factor_by_kreis(e: float, away_by_area: Mapping[str, float], area_of_kreis: Mapping[str, str],
                        absence_mode: str) -> pd.Series:
    """``Series[Kreis code -> f(k)]``, the share of the students of a Kreis present at an education place.

    ``f(k) = e * (1 - away(area(k)))`` with ``e`` the education-leg share (:func:`education_leg_share`)
    and ``away`` the share away from home all day of the Kreis's area. ``absence_mode`` is ``"cells"``
    or ``"off"``; ``"off"`` sets the away share to 0 (``f = e``); any other value raises.
    """
    if absence_mode not in ABSENCE_MODES:
        raise ValueError(f"{_LOG_TAG} absence_mode must be one of {list(ABSENCE_MODES)}, got {absence_mode!r}")
    if not 0.0 < e <= 1.0:
        raise ValueError(f"{_LOG_TAG} education-leg share e={e!r} lies outside (0, 1]")
    away = {}
    for kreis, area in area_of_kreis.items():
        if absence_mode == ABSENCE_MODE_OFF:
            away[kreis] = 0.0
            continue
        if area not in away_by_area:
            raise ValueError(f"{_LOG_TAG} no away share for area {area!r} of Kreis {kreis}; areas with a share: "
                             f"{sorted(away_by_area)}")
        share = float(away_by_area[area])
        if not 0.0 <= share < 1.0:
            raise ValueError(f"{_LOG_TAG} away share {share} of area {area!r} lies outside [0, 1)")
        away[kreis] = share
    factor = pd.Series({kreis: e * (1.0 - away[kreis]) for kreis in area_of_kreis}, dtype=float, name="day_factor")
    factor.index.name = "code"
    return factor


def effective_day_factor(level_by_kreis: pd.Series, f_by_kreis: pd.Series) -> float:
    """The level-weighted mean of ``f(k)``: ``sum(level_k * f_k) / sum(level_k)``.

    Raises ``ValueError`` when a Kreis of the level has no ``f`` or the level is empty, negative or
    non-finite.
    """
    absent = [str(kreis) for kreis in level_by_kreis.index if kreis not in f_by_kreis.index]
    if absent:
        raise ValueError(f"{_LOG_TAG} no day factor for Kreis(e) {absent} of the level")
    weights = level_by_kreis.astype(float)
    if not (np.isfinite(weights) & (weights >= 0)).all() or not weights.sum() > 0:
        raise ValueError(f"{_LOG_TAG} the level must be finite, non-negative and not all zero")
    return float((weights * f_by_kreis.loc[weights.index].astype(float)).sum() / weights.sum())
