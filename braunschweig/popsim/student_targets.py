"""The student targets of the PopulationSim stage (design A3 of the student layer), as pure functions.

The four student entries of the Kreis attribute control (education leg by student status for the ages
18 to 29 and for 30 or older, student work leg, student season ticket) are NOT read from a share
table: their per-Kreis targets are counts built here from the student level table (A2, ADR-0211)
and a few committed SrV shares. This module reads those shares and builds the integer tables; the
stage only calls it. It imports no calibration module (the stage's cache token must not cover them);
the committed tables and their definitions are owned by ``braunschweig.calibration.srv_student_reference``
and the headers of the tables.

Rules implemented (the design owns them; numbers live in the CSV headers, not here):

* **One student count** (A3 "One student count"). The students of a Kreis are rounded ONCE: the level of
  the Kreis is rounded to a whole number and split over the two age ranges (18 to 29, 30 or older) by
  largest remainder (:func:`student_counts`). Every student label of every entry is a part of that one
  count, so the entries never disagree.
* **The other adults** (A3 "The other adults", A24). A Kreis keeps its own SrV share ``s_k`` of non-student
  adults with an education leg. ``s_k`` times the Kreis's non-student adults (adults minus the students of
  the level) is its number of other adults with an education leg ``O_k``. ``O_k`` is split over the two age
  ranges in proportion to ``N_r * p_r`` (the non-student persons of the range times the REGIONAL share of
  non-student persons of that range with an education leg) and rounded by largest remainder to
  ``round(O_k)``.
* **A9** (ASSUMPTION): the SrV shares of the education leg ``e`` and of the work leg ``w`` within students
  hold for every Kreis of the region; the season-ticket share holds within each home area.
* **A21** (owner decision 2026-11-27, one Wolfsburg rule of ADR-0205): Wolfsburg (03103) is not surveyed;
  its season-ticket share and its other-adult education-leg share are the SrV value of the region.
  Such a use of a regional value is counted and logged as a rate, not applied silently.
* **A22** (ASSUMPTION): the education leg of the students aged 30 or older is not steered; the entry
  holds them as one label.
* **A23** (ASSUMPTION): the SrV work-leg share of all adult students holds for the students aged 18 to 29.

No fallback value exists: a missing file, row or column, a share outside (0, 1), or any negative
remainder raises ``ValueError`` naming the file and column. Every count is rounded with
``kreis_attribute_control.round_count`` (Python ``round``), the convention of every KREIS entry, and
split by largest remainder with ``braunschweig.popsim.controls.largest_remainder_round``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.data.csv_header import read_header_mapping, read_header_value
from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.popsim.controls import largest_remainder_round
from braunschweig.popsim.student_level import AGE_LABELS, YOUNG_AGE_LABELS, StudentLevel

logger = logging.getLogger(__name__)

_LOG_TAG = "[student targets]"

#: Committed SrV tables, relative to ``data_path`` (the same ``eqasim-data/data`` root as the level table).
REGIONAL_RELPATH = "braunschweig/srv/srv2023_student_parameters.csv"
AREA_RELPATH = "braunschweig/srv/srv2023_student_parameters_by_area.csv"
OTHER_ADULTS_RELPATH = "braunschweig/srv/srv2023_education_leg_by_student_status.csv"

#: Header keys of the control definitions of the regional parameter table (section 5a, A23).
EDUCATION_SHARE_HEADER_KEY = "education_leg_share_control_definition"
WORK_SHARE_HEADER_KEY = "work_leg_share_control_definition"

#: The two age ranges of the entries (spec A3 "Age"): the three young level groups, and 30 or older.
AGE_RANGE_YOUNG = "18_29"
AGE_RANGE_OLD = "30plus"
AGE_RANGES = (AGE_RANGE_YOUNG, AGE_RANGE_OLD)
#: Level age group -> age range of the entries.
_RANGE_OF_LEVEL_GROUP = {label: (AGE_RANGE_YOUNG if label in YOUNG_AGE_LABELS else AGE_RANGE_OLD)
                         for label in AGE_LABELS}

#: Codes of the SrV tables (owner of the values: ``srv_participation_universe.REGION_CODE`` and the
#: Wolfsburg rule of ADR-0205; typed here only because the calibration modules stay out of the stage).
REGION_CODE = "03ZGB"
WOLFSBURG_CODE = "03103"

# Entry names, category labels and universes of the four student entries: owned by
# ``kreis_attribute_control`` (which imports no calibration module either), so the seed column's
# labels, the registry entry and these target tables cannot drift apart. Overridable through the
# ``categories`` argument of the builders.
ENTRY_EDUCATION_YOUNG = kac.STUDENT_EDUCATION_18_29
ENTRY_EDUCATION_OLD = kac.STUDENT_EDUCATION_30PLUS
ENTRY_WORK_LEG = kac.STUDENT_WORK_LEG
ENTRY_SEASON_TICKET = kac.STUDENT_SEASON_TICKET
STUDENT_ENTRY_CATEGORIES: dict[str, tuple[str, ...]] = {
    ENTRY_EDUCATION_YOUNG: kac.STUDENT_EDUCATION_18_29_CATEGORIES,
    ENTRY_EDUCATION_OLD: kac.STUDENT_EDUCATION_30PLUS_CATEGORIES,
    ENTRY_WORK_LEG: kac.STUDENT_WORK_LEG_CATEGORIES,
    ENTRY_SEASON_TICKET: kac.STUDENT_SEASON_TICKET_CATEGORIES,
}
#: Age range of the universe of each entry (the persons its categories partition), read off
#: ``kac.STUDENT_AGE_BOUNDS`` (18 to 29 -> the young range, 30 or older -> the old range).
_RANGE_OF_BOUNDS = {(18, 29): AGE_RANGE_YOUNG, (30, None): AGE_RANGE_OLD}
STUDENT_ENTRY_AGE_RANGE: dict[str, str] = {
    name: _RANGE_OF_BOUNDS[bounds] for name, bounds in kac.STUDENT_AGE_BOUNDS.items()}

_KREIS_COLUMN = "ARS_kreis"


@dataclass(frozen=True)
class StudentShares:
    """The SrV shares the target builder needs (all in (0, 1)).

    ``education_leg`` is ``e`` and ``work_leg`` ``w`` (regional, A9, A23); ``season_ticket_by_area`` the
    flat-rate share per home area and ``season_ticket_region`` the regional value Wolfsburg takes (A21);
    ``other_education_by_kreis`` the share of non-student adults with an education leg per Kreis
    (Wolfsburg = the regional value, A21) and ``other_education_by_age_range`` the regional share per
    age range of the entries.
    """
    education_leg: float
    work_leg: float
    season_ticket_by_area: Mapping[str, float]
    season_ticket_region: float
    other_education_by_kreis: Mapping[str, float]
    other_education_by_age_range: Mapping[str, float]


# ----------------------------------------------------------------------------- reading


def _read_table(data_path, relpath: str, required: Sequence[str]) -> tuple[Path, pd.DataFrame]:
    path = Path(data_path) / relpath
    if not path.is_file():
        raise ValueError(f"{_LOG_TAG} the committed SrV table {path} does not exist; restore it "
                         "(scripts/extract_srv_student_reference.py writes it)")
    table = pd.read_csv(path, comment="#", dtype=str)
    missing = [column for column in required if column not in table.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} {path.name}: missing column(s) {missing}; found {list(table.columns)}")
    return path, table


def _share_value(raw, path: Path, what: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{_LOG_TAG} {path.name}: {what} is not a number ({raw!r})") from error
    if not (math.isfinite(value) and 0.0 < value < 1.0):
        raise ValueError(f"{_LOG_TAG} {path.name}: {what} = {value!r} lies outside (0, 1)")
    return value


def _header_share(path: Path, key: str) -> float:
    try:
        raw = read_header_value(path, key)
    except ValueError as error:
        raise ValueError(f"{_LOG_TAG} {path.name}: no usable header key {key!r} ({error})") from error
    return _share_value(raw, path, f"header key {key!r}")


def _single_share(table: pd.DataFrame, path: Path, column: str, **keys: str) -> float:
    selected = table
    for name, value in keys.items():
        selected = selected[selected[name] == value]
    description = ", ".join(f"{name}={value}" for name, value in keys.items())
    if len(selected) != 1:
        raise ValueError(f"{_LOG_TAG} {path.name}: expected exactly one row with {description}, "
                         f"found {len(selected)}")
    return _share_value(selected[column].iloc[0], path, f"column {column!r} of the row {description}")


#: The header line of :data:`AREA_RELPATH` that names the Kreise of each home area the table was written for
#: (``# Home areas: braunschweig=03101; surrounding=03102,...``, written by
#: ``scripts/extract_srv_student_reference.py`` from ``srv_student_reference.HOME_AREAS``).
HOME_AREAS_HEADER_LABEL = "Home areas"


def _check_home_areas_against_table(area_path: Path, home_areas: Mapping[str, Sequence[str]]) -> None:
    """Raise unless the configured home areas are exactly the areas the by-area table was written for.

    The ticket share of a home area is the SrV share of the Kreise the table pooled into that area; a
    configured area with other Kreise would silently take the wrong share. The order of the Kreise is no
    difference."""
    from braunschweig.popsim.stage.config_keys import KEY_STUDENT_HOME_AREA_KREISE as key
    try:
        table_areas = read_header_mapping(area_path, HOME_AREAS_HEADER_LABEL)
    except ValueError as error:
        raise ValueError(f"{_LOG_TAG} {area_path.name}: the home areas of the table cannot be checked "
                         f"against {key} ({error}); regenerate it with "
                         "scripts/extract_srv_student_reference.py") from error
    configured = {str(area): tuple(sorted(str(kreis) for kreis in kreise))
                  for area, kreise in home_areas.items()}
    in_table = {area: tuple(sorted(kreise)) for area, kreise in table_areas.items()}
    if configured != in_table:
        differences = []
        for area in sorted(set(configured) | set(in_table)):
            if configured.get(area) != in_table.get(area):
                differences.append(f"{area}: configured {list(configured.get(area, ()))}, "
                                   f"table {list(in_table.get(area, ()))}")
        raise ValueError(f"{_LOG_TAG} {area_path.name}: the home areas of {key} differ from the areas the SrV "
                         f"table was written for ({'; '.join(differences)}); the flat-rate share of an area "
                         "is the SrV share of exactly its table Kreise, so change the config back or "
                         "regenerate the table with the new areas (srv_student_reference.HOME_AREAS)")


def read_student_shares(data_path, expected_kreise: Sequence[str],
                        areas: Sequence[str] | None = None,
                        home_areas: Mapping[str, Sequence[str]] | None = None) -> StudentShares:
    """Read the committed SrV tables under ``data_path``; no fallback.

    ``home_areas`` (the value of ``config_keys.KEY_STUDENT_HOME_AREA_KREISE``) is checked against the
    ``# Home areas:`` header of :data:`AREA_RELPATH`: ``ValueError`` when the Kreise of any area differ or the
    header is absent. With it, ``areas`` defaults to its areas.

    ``education_leg`` and ``work_leg`` come from the headers of :data:`REGIONAL_RELPATH` (the control
    definitions, A9 and A23); the regional flat-rate share of students from its row ``season_ticket`` /
    ``students_present`` / ``flatrate``. :data:`AREA_RELPATH` gives the same row per ``area`` (every area of
    ``areas`` must be present; with ``areas=None`` every area found is read, at least one is required).
    :data:`OTHER_ADULTS_RELPATH` gives, for ``group == other_adult``, the ``18plus`` share per Kreis of
    ``expected_kreise`` (Wolfsburg takes the ``03ZGB`` value, A21; every other Kreis needs a finite share)
    and the regional shares of the two age ranges. A missing file, row or column, or a share outside
    (0, 1), raises ``ValueError`` naming the file and the column.
    """
    regional_path, regional = _read_table(
        data_path, REGIONAL_RELPATH, ("parameter", "base", "label", "share"))
    education = _header_share(regional_path, EDUCATION_SHARE_HEADER_KEY)
    work = _header_share(regional_path, WORK_SHARE_HEADER_KEY)
    ticket_region = _single_share(regional, regional_path, "share", parameter="season_ticket",
                                  base="students_present", label="flatrate")

    area_path, by_area = _read_table(
        data_path, AREA_RELPATH, ("area", "parameter", "base", "label", "share"))
    if home_areas is not None:
        _check_home_areas_against_table(area_path, home_areas)
        if areas is None:
            areas = sorted(home_areas)
    flatrate_rows = by_area[(by_area["parameter"] == "season_ticket")
                            & (by_area["base"] == "students_present") & (by_area["label"] == "flatrate")]
    found_areas = sorted(set(flatrate_rows["area"]))
    wanted_areas = list(areas) if areas is not None else found_areas
    if not wanted_areas:
        raise ValueError(f"{_LOG_TAG} {area_path.name}: no season_ticket/students_present/flatrate row "
                         "(column 'area')")
    ticket_by_area = {area: _single_share(by_area, area_path, "share", area=area,
                                          parameter="season_ticket", base="students_present",
                                          label="flatrate") for area in wanted_areas}

    other_path, other = _read_table(
        data_path, OTHER_ADULTS_RELPATH, ("code", "level", "group", "age_range", "share"))
    other = other[other["group"] == "other_adult"]
    if other.empty:
        raise ValueError(f"{_LOG_TAG} {other_path.name}: no row with group == 'other_adult' (column 'group')")
    region_18plus = _single_share(other, other_path, "share", code=REGION_CODE, age_range="18plus")
    by_range = {age_range: _single_share(other, other_path, "share", code=REGION_CODE, age_range=age_range)
                for age_range in AGE_RANGES}
    by_kreis: dict[str, float] = {}
    for kreis in expected_kreise:
        kreis = str(kreis)
        if kreis == WOLFSBURG_CODE:
            by_kreis[kreis] = region_18plus
        else:
            by_kreis[kreis] = _single_share(other, other_path, "share", code=kreis, age_range="18plus")
    logger.info("%s %s: other-adult education-leg share of %d Kreise (Wolfsburg rule A21: %d on the "
                "regional value); %s: flat-rate share of %d area(s)", _LOG_TAG, other_path.name,
                len(by_kreis), sum(kreis == WOLFSBURG_CODE for kreis in by_kreis), area_path.name,
                len(ticket_by_area))
    return StudentShares(education_leg=education, work_leg=work, season_ticket_by_area=ticket_by_area,
                         season_ticket_region=ticket_region, other_education_by_kreis=by_kreis,
                         other_education_by_age_range=by_range)


def area_of_kreis_from_home_areas(home_areas: Mapping[str, Sequence[str]],
                                  region_kreise: Sequence[str],
                                  run_kreise: Sequence[str] | None = None) -> dict[str, str | None]:
    """``{Kreis: home area}`` for every Kreis of the run; Wolfsburg maps to ``None`` (A21).

    ``home_areas`` is the value of ``config_keys.KEY_STUDENT_HOME_AREA_KREISE`` (``{area: [Kreis
    codes]}``). It is checked against the STUDY region ``region_kreise`` (the stage passes the Kreise of
    the student level table), not against the run, so a run over fewer Kreise keeps the default areas.
    It must partition the region without Wolfsburg: ``ValueError`` naming the key when a value is no
    list of Kreis codes, a Kreis lies in more than one area, Wolfsburg lies in an area, an area holds a
    Kreis outside the region, or a Kreis of the region other than Wolfsburg lies in no area (A20, A21).
    ``run_kreise`` (default: the region) must lie in the region; the result maps exactly those Kreise.
    """
    # Deferred: importing the stage package's config module from here at module level would be
    # circular (the stage package imports this module).
    from braunschweig.popsim.stage.config_keys import KEY_STUDENT_HOME_AREA_KREISE as key

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
    if WOLFSBURG_CODE in area_of:
        raise ValueError(f"{key}: Wolfsburg ({WOLFSBURG_CODE}) is not surveyed by SrV and takes the value "
                         f"of the region (A21); remove it from area {area_of[WOLFSBURG_CODE]!r}")
    region = [str(kreis) for kreis in region_kreise]
    outside = sorted(set(area_of) - set(region))
    if outside:
        raise ValueError(f"{key}: Kreis(e) {outside} lie outside the region {region}")
    uncovered = [kreis for kreis in region if kreis != WOLFSBURG_CODE and kreis not in area_of]
    if uncovered:
        raise ValueError(f"{key}: Kreis(e) {uncovered} of the region lie in no home area")
    run = region if run_kreise is None else [str(kreis) for kreis in run_kreise]
    foreign = [kreis for kreis in run if kreis not in region]
    if foreign:
        raise ValueError(f"{key}: Kreis(e) {foreign} of the run lie outside the study region {region}, "
                         "so they have no student level and no home area")
    return {kreis: area_of.get(kreis) for kreis in run}


# ----------------------------------------------------------------------------- the one student count


def student_counts(level: StudentLevel) -> pd.DataFrame:
    """Students per Kreis and age range, rounded once (A3 "One student count").

    Columns ``ars5, age_range, students_persons, students_count``. ``students_persons`` is the level
    summed over both sexes (``18_29`` = ``18_19`` + ``20_24`` + ``25_29``; ``30plus``). ``students_count``
    is the largest-remainder rounding over the two ranges of a Kreis, so
    ``students_count`` summed over the ranges equals ``round(level of the Kreis)``.
    """
    table = level.table.assign(age_range=level.table["age_group"].map(_RANGE_OF_LEVEL_GROUP))
    sums = table.groupby(["code", "age_range"])["students_persons"].sum()
    rows = []
    for kreis in sorted(set(table["code"])):
        persons = [float(sums.get((kreis, age_range), 0.0)) for age_range in AGE_RANGES]
        counts = largest_remainder_round(persons, kac.round_count(sum(persons)))
        rows.extend({"ars5": str(kreis), "age_range": age_range, "students_persons": person,
                     "students_count": count}
                    for age_range, person, count in zip(AGE_RANGES, persons, counts))
    return pd.DataFrame(rows, columns=["ars5", "age_range", "students_persons", "students_count"])


# ----------------------------------------------------------------------------- the entry tables


def _persons(persons_by_range: Mapping[tuple[str, str], float], kreis: str, age_range: str) -> float:
    try:
        value = float(persons_by_range[(kreis, age_range)])
    except KeyError as error:
        raise ValueError(f"{_LOG_TAG} persons_by_range has no value for (Kreis {kreis}, age range "
                         f"{age_range})") from error
    if not (math.isfinite(value) and value >= 0):
        raise ValueError(f"{_LOG_TAG} persons of (Kreis {kreis}, {age_range}) is {value!r}")
    return value


def _non_negative(value: int, kreis: str, entry: str, label: str) -> int:
    if value < 0:
        raise ValueError(f"{_LOG_TAG} Kreis {kreis}, entry {entry}: negative remainder {value} for "
                         f"'{label}' (more students or other adults than persons of the range)")
    return value


def _entry_categories(categories: Mapping[str, Sequence[str]] | None) -> Mapping[str, Sequence[str]]:
    return STUDENT_ENTRY_CATEGORIES if categories is None else categories


def student_kreis_targets_with_diagnostics(
        counts: pd.DataFrame, persons_by_range: Mapping[tuple[str, str], float],
        shares: StudentShares, area_of_kreis: Mapping[str, str | None], entry_names: Collection[str],
        categories: Mapping[str, Sequence[str]] | None = None) -> tuple[dict[str, pd.DataFrame], dict]:
    """One integer target table per requested entry: ``ARS_kreis`` plus one column per category.

    ``counts`` is :func:`student_counts`; ``persons_by_range`` maps ``(Kreis, age range)`` to the persons
    of the census of that universe (``18_29``, ``30plus``); ``area_of_kreis`` maps every Kreis to its home
    area, ``None`` for Wolfsburg (A21: the regional ticket value). ``categories`` gives the category
    labels per entry (default :data:`STUDENT_ENTRY_CATEGORIES`, the ``kreis_attribute_control``
    constants); columns are ``<entry>_<category>``.

    With ``S_r`` the students of range ``r`` and ``N_r = persons_r - S_r``: ``student_edu =
    round(S_18_29 * e)``; ``O_k = s_k * (N_18_29 + N_30plus)`` is split over the ranges by
    ``N_r * p_r`` and rounded to ``round(O_k)`` by largest remainder; ``other_noedu_r = round(persons_r) -
    S_r - other_edu_r``; the work and ticket entries are ``round(S_18_29 * w)`` and
    ``round(S_18_29 * t(area))``, the rest of the range being ``not_student``. Every row sums to
    ``round(persons of the entry's universe)``. A negative remainder, an unknown entry name, a Kreis
    without area or a missing area share raises ``ValueError``.

    Returns ``(tables, diagnostics)``. ``diagnostics`` counts the Wolfsburg substitutions of A21, one
    counter for both quantities: ``region_value_substitutions`` is the number of (Kreis, quantity)
    cells that took the regional SrV value (the other-adult share of ``03103``, the season ticket of a
    Kreis whose area is ``None``), ``region_value_cells`` the number of cells that quantity exists for
    (one per Kreis and requested quantity), ``region_value_rate`` their ratio, and ``kreise`` the number
    of Kreise. The stage writes it into its report.
    """
    names = list(entry_names)
    labels = _entry_categories(categories)
    unknown = [name for name in names if name not in labels or name not in STUDENT_ENTRY_AGE_RANGE]
    if unknown:
        raise ValueError(f"{_LOG_TAG} unknown student entr(y/ies) {unknown}; known: "
                         f"{sorted(STUDENT_ENTRY_AGE_RANGE)}")
    students = {(str(row.ars5), row.age_range): int(row.students_count)
                for row in counts.itertuples(index=False)}
    kreise = list(dict.fromkeys(str(kreis) for kreis in counts["ars5"]))
    rows: dict[str, list[dict]] = {name: [] for name in names}
    substitutions = 0
    cells = 0
    for kreis in kreise:
        if kreis not in area_of_kreis:
            raise ValueError(f"{_LOG_TAG} Kreis {kreis} has no entry in area_of_kreis")
        persons = {r: _persons(persons_by_range, kreis, r) for r in AGE_RANGES}
        s_young = students[(kreis, AGE_RANGE_YOUNG)]
        s_old = students[(kreis, AGE_RANGE_OLD)]
        universe = {r: kac.round_count(persons[r]) for r in AGE_RANGES}
        s_of = {AGE_RANGE_YOUNG: s_young, AGE_RANGE_OLD: s_old}
        others = {r: persons[r] - s_of[r] for r in AGE_RANGES}
        for r in AGE_RANGES:
            if others[r] < 0:
                raise ValueError(f"{_LOG_TAG} Kreis {kreis}: {s_of[r]} students exceed the "
                                 f"{persons[r]:.1f} persons of age range {r} (negative remainder)")

        values: dict[str, dict[str, int]] = {}
        if ENTRY_EDUCATION_YOUNG in names or ENTRY_EDUCATION_OLD in names:
            share_k = shares.other_education_by_kreis.get(kreis)
            if share_k is None:
                raise ValueError(f"{_LOG_TAG} {OTHER_ADULTS_RELPATH}: no other-adult share for Kreis {kreis}")
            cells += 1
            if kreis == WOLFSBURG_CODE:
                substitutions += 1
            total_other = share_k * (others[AGE_RANGE_YOUNG] + others[AGE_RANGE_OLD])
            weights = [others[r] * shares.other_education_by_age_range[r] for r in AGE_RANGES]
            if sum(weights) <= 0:
                # Both ranges consist of students only (shares are in (0, 1), so a zero weight means
                # zero non-student persons), hence O_k = 0: a defined result, no other adults.
                parts = [0.0, 0.0]
            else:
                parts = [total_other * weight / sum(weights) for weight in weights]
            other_edu = dict(zip(AGE_RANGES, largest_remainder_round(parts, kac.round_count(total_other))))
            student_edu = kac.round_count(s_young * shares.education_leg)
            values[ENTRY_EDUCATION_YOUNG] = {
                "student_edu": student_edu,
                "student_noedu": s_young - student_edu,
                "other_edu": other_edu[AGE_RANGE_YOUNG],
                "other_noedu": universe[AGE_RANGE_YOUNG] - s_young - other_edu[AGE_RANGE_YOUNG]}
            values[ENTRY_EDUCATION_OLD] = {
                "student": s_old,
                "other_edu": other_edu[AGE_RANGE_OLD],
                "other_noedu": universe[AGE_RANGE_OLD] - s_old - other_edu[AGE_RANGE_OLD]}
        if ENTRY_WORK_LEG in names:
            student_work = kac.round_count(s_young * shares.work_leg)
            values[ENTRY_WORK_LEG] = {
                "student_work": student_work, "student_nowork": s_young - student_work,
                "not_student": universe[AGE_RANGE_YOUNG] - s_young}
        if ENTRY_SEASON_TICKET in names:
            area = area_of_kreis[kreis]
            cells += 1
            if area is None:
                share_t = shares.season_ticket_region
                substitutions += 1
            elif area in shares.season_ticket_by_area:
                share_t = shares.season_ticket_by_area[area]
            else:
                raise ValueError(f"{_LOG_TAG} {AREA_RELPATH}: no season_ticket/students_present/flatrate "
                                 f"row for area {area!r} (Kreis {kreis}); found "
                                 f"{sorted(shares.season_ticket_by_area)}")
            student_flatrate = kac.round_count(s_young * share_t)
            values[ENTRY_SEASON_TICKET] = {
                "student_flatrate": student_flatrate, "student_no_flatrate": s_young - student_flatrate,
                "not_student": universe[AGE_RANGE_YOUNG] - s_young}

        for name in names:
            cell = values[name]
            ordered = list(labels[name])
            if set(cell) != set(ordered):
                raise ValueError(f"{_LOG_TAG} entry {name}: categories {ordered} differ from the "
                                 f"built labels {sorted(cell)}")
            row = {_KREIS_COLUMN: kreis}
            for label in ordered:
                row[f"{name}_{label}"] = _non_negative(int(cell[label]), kreis, name, label)
            rows[name].append(row)

    rate = substitutions / cells if cells else 0.0
    diagnostics = {"kreise": len(kreise), "region_value_substitutions": substitutions,
                   "region_value_cells": cells, "region_value_rate": rate}
    logger.info("%s %d Kreise, entries %s; regional SrV value (Wolfsburg rule A21) for %d of %d "
                "(Kreis, quantity) cells = %.1f%% (other-adult share and season ticket)", _LOG_TAG,
                len(kreise), names, substitutions, cells, 100.0 * rate)
    tables = {}
    for name in names:
        columns = [_KREIS_COLUMN] + [f"{name}_{label}" for label in labels[name]]
        frame = pd.DataFrame(rows[name], columns=columns)
        frame[columns[1:]] = frame[columns[1:]].astype(np.int64)
        tables[name] = frame
    return tables, diagnostics


def student_kreis_targets(counts: pd.DataFrame, persons_by_range: Mapping[tuple[str, str], float],
                          shares: StudentShares, area_of_kreis: Mapping[str, str | None],
                          entry_names: Collection[str],
                          categories: Mapping[str, Sequence[str]] | None = None) -> dict[str, pd.DataFrame]:
    """The tables of :func:`student_kreis_targets_with_diagnostics` without its diagnostics."""
    return student_kreis_targets_with_diagnostics(counts, persons_by_range, shares, area_of_kreis,
                                                  entry_names, categories)[0]


def student_targets_long(tables: Mapping[str, pd.DataFrame],
                         persons_by_range: Mapping[tuple[str, str], float],
                         categories: Mapping[str, Sequence[str]] | None = None) -> pd.DataFrame:
    """The entry tables in long form for the fit report.

    Columns ``ars5, control, category, target_persons, universe_persons, target_share``; ``control`` is
    the entry name, ``universe_persons`` is ``round(persons of the entry's age range)`` (what the row of
    the entry sums to) and ``target_share = target_persons / universe_persons`` (NaN for an empty
    universe).
    """
    labels = _entry_categories(categories)
    rows = []
    for name, table in tables.items():
        if name not in STUDENT_ENTRY_AGE_RANGE:
            raise ValueError(f"{_LOG_TAG} unknown student entry {name!r}")
        age_range = STUDENT_ENTRY_AGE_RANGE[name]
        for record in table.to_dict("records"):
            kreis = str(record[_KREIS_COLUMN])
            universe = kac.round_count(_persons(persons_by_range, kreis, age_range))
            for label in labels[name]:
                target = int(record[f"{name}_{label}"])
                rows.append({"ars5": kreis, "control": name, "category": label,
                             "target_persons": target, "universe_persons": universe,
                             "target_share": target / universe if universe > 0 else float("nan")})
    return pd.DataFrame(rows, columns=["ars5", "control", "category", "target_persons",
                                       "universe_persons", "target_share"])
