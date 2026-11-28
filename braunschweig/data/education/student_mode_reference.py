"""The students' mode by distance band for the student in-commuters (issue #140, Phase 4, design B6).

The in-commuting students draw a fixed car or public-transport mode from the mode split of the MiD 2023
STUDENTS' education legs by distance band (``mid/mid2023_student_education_distance_mode.csv``, Phase 0
reference table R4), not from the national MiD table of work commutes that the OFF path uses
(``braunschweig.data.mikrozensus.reference.load_commute_mode_by_distance``).

The table is not a drop-in for the work-commute table: it has its own band edges and column names, so this module
has its own loader (:func:`load_student_education_mode_by_distance`) and band function
(:func:`student_mode_band`). Its bands are ROUTED distances (MiD ``wegkm_imp``, the reported leg length), so a
caller passes a routed length (the straight line times ``braunschweig.constants.ROUTED_DETOUR_FACTOR`` in the
in-commuter stage), never a straight line.

Import boundary (Phase 4): a leaf that imports ``braunschweig.data.csv_header`` and
``braunschweig.data.cordon.mode_reference`` (pure numpy/pandas) only.
"""
from __future__ import annotations

import logging
import math
import os
import re

import numpy as np
import pandas as pd

from braunschweig.data.cordon.mode_reference import restrict_to_modes

logger = logging.getLogger(__name__)

_LOG_TAG = "[student mode reference]"

#: The table relative to ``data_path`` (committed; registry record ``mid2023_student_reference``).
TABLE_RELATIVE_PATH = os.path.join("braunschweig", "mid", "mid2023_student_education_distance_mode.csv")
#: The table's own band edges (km, LEFT-inclusive) and labels, as its header line ``# Bands:`` states them; the
#: loader refuses a table whose header differs (owner of the edges: ``mid_student_reference`` of Phase 0).
BAND_EDGES_KM = (0.0, 5.0, 10.0, 25.0, 50.0, 100.0, math.inf)
BAND_LABELS = ("0_5", "5_10", "10_25", "25_50", "50_100", "100_plus")
#: The modes an in-commuter can take across the cordon (the restriction of the OFF path, ``restrict_to_modes``).
ALLOWED_MODES = ("car", "pt")
#: ASSUMPTION (not from a table): a band with fewer students with a car or public-transport education leg
#: (``n_unweighted``) than this is pooled with its neighbour. The table states the counts, not a minimum; the
#: stage key ``braunschweig.student_incommuters.mode_band_min_persons`` carries the value.
DEFAULT_MIN_BAND_PERSONS = 30

_COLUMNS = ["distance_band", "mode", "n_unweighted", "share_of_band", "share_of_all"]
_BANDS_HEADER = re.compile(r"^# Bands: km edges \[(?P<edges>[^\]]*)\], LEFT-inclusive, labels \[(?P<labels>[^\]]*)\]")
_SHARE_OF_ALL_HEADER = "share_of_all (weighted over all banded students)"


def student_mode_band(routed_km) -> str:
    """The band label of a ROUTED length in km: the table's own edges, left-inclusive (5.0 km is ``5_10``).

    Covers 0 to infinity; a negative or non-finite length raises ``ValueError``.
    """
    d = float(routed_km)
    if not (math.isfinite(d) and d >= 0.0):
        raise ValueError(f"{_LOG_TAG} a routed length must be a finite number >= 0 km, got {routed_km!r}")
    for label, upper in zip(BAND_LABELS, BAND_EDGES_KM[1:]):
        if d < upper:
            return label
    raise AssertionError("unreachable: the last edge is infinite")


def _check_header(path) -> None:
    with open(path, encoding="utf-8") as handle:
        header = [line.rstrip("\n") for line in handle if line.startswith("#")]
    bands = [match for match in (_BANDS_HEADER.match(line) for line in header) if match]
    if len(bands) != 1:
        raise ValueError(f"{_LOG_TAG} {os.path.basename(path)}: expected one header line '# Bands: km edges [...], "
                         f"LEFT-inclusive, labels [...]', found {len(bands)}")
    edges = tuple(float(value) for value in bands[0].group("edges").split(","))
    labels = tuple(value.strip().strip("'\"") for value in bands[0].group("labels").split(","))
    if edges != BAND_EDGES_KM or labels != BAND_LABELS:
        raise ValueError(f"{_LOG_TAG} {os.path.basename(path)}: band edges {edges} / labels {labels} differ from "
                         f"{BAND_EDGES_KM} / {BAND_LABELS} of student_mode_band")
    if not any(_SHARE_OF_ALL_HEADER in line for line in header):
        raise ValueError(f"{_LOG_TAG} {os.path.basename(path)}: the header no longer defines {_SHARE_OF_ALL_HEADER!r}, "
                         f"the weighted mass the thin bands are pooled with")


def pool_thin_bands(table: pd.DataFrame, min_band_persons: int) -> list[tuple[str, ...]]:
    """The groups of adjacent bands after pooling, in band order (a group of one is a band kept on its own).

    Rule: the persons of a group are the ``n_unweighted`` of its ``ALLOWED_MODES`` rows. While a group holds fewer
    than ``min_band_persons``, it merges with its lower neighbour (the first group with its upper one). The merged
    group's shares are the sums of the weighted ``share_of_all`` of its bands (see the loader).
    """
    if isinstance(min_band_persons, bool) or not isinstance(min_band_persons, (int, np.integer)) or min_band_persons < 0:
        raise ValueError(f"{_LOG_TAG} min_band_persons must be a whole number >= 0, got {min_band_persons!r}")
    kept = table[table["mode"].isin(ALLOWED_MODES)]
    persons = kept.groupby("distance_band")["n_unweighted"].sum().reindex(BAND_LABELS, fill_value=0)
    total = int(persons.sum())
    if total < min_band_persons:
        raise ValueError(f"{_LOG_TAG} all bands together hold {total} persons, fewer than min_band_persons "
                         f"{min_band_persons}; no pooling can reach it")
    groups = [[label] for label in BAND_LABELS]
    while True:
        counts = [int(persons[group].sum()) for group in groups]
        thin = [i for i, n in enumerate(counts) if n < min_band_persons]
        if not thin:
            return [tuple(group) for group in groups]
        i = thin[0]
        j = i + 1 if i == 0 else i - 1
        low, high = min(i, j), max(i, j)
        groups[low:high + 1] = [groups[low] + groups[high]]


def load_student_education_mode_by_distance(data_path, min_band_persons: int = DEFAULT_MIN_BAND_PERSONS) -> dict:
    """``{band label: {mode: probability}}`` over ``car`` and ``pt`` for every band of :data:`BAND_LABELS`.

    Read from ``<data_path>/`` :data:`TABLE_RELATIVE_PATH`. The bands are ROUTED distances
    (:func:`student_mode_band`). The header line ``# Bands:`` must state the edges of :func:`student_mode_band`.

    Pooling of thin bands (:func:`pool_thin_bands`): the table's header defines ``share_of_all`` as the weighted
    share over all banded students, so the weighted mass of a mode in a group of bands is the sum of its
    ``share_of_all`` over the group; every band of a group gets the group's distribution. The table's header states
    no minimum band size: the minimum is the caller's ``min_band_persons`` (an ASSUMPTION, see
    :data:`DEFAULT_MIN_BAND_PERSONS`). After pooling the distribution is restricted to car and public transport
    (``restrict_to_modes``, which counts and warns about a band without either mode).
    """
    path = os.path.join(str(data_path), TABLE_RELATIVE_PATH)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{_LOG_TAG} the students' mode table is missing: {path}")
    _check_header(path)
    table = pd.read_csv(path, comment="#", encoding="utf-8", dtype={"distance_band": str, "mode": str})
    missing = [column for column in _COLUMNS if column not in table.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} {os.path.basename(path)}: missing column(s) {missing}")
    unknown = sorted(set(table["distance_band"]) - set(BAND_LABELS))
    if unknown:
        raise ValueError(f"{_LOG_TAG} {os.path.basename(path)}: unknown band(s) {unknown}")
    mass = pd.to_numeric(table["share_of_all"], errors="coerce")
    if not (np.isfinite(mass) & (mass >= 0)).all():
        raise ValueError(f"{_LOG_TAG} {os.path.basename(path)}: share_of_all must be finite and >= 0")
    table = table.assign(share_of_all=mass)
    groups = pool_thin_bands(table, min_band_persons)
    reference = {}
    for group in groups:
        rows = table[table["distance_band"].isin(group)]
        by_mode = rows.groupby("mode", sort=True)["share_of_all"].sum()
        distribution = {mode: float(value) for mode, value in by_mode.items()}
        for label in group:
            reference[label] = dict(distribution)
    pooled = [group for group in groups if len(group) > 1]
    logger.info("%s %s: %d bands, pooled %s (fewer than %d car/pt persons each)", _LOG_TAG,
                os.path.basename(path), len(BAND_LABELS), pooled or "none", min_band_persons)
    for group in pooled:
        pooled_car = _car_share(reference[group[0]])
        for label in group:
            own = table[table["distance_band"] == label].groupby("mode")["share_of_all"].sum().to_dict()
            logger.info("%s band %s: car share of car and pt %.4f on its own, %.4f pooled with %s", _LOG_TAG, label,
                        _car_share(own), pooled_car, [other for other in group if other != label])
    return restrict_to_modes(reference, allowed=ALLOWED_MODES)


def _car_share(distribution: dict) -> float:
    """Car over car plus pt of a ``{mode: mass}`` mapping (NaN without either mode)."""
    car, pt = float(distribution.get("car", 0.0)), float(distribution.get("pt", 0.0))
    return car / (car + pt) if car + pt > 0 else float("nan")
