"""Student origin-destination table T(home Kreis, university site) for the student layer (#140, Phase 2).

This first part reads the committed CHE table (``scripts/seed_che_student_origins.py`` writes it)
into ONE cell per (origin Kreis, institution of the choice set), design B3 step 1. CHE publishes
the same statistic in two "top 10" views: per institution the ten largest origin Kreise
(``institution_origins``) and per Kreis the ten largest destination institutions
(``kreis_destinations``). A cell is the listed value of whichever view shows it; a cell shown in
both views must carry the same number in both, else the transcription is wrong and
:func:`origin_cells` raises. A cell shown in neither view is censored: its persons are unknown,
at most the smallest value of every ten-row list it is missing from (a list with fewer than ten
rows shows every cell, so it bounds its missing cells at zero).

ASSUMPTION A2: a CHE origin is the Kreis of the school-leaving certificate, NOT the residence
during the studies; the cells are an origin prior, not a residence count.

ASSUMPTION A7: distance-learning institutions (:data:`DISTANCE_LEARNING_DEFAULT`, overridable by
the caller) produce no commute to a site; their cells are dropped and counted.

The second part builds T, applies the movers step and balances the result (design B3 steps 2 to
7, :func:`build_od_table`, :func:`apply_movers`, :func:`balance_tables`). T counts HEADS: the
students of a site by the Kreis they live in, never trips on one day; no day factor enters T, the
weights or the balance. Assumptions behind it:

- ASSUMPTION A2: the CHE origin (Kreis of the school-leaving certificate) times the commuter
  weight stands in for the residence; the official residence table would replace it.
- ASSUMPTION A3: the commuter weight depends on the distance only (the curve of
  :mod:`braunschweig.data.education.student_geometry`), for every institution and both directions.
- ASSUMPTION A4: the CHE origin shares of winter semester 2022/23 hold for the LSN enrollment
  (the scale factor per institution).
- ASSUMPTION A5: a multi-site institution's origins split over its sites by site enrollment times
  the commuter weight, with weight 1 for a site in the origin Kreis itself (A6: lives at the site).
- ASSUMPTION A6: a student who does not live in the origin Kreis lives in the Kreis of the site
  (the "movers" of the own-Kreis row); so does a student with a foreign certificate.
- ASSUMPTION A7: see above; distance-learning cells never reach the builder.

Pure module (no I/O); the caller reads the CHE table with ``comment="#"`` and ``dtype=str`` so
that ``origin_ars5`` keeps its leading zeros.
"""
from __future__ import annotations

import logging
import math
import re
from typing import NamedTuple

import numpy as np
import pandas as pd

from braunschweig.data.education.student_geometry import PAIR_COLUMNS

LOGGER = logging.getLogger(__name__)

# CHE institution name -> LSN institution name (``SITE_ROWS`` of scripts/seed_lsn_university_sites.py).
INSTITUTION_CROSSWALK: dict[str, str] = {
    "Technische Universität Carolo-Wilhelmina zu Braunschweig": "TU Braunschweig",
    "Hochschule für Bildende Künste Braunschweig": "Hochsch. f. Bildende Künste Braunschweig",
    "Technische Universität Clausthal": "TU Clausthal",
    "Ostfalia Hochschule für angewandte Wissenschaften": "HS Braunschweig/Wolfenbüttel",
    "Gottfried Wilhelm Leibniz Universität Hannover": "Uni Hannover",
    "Hochschule Hannover": "HS Hannover",
    "Medizinische Hochschule Hannover (MHH)": "MHH Hannover",
    "Stiftung Tierärztliche Hochschule Hannover": "TiHo Hannover",
    "Hochschule für Musik, Theater und Medien Hannover": "Hochsch. f. Musik, Theater und Medien Hannover",
    "Georg-August-Universität Göttingen": "Uni Göttingen",
    "Stiftung Universität Hildesheim": "Uni Hildesheim",
    "Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen":
        "HS Hildesheim/Holzminden/Göttingen",
    "Leuphana Universität Lüneburg": "Uni Lüneburg",
}

# CHE institution name -> name in ``_SURROUNDING`` of scripts/seed_nds_hochschulen.py (the source of
# their coordinates). No LSN enrollment exists for them: they stay unscaled (design B3 step 5a).
OUTSIDE_LSN_CROSSWALK: dict[str, str] = {
    "Otto-von-Guericke-Universität Magdeburg": "OVGU Magdeburg",
    "Hochschule Magdeburg-Stendal": "Hochschule Magdeburg-Stendal",
    "Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)": "Hochschule Harz",
}

# ASSUMPTION A7 (see the module docstring); callers pass their own tuple to override it.
DISTANCE_LEARNING_DEFAULT: tuple[str, ...] = ("IU Internationale Hochschule in Erfurt", "FernUniversität in Hagen")

CELL_COLUMNS = ["origin_ars5", "institution", "che_persons", "source", "upper_bound_persons"]

CHE_COLUMNS = ["view", "institution", "origin_ars5", "origin_name", "rank", "row_kind", "students_persons"]
VIEW_INSTITUTION = "institution_origins"
VIEW_KREIS = "kreis_destinations"
ROW_KINDS = ("listed", "abroad", "other", "total")

SOURCE_INSTITUTION_VIEW = "institution_view"
SOURCE_KREIS_VIEW = "kreis_view"
SOURCE_BOTH_VIEWS = "both_views"
SOURCE_CENSORED = "censored"
SOURCES = (SOURCE_INSTITUTION_VIEW, SOURCE_KREIS_VIEW, SOURCE_BOTH_VIEWS, SOURCE_CENSORED)

# CHE publishes "top 10" lists: a list with this many rows hides every smaller cell.
CHE_LIST_LENGTH = 10

_ARS5 = re.compile(r"^\d{5}$")


def _validate_che(che: pd.DataFrame) -> pd.DataFrame:
    """Return a validated copy of the CHE table with integer ``students_persons``.

    Raises ValueError naming the column and the offending values for a missing column, an unknown
    view or row kind, a listed row without institution or without a five-digit ``origin_ars5``, a
    missing, non-numeric or negative count, a duplicated listed cell within one view, or a list longer
    than :data:`CHE_LIST_LENGTH`.
    """
    missing = [column for column in CHE_COLUMNS if column not in che.columns]
    if missing:
        raise ValueError(f"CHE table: missing column(s) {missing}; found {list(che.columns)}")
    frame = che.loc[:, CHE_COLUMNS].copy()

    bad_views = sorted(set(frame["view"].dropna()) - {VIEW_INSTITUTION, VIEW_KREIS}) + (
        ["<missing>"] if frame["view"].isna().any() else [])
    if bad_views:
        raise ValueError(f"CHE table: column 'view' has unknown value(s) {bad_views}")
    bad_kinds = sorted(set(frame["row_kind"].dropna()) - set(ROW_KINDS)) + (
        ["<missing>"] if frame["row_kind"].isna().any() else [])
    if bad_kinds:
        raise ValueError(f"CHE table: column 'row_kind' has unknown value(s) {bad_kinds}")

    persons = pd.to_numeric(frame["students_persons"], errors="coerce")
    bad = persons.isna() | (persons < 0) | (persons != persons.round())
    if bad.any():
        raise ValueError(f"CHE table: column 'students_persons' needs non-negative whole numbers, got "
                         f"{frame.loc[bad, 'students_persons'].tolist()[:5]} "
                         f"(institution {frame.loc[bad, 'institution'].tolist()[:5]})")
    frame["students_persons"] = persons.astype("int64")

    listed = frame["row_kind"] == "listed"
    no_institution = listed & frame["institution"].isna()
    if no_institution.any():
        raise ValueError(f"CHE table: {int(no_institution.sum())} listed row(s) without a value in column "
                         f"'institution' (origin_ars5 {frame.loc[no_institution, 'origin_ars5'].tolist()[:5]})")
    bad_code = listed & ~frame["origin_ars5"].map(lambda code: isinstance(code, str) and bool(_ARS5.match(code)))
    if bad_code.any():
        raise ValueError(f"CHE table: column 'origin_ars5' needs five-digit Kreis codes read as text "
                         f"(dtype=str), got {frame.loc[bad_code, 'origin_ars5'].tolist()[:5]}")

    keys = frame.loc[listed, ["view", "institution", "origin_ars5"]]
    duplicated = keys.duplicated(keep=False)
    if duplicated.any():
        raise ValueError(f"CHE table: duplicate listed cell(s) (view, institution, origin_ars5) "
                         f"{keys[duplicated].drop_duplicates().values.tolist()[:5]}")

    list_lengths = pd.concat([
        frame[listed & (frame["view"] == VIEW_INSTITUTION)].groupby("institution").size(),
        frame[listed & (frame["view"] == VIEW_KREIS)].groupby("origin_ars5").size()])
    too_long = list_lengths[list_lengths > CHE_LIST_LENGTH]
    if not too_long.empty:
        raise ValueError(f"CHE table: list(s) with more than {CHE_LIST_LENGTH} listed rows "
                         f"{too_long.to_dict()}; CHE publishes top-{CHE_LIST_LENGTH} lists")
    return frame


def _list_bounds(listed: pd.DataFrame, key: str) -> dict[str, int]:
    """Per list (keyed by ``key``): the upper bound of every cell the list does not show.

    A full list (:data:`CHE_LIST_LENGTH` rows) hides only cells below its smallest listed value; a
    shorter list shows every cell, so its missing cells are zero.
    """
    grouped = listed.groupby(key)["students_persons"]
    sizes, smallest = grouped.size(), grouped.min()
    return {name: int(smallest[name]) if sizes[name] >= CHE_LIST_LENGTH else 0 for name in sizes.index}


def _shown_cells(frame: pd.DataFrame) -> dict[tuple[str, str], tuple[int | None, int | None]]:
    """Every listed cell (origin_ars5, institution) -> (institution-view value, Kreis-view value).

    Raises ValueError for a cell shown in both views with different numbers (a transcription error).
    """
    listed = frame[frame["row_kind"] == "listed"]
    shown: dict[tuple[str, str], list[int | None]] = {}
    for view, position in ((VIEW_INSTITUTION, 0), (VIEW_KREIS, 1)):
        part = listed[listed["view"] == view]
        for ars5, institution, persons in zip(part["origin_ars5"], part["institution"], part["students_persons"]):
            shown.setdefault((ars5, institution), [None, None])[position] = int(persons)
    conflicts = [(cell, values) for cell, values in shown.items()
                 if None not in values and values[0] != values[1]]
    if conflicts:
        details = "; ".join(f"origin_ars5 {ars5} x {institution}: institution view {a}, Kreis view {b}"
                            for (ars5, institution), (a, b) in conflicts[:5])
        raise ValueError(f"CHE table: {len(conflicts)} cell(s) shown in both views with different values in "
                         f"column 'students_persons' ({details})")
    return {cell: (values[0], values[1]) for cell, values in shown.items()}


def _require_codes(codes: list[str], what: str) -> None:
    bad = [code for code in codes if not (isinstance(code, str) and _ARS5.match(code))]
    if bad:
        raise ValueError(f"{what}: needs five-digit Kreis codes as text, got {bad[:5]}")
    duplicated = sorted({code for code in codes if codes.count(code) > 1})
    if duplicated:
        raise ValueError(f"{what}: duplicate Kreis code(s) {duplicated}")


def _rate(count: int, total: int) -> str:
    return f"{count}/{total} ({100.0 * count / total:.1f}%)" if total else f"{count}/0"


def origin_cells(che: pd.DataFrame, institutions: list[str], origin_kreise: list[str], region_kreise: list[str],
                 distance_learning: tuple[str, ...]) -> tuple[pd.DataFrame, dict]:
    """One CHE cell per (origin Kreis, institution of the choice set), design B3 step 1.

    Parameters:
        che: the CHE table (columns :data:`CHE_COLUMNS`, read with ``dtype=str``).
        institutions: CHE names of the choice set; each must appear somewhere in the table.
        origin_kreise: five-digit codes of the origin Kreise to build cells for.
        region_kreise: five-digit codes of the region Kreise whose lists are scanned for
            institutions outside the choice set.
        distance_learning: CHE names of distance-learning institutions (ASSUMPTION A7); their cells
            are dropped and counted, they must not be in ``institutions``.

    Returns:
        ``(cells, diagnostics)``. ``cells`` has :data:`CELL_COLUMNS`: ``che_persons`` is the listed
        value (0 when censored), ``source`` one of :data:`SOURCES`, ``upper_bound_persons`` the listed
        value for a shown cell and, for a censored cell, the smallest bound of the institution's own
        list and the Kreis's list (only lists that exist count; NaN when neither exists). The
        ``diagnostics`` dict carries ``cells_per_source``, ``censored_upper_bound_persons`` (sum of the
        finite bounds), ``censored_cells_without_bound``, ``distance_learning_cells_dropped``
        ``(cells, persons)`` over ``origin_kreise``, ``shown_cells_outside_origin_kreise``
        ``(cells, persons)`` (listed cells of the choice set from Kreise not in ``origin_kreise``),
        ``listed_outside_choice_set`` (name -> persons over
        the cells of ``region_kreise`` shown in either view, neither in the choice set nor distance
        learning), ``one_view_cells_above_other_tenth`` (cells shown in one view whose value exceeds
        the bound of the other view's list: a transcription signal) and
        ``origin_kreise_without_kreis_list``.

    Raises:
        ValueError: invalid CHE table (see :func:`_validate_che`), a cell shown in both views with
            different values, a duplicated or malformed Kreis code, a duplicated choice-set
            institution, a choice-set or distance-learning institution absent from the table, or a
            distance-learning institution in the choice set.
    """
    frame = _validate_che(che)
    institutions, origin_kreise, region_kreise = list(institutions), list(origin_kreise), list(region_kreise)
    if not institutions or not origin_kreise:
        raise ValueError(f"origin_cells: needs at least one institution and one origin Kreis, got "
                         f"{len(institutions)} institution(s) and {len(origin_kreise)} Kreis code(s)")
    duplicated = sorted({name for name in institutions if institutions.count(name) > 1})
    if duplicated:
        raise ValueError(f"origin_cells: duplicate choice-set institution(s) {duplicated}")
    in_both = sorted(set(institutions) & set(distance_learning))
    if in_both:
        raise ValueError(f"origin_cells: institution(s) {in_both} are both in the choice set and distance learning")
    known = set(frame["institution"].dropna())
    unknown = [name for name in institutions if name not in known]
    if unknown:
        raise ValueError(f"origin_cells: choice-set institution(s) {unknown} appear nowhere in column "
                         f"'institution' of the CHE table")
    # A misspelt distance-learning name would drop nothing and leave its cells silently in the table.
    unknown = [name for name in distance_learning if name not in known]
    if unknown:
        raise ValueError(f"origin_cells: distance-learning institution(s) {unknown} appear nowhere in column "
                         f"'institution' of the CHE table")
    _require_codes(origin_kreise, "origin_cells origin_kreise")
    _require_codes(region_kreise, "origin_cells region_kreise")

    shown = _shown_cells(frame)
    listed = frame[frame["row_kind"] == "listed"]
    institution_bounds = _list_bounds(listed[listed["view"] == VIEW_INSTITUTION], "institution")
    kreis_bounds = _list_bounds(listed[listed["view"] == VIEW_KREIS], "origin_ars5")

    rows, signals = [], []
    for ars5 in origin_kreise:
        for institution in institutions:
            institution_value, kreis_value = shown.get((ars5, institution), (None, None))
            if institution_value is not None and kreis_value is not None:
                rows.append((ars5, institution, institution_value, SOURCE_BOTH_VIEWS, float(institution_value)))
                continue
            if institution_value is None and kreis_value is None:
                bounds = [b for b in (institution_bounds.get(institution), kreis_bounds.get(ars5)) if b is not None]
                rows.append((ars5, institution, 0, SOURCE_CENSORED, float(min(bounds)) if bounds else math.nan))
                continue
            if institution_value is not None:
                value, source, other_bound = institution_value, SOURCE_INSTITUTION_VIEW, kreis_bounds.get(ars5)
            else:
                value, source, other_bound = kreis_value, SOURCE_KREIS_VIEW, institution_bounds.get(institution)
            rows.append((ars5, institution, value, source, float(value)))
            if other_bound is not None and value > other_bound:
                signals.append({"origin_ars5": ars5, "institution": institution, "che_persons": value,
                                "source": source, "other_list_bound_persons": other_bound})

    cells = pd.DataFrame(rows, columns=CELL_COLUMNS)
    cells["che_persons"] = cells["che_persons"].astype("int64")
    cells["upper_bound_persons"] = cells["upper_bound_persons"].astype(float)

    censored = cells[cells["source"] == SOURCE_CENSORED]
    per_source = {source: int((cells["source"] == source).sum()) for source in SOURCES}
    without_bound = int(censored["upper_bound_persons"].isna().sum())

    origin_set, region_set, choice_set = set(origin_kreise), set(region_kreise), set(institutions)
    distance_set = set(distance_learning)
    dropped = [max(v for v in values if v is not None) for (ars5, institution), values in shown.items()
               if ars5 in origin_set and institution in distance_set]
    outside: dict[str, int] = {}
    for (ars5, institution), values in shown.items():
        if ars5 in region_set and institution not in choice_set and institution not in distance_set:
            outside[institution] = outside.get(institution, 0) + max(v for v in values if v is not None)
    outside = dict(sorted(outside.items(), key=lambda item: (-item[1], item[0])))
    # Listed cells of the choice set whose origin lies beyond origin_kreise (beyond the commute range):
    # they never become a cell here, so the persons are counted rather than lost silently.
    beyond = [max(v for v in values if v is not None) for (ars5, institution), values in shown.items()
              if ars5 not in origin_set and institution in choice_set]
    without_kreis_list = sorted(ars5 for ars5 in origin_kreise if ars5 not in kreis_bounds)

    diagnostics = {
        "cells_per_source": per_source,
        "censored_upper_bound_persons": int(censored["upper_bound_persons"].sum(skipna=True)),
        "censored_cells_without_bound": without_bound,
        "distance_learning_cells_dropped": (len(dropped), int(sum(dropped))),
        "shown_cells_outside_origin_kreise": (len(beyond), int(sum(beyond))),
        "listed_outside_choice_set": outside,
        "one_view_cells_above_other_tenth": signals,
        "origin_kreise_without_kreis_list": without_kreis_list,
    }

    total = len(cells)
    shown_count = total - per_source[SOURCE_CENSORED]
    LOGGER.info("[student_od_table] CHE origin cells: primary (listed) %s (institution view %d, Kreis view %d, "
                "both views %d), censored %s, upper bound %d persons, %d censored without bound",
                _rate(shown_count, total), per_source[SOURCE_INSTITUTION_VIEW], per_source[SOURCE_KREIS_VIEW],
                per_source[SOURCE_BOTH_VIEWS], _rate(per_source[SOURCE_CENSORED], total),
                diagnostics["censored_upper_bound_persons"], without_bound)
    LOGGER.info("[student_od_table] distance-learning cells dropped: %d cells, %d persons",
                *diagnostics["distance_learning_cells_dropped"])
    LOGGER.info("[student_od_table] listed choice-set cells from Kreise outside the origin Kreise (not in the "
                "table): %d cells, %d persons", *diagnostics["shown_cells_outside_origin_kreise"])
    if without_bound:
        LOGGER.warning("[student_od_table] %d censored cell(s) have neither an institution nor a Kreis list: "
                       "no upper bound", without_bound)
    if without_kreis_list:
        LOGGER.warning("[student_od_table] %d origin Kreis(e) without a CHE Kreis list: %s",
                       len(without_kreis_list), without_kreis_list)
    if outside:
        LOGGER.warning("[student_od_table] %d institution(s) listed for a region Kreis are outside the choice "
                       "set and not distance learning (%d persons): %s",
                       len(outside), sum(outside.values()), outside)
    if signals:
        LOGGER.warning("[student_od_table] %d cell(s) shown in one view exceed the bound of the other view's "
                       "list (check the transcription): %s", len(signals), signals)
    return cells, diagnostics


def che_totals(che: pd.DataFrame) -> pd.Series:
    """CHE institution -> ``total`` row of its institution-view list (persons).

    Institutions without an own list (the art academy) are absent. Raises ValueError for an invalid
    table (see :func:`_validate_che`) or unless every institution with an own list has exactly one
    total row and every total row belongs to such an institution.
    """
    frame = _validate_che(che)
    institution_view = frame[frame["view"] == VIEW_INSTITUTION]
    totals = institution_view[institution_view["row_kind"] == "total"]
    counts = totals["institution"].fillna("<missing>").value_counts()
    own_lists = set(institution_view.loc[institution_view["row_kind"] == "listed", "institution"])
    without_total = sorted(own_lists - set(counts.index))
    duplicated = sorted(counts[counts > 1].index)
    without_list = sorted(set(counts.index) - own_lists)
    if without_total or duplicated or without_list:
        raise ValueError(f"CHE table: institution view needs exactly one row_kind 'total' row per institution "
                         f"with an own list (column 'institution'); without total {without_total}, "
                         f"several totals {duplicated}, total without a list {without_list}")
    return pd.Series(totals["students_persons"].to_numpy(dtype="int64"), index=totals["institution"].to_numpy(),
                     name="che_total_persons")


# --- the builder T, the movers step, the balance (design B3 steps 2 to 7) -----------------------------------

class CommuterWeights(NamedTuple):
    """The commuter-weight triple: the weight curve's values at its three anchors (design B4).

    The anchors are :func:`braunschweig.data.education.student_geometry.curve_anchors_km`. Each value
    lies in [0, 1] and the values do not increase with distance (:func:`validate_weights`). There is
    no default: every builder call takes the triple (calibrated, or set by the owner).
    """
    short: float
    middle: float
    long: float


SCOPE_LOCAL = "local"
SCOPE_SURROUNDING = "surrounding"
SCOPES = (SCOPE_LOCAL, SCOPE_SURROUNDING)

KIND_COMMUTER = "commuter"
KIND_LOCAL = "local"
KIND_MOVER = "mover_to_braunschweig"
KIND_OUT_COMMUTER = "out_commuter"
CELL_KINDS = (KIND_COMMUTER, KIND_LOCAL, KIND_MOVER, KIND_OUT_COMMUTER)

SITE_COLUMNS = ["destination_id", "che_institution", "institution", "site_kreis_ars5", "scope", "students_persons"]
# Columns of T. The row key is (home_ars5, destination_id, cell_kind): unique, checked by build_od_table and
# apply_movers (a mover row shares home and site with the (Braunschweig, site) commuter row; cell_kind
# tells them apart).
# ``enrollment_persons`` is the site's LSN enrollment on every row of that site (NaN outside LSN), so that
# balance_tables checks the rows against an enrollment it does not derive from the rows themselves, also on
# a table read back from a file.
T_COLUMNS = ["home_ars5", "home_in_region", "destination_id", "institution", "site_kreis_ars5", "scope",
             "enrollment_persons", "distance_km", "coeff_short", "coeff_middle", "coeff_long",
             "origin_persons_scaled", "upper_bound_persons_scaled", "weight", "students_persons", "movers_persons",
             "cell_kind"]
T_ROW_KEY = ["home_ars5", "destination_id", "cell_kind"]
BALANCE_SITE_COLUMNS = ["destination_id", "institution", "site_kreis_ars5", "enrollment_persons",
                        "residents_region_persons", "in_commuters_persons", "total_persons",
                        "censored_upper_bound_persons", "censored_upper_bound_commuters_persons",
                        "censored_cells_without_bound"]
OUT_COMMUTER_COLUMNS = ["destination_id", "institution", "site_kreis_ars5", "out_commuters_persons",
                        "censored_upper_bound_commuters_persons", "censored_cells_without_bound"]
IN_COMMUTER_COLUMNS = ["home_ars5", "in_commuters_persons"]
HOME_KREIS_COLUMNS = ["home_ars5", "students_local_sites_persons", "students_out_commuting_persons",
                      "students_living_persons"]

# Float rounding only (persons), no modelling meaning: the sign checks of the builder and the
# enrollment check of the balance allow this much numerical noise.
TOLERANCE_PERSONS = 1e-6

_COEFFICIENTS = ["coeff_short", "coeff_middle", "coeff_long"]
_GEOMETRY = ["distance_km", *_COEFFICIENTS]


def validate_weights(weights: CommuterWeights) -> None:
    """Raise ValueError unless ``weights`` is a triple of numbers in [0, 1] that does not increase."""
    values = tuple(weights)
    if len(values) != 3:
        raise ValueError(f"commuter weights: need the triple (short, middle, long), got {weights!r}")
    numbers = []
    for name, value in zip(CommuterWeights._fields, values):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"commuter weight {name!r} must be a number in [0, 1], got {value!r}") from None
        if not 0.0 <= number <= 1.0:  # also rejects NaN
            raise ValueError(f"commuter weight {name!r} must lie in [0, 1], got {value!r}")
        numbers.append(number)
    if not numbers[0] >= numbers[1] >= numbers[2]:
        raise ValueError(f"commuter weights must not increase with distance (short >= middle >= long), "
                         f"got {tuple(numbers)}")


def _weight_values(short: np.ndarray, middle: np.ndarray, long: np.ndarray, weights: CommuterWeights) -> np.ndarray:
    # The one place the weight is computed: pair_weight and the builder must give bit-identical values.
    return short * float(weights[0]) + middle * float(weights[1]) + long * float(weights[2])


def pair_weight(pairs: pd.DataFrame, weights: CommuterWeights) -> pd.Series:
    """Commuter weight of every pair: ``coeff_short * short + coeff_middle * middle + coeff_long * long``.

    ``pairs`` carries the coefficient columns of :data:`PAIR_COLUMNS` (the T table carries them too).
    Raises ValueError for an invalid triple (:func:`validate_weights`) or a missing coefficient column.
    """
    validate_weights(weights)
    _require_frame_columns(pairs, _COEFFICIENTS, "pair_weight pairs")
    values = _weight_values(*(pairs[column].to_numpy(dtype=float) for column in _COEFFICIENTS), weights)
    return pd.Series(values, index=pairs.index, name="weight")


def _require_frame_columns(frame: pd.DataFrame, columns: list[str], what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what}: missing column(s) {missing}; found {list(frame.columns)}")


def _numbers(frame: pd.DataFrame, column: str, what: str, *, allow_nan: bool) -> np.ndarray:
    """Column as float array; raises for text, infinite or negative values (and NaN unless allowed)."""
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    bad = (np.isnan(values) & frame[column].notna().to_numpy()) | np.isinf(values) | (values < 0)
    if not allow_nan:
        bad |= np.isnan(values)
    if bad.any():
        raise ValueError(f"{what}: column {column!r} needs non-negative numbers, got "
                         f"{frame.loc[bad, column].tolist()[:5]}")
    return values


def _first_duplicates(values) -> list:
    series = pd.Series(list(values), dtype=object)
    return series[series.duplicated()].drop_duplicates().tolist()[:5]


def _require_unique_row_key(od: pd.DataFrame, what: str) -> None:
    duplicated = od.duplicated(T_ROW_KEY, keep=False)
    if duplicated.any():
        raise ValueError(f"{what}: duplicate row key {T_ROW_KEY} "
                         f"{od.loc[duplicated, T_ROW_KEY].drop_duplicates().values.tolist()[:5]}")


class _BuilderInputs(NamedTuple):
    """Everything of :func:`build_od_table` that does not depend on the weights (arrays K x I or K x S)."""
    kreise: np.ndarray            # (K,) origin Kreise in the order of the cells
    region_mask: np.ndarray       # (K,) home Kreis in the region
    destination: np.ndarray       # (S,) site ids
    institution_label: np.ndarray  # (S,) the sites' institution column
    site_kreis: np.ndarray        # (S,)
    scope: np.ndarray             # (S,)
    local: np.ndarray             # (S,) bool
    institutions: list            # (I,) CHE names
    institution_index: np.ndarray  # (S,) institution of each site
    multi_site: np.ndarray        # (S,) bool: the site's institution has several sites
    own_kreis_index: np.ndarray   # (S,) row of the site's Kreis in kreise, -1 if not an origin Kreis
    enrollment: np.ndarray        # (S,) E_s, NaN outside LSN
    split_enrollment: np.ndarray  # (S,) E_s for multi-site institutions, 0 otherwise
    enrollment_share: np.ndarray  # (S,) E_s / sum E of the institution (multi-site), else 1
    membership: np.ndarray        # (S, I) 1 where the site belongs to the institution
    factor: np.ndarray            # (I,) scale factor
    origin: np.ndarray            # (K, I) che_persons
    upper_bound: np.ndarray       # (K, I) upper_bound_persons
    censored: np.ndarray          # (K, I) bool
    distance_km: np.ndarray       # (K, S) NaN where no pair is given (never a row)
    coefficients: tuple           # three (K, S) arrays, 0 where no pair is given (never used)
    without_che_total: list
    without_enrollment: list


def _builder_inputs(cells: pd.DataFrame, che_totals: pd.Series, sites: pd.DataFrame, pairs: pd.DataFrame,
                    region_kreise) -> _BuilderInputs:
    """Validate the builder inputs and lay them out as arrays (everything independent of the weights)."""
    _require_frame_columns(cells, CELL_COLUMNS, "cells")
    _require_frame_columns(sites, SITE_COLUMNS, "sites")
    _require_frame_columns(pairs, PAIR_COLUMNS, "pairs")
    region = list(region_kreise)
    if not region:
        raise ValueError("build_od_table: region_kreise is empty")
    if _first_duplicates(region):
        raise ValueError(f"build_od_table region_kreise: duplicate Kreis code(s) {_first_duplicates(region)}")

    # Sites.
    destination = sites["destination_id"].to_numpy(dtype=object)
    if sites["destination_id"].isna().any() or _first_duplicates(destination):
        raise ValueError(f"sites: column 'destination_id' needs unique values, duplicates "
                         f"{_first_duplicates(destination)}, missing {int(sites['destination_id'].isna().sum())}")
    scope = sites["scope"].to_numpy(dtype=object)
    bad_scope = sorted({str(value) for value in scope if value not in SCOPES})
    if bad_scope:
        raise ValueError(f"sites: column 'scope' has unknown value(s) {bad_scope}; expected {list(SCOPES)}")
    for column in ("che_institution", "site_kreis_ars5"):
        if sites[column].isna().any():
            raise ValueError(f"sites: column {column!r} is missing for destination_id "
                             f"{sites.loc[sites[column].isna(), 'destination_id'].tolist()[:5]}")
    site_kreis = sites["site_kreis_ars5"].to_numpy(dtype=object)
    che_name = sites["che_institution"].to_numpy(dtype=object)
    enrollment = _numbers(sites, "students_persons", "sites", allow_nan=True)
    local = scope == SCOPE_LOCAL
    no_enrollment = local & np.isnan(enrollment)
    if no_enrollment.any():
        raise ValueError(f"sites: column 'students_persons' is missing for local site(s) "
                         f"{destination[no_enrollment].tolist()[:5]}; the own-Kreis row needs the enrollment")

    # Kreise: the origin Kreise of the cells, in their order.
    kreise = pd.Index(pd.unique(cells["origin_ars5"]))
    region_set = set(region)
    without_cells = [code for code in region if code not in set(kreise)]
    if without_cells:
        raise ValueError(f"build_od_table: region Kreis(e) {without_cells} have no origin cells "
                         f"(column 'origin_ars5' of cells)")
    outside = local & ~np.isin(site_kreis, region)
    inside = ~local & np.isin(site_kreis, region)
    if outside.any() or inside.any():
        raise ValueError(f"sites: column 'site_kreis_ars5' contradicts column 'scope': local site(s) outside the "
                         f"region {destination[outside].tolist()[:5]}, surrounding site(s) inside it "
                         f"{destination[inside].tolist()[:5]}")

    # Institutions and cells.
    institutions = pd.Index(pd.unique(che_name))
    extra = sorted(set(cells["institution"]) - set(institutions))
    if extra:
        raise ValueError(f"cells: column 'institution' has institution(s) {extra} without a site "
                         f"(column 'che_institution' of sites); their persons would be lost")
    keys = list(zip(cells["origin_ars5"], cells["institution"]))
    if _first_duplicates(keys):
        raise ValueError(f"cells: duplicate (origin_ars5, institution) {_first_duplicates(keys)}")
    che_persons = _numbers(cells, "che_persons", "cells", allow_nan=False)
    bounds = _numbers(cells, "upper_bound_persons", "cells", allow_nan=True)
    n_kreise, n_institutions, n_sites = len(kreise), len(institutions), len(sites)
    k_pos = kreise.get_indexer(cells["origin_ars5"])
    i_pos = institutions.get_indexer(cells["institution"])
    origin = np.full((n_kreise, n_institutions), np.nan)
    upper_bound = np.full((n_kreise, n_institutions), np.nan)
    censored = np.zeros((n_kreise, n_institutions), dtype=bool)
    origin[k_pos, i_pos] = che_persons
    upper_bound[k_pos, i_pos] = bounds
    censored[k_pos, i_pos] = (cells["source"] == SOURCE_CENSORED).to_numpy()
    if np.isnan(origin).any():
        missing = [(kreise[k], institutions[i]) for k, i in zip(*np.nonzero(np.isnan(origin)))]
        raise ValueError(f"cells: {len(missing)} (origin_ars5, institution) cell(s) missing, the table must be "
                         f"rectangular: {missing[:5]}")

    # Scale factor per institution (rule 1) and the enrollment of the split (rule 2).
    institution_index = institutions.get_indexer(che_name)
    sites_per_institution = np.bincount(institution_index, minlength=n_institutions)
    multi_site = sites_per_institution[institution_index] > 1
    nan_multi = multi_site & np.isnan(enrollment)
    if nan_multi.any():
        raise ValueError(f"sites: column 'students_persons' is missing for site(s) "
                         f"{destination[nan_multi].tolist()[:5]} of an institution with several sites; "
                         f"the split needs every site's enrollment")
    if che_totals.index.duplicated().any():
        raise ValueError(f"che_totals: duplicate institution(s) {che_totals.index[che_totals.index.duplicated()].tolist()}")
    factor = np.ones(n_institutions)
    without_che_total, without_enrollment = [], []
    for i, name in enumerate(institutions):
        site_enrollment = enrollment[institution_index == i]
        lacks_enrollment, lacks_total = bool(np.isnan(site_enrollment).any()), name not in che_totals.index
        if lacks_enrollment:
            without_enrollment.append(name)
        if lacks_total:
            without_che_total.append(name)
        if lacks_enrollment or lacks_total:
            continue
        total = float(che_totals[name])
        if not (math.isfinite(total) and total > 0):
            raise ValueError(f"che_totals: institution {name!r} needs a positive total, got {che_totals[name]!r}")
        factor[i] = site_enrollment.sum() / total
    split_enrollment = np.where(multi_site, enrollment, 0.0)
    enrollment_sum = np.bincount(institution_index, weights=split_enrollment, minlength=n_institutions)
    empty = (sites_per_institution > 1) & ~(enrollment_sum > 0)
    if empty.any():
        raise ValueError(f"sites: column 'students_persons' sums to 0 for institution(s) "
                         f"{list(institutions[empty])} with several sites; the split is undefined")
    enrollment_share = np.where(multi_site, split_enrollment / np.where(multi_site, enrollment_sum[institution_index], 1.0),
                                1.0)
    membership = np.zeros((n_sites, n_institutions))
    membership[np.arange(n_sites), institution_index] = 1.0

    # Pair geometry (K x S). Needed: every row, and every pair a multi-site split reads.
    site_index = pd.Index(destination)
    pk = kreise.get_indexer(pairs["ars5"])
    ps = site_index.get_indexer(pairs["destination_id"])
    keep = (pk >= 0) & (ps >= 0)
    flat = pk[keep] * n_sites + ps[keep]
    counts = np.bincount(flat, minlength=n_kreise * n_sites).reshape(n_kreise, n_sites)
    region_mask = np.asarray(kreise.isin(region_set))
    if (counts > 1).any():
        duplicated = [(kreise[k], destination[s]) for k, s in zip(*np.nonzero(counts > 1))]
        raise ValueError(f"pairs: duplicate (ars5, destination_id) {duplicated[:5]}")
    needed = local[None, :] | region_mask[:, None] | multi_site[None, :]
    missing = needed & (counts == 0)
    if missing.any():
        pairs_missing = [(kreise[k], destination[s]) for k, s in zip(*np.nonzero(missing))]
        raise ValueError(f"pairs: {len(pairs_missing)} (ars5, destination_id) pair(s) missing from the coefficients "
                         f"(PAIR_COLUMNS): {pairs_missing[:5]}")
    kept = pairs.loc[keep]
    geometry = {column: _numbers(kept, column, "pairs", allow_nan=False) for column in _GEOMETRY}
    distance_km = np.full(n_kreise * n_sites, np.nan)
    distance_km[flat] = geometry["distance_km"]
    coefficients = []
    for column in _COEFFICIENTS:
        array = np.zeros(n_kreise * n_sites)
        array[flat] = geometry[column]
        coefficients.append(array.reshape(n_kreise, n_sites))

    return _BuilderInputs(
        kreise=kreise.to_numpy(dtype=object), region_mask=region_mask, destination=destination,
        institution_label=sites["institution"].to_numpy(dtype=object), site_kreis=site_kreis, scope=scope,
        local=local, institutions=list(institutions), institution_index=institution_index, multi_site=multi_site,
        own_kreis_index=kreise.get_indexer(site_kreis), enrollment=enrollment, split_enrollment=split_enrollment,
        enrollment_share=enrollment_share, membership=membership, factor=factor, origin=origin,
        upper_bound=upper_bound, censored=censored, distance_km=distance_km.reshape(n_kreise, n_sites),
        coefficients=tuple(coefficients), without_che_total=without_che_total, without_enrollment=without_enrollment)


def build_od_table(cells: pd.DataFrame, che_totals: pd.Series, sites: pd.DataFrame, pairs: pd.DataFrame,
                   region_kreise, weights: CommuterWeights) -> tuple[pd.DataFrame, dict]:
    """The student table T(home Kreis, site): head counts by the Kreis the students live in (design B3).

    Parameters:
        cells: :func:`origin_cells` output (:data:`CELL_COLUMNS`), one cell per (origin Kreis, CHE
            institution), rectangular; its origin Kreise are the home Kreise of the local sites' rows.
        che_totals: :func:`che_totals` output (CHE name -> total persons).
        sites: one row per site, :data:`SITE_COLUMNS` (``students_persons`` = LSN enrollment ``E_s``,
            NaN for the institutions outside LSN, which must have a single surrounding site).
        pairs: :func:`braunschweig.data.education.student_geometry.pair_coefficients` output
            (:data:`PAIR_COLUMNS`); the maximum commute distance is inside its coefficients.
        region_kreise: the region's Kreis codes (all must be origin Kreise of ``cells``).
        weights: the commuter-weight triple; ``w(k, s)`` is :func:`pair_weight` of the pair.

    Rules, in this order:
        1. scale factor per institution = its sites' summed enrollment / its CHE total (A4); without a
           CHE total (the art academy) or without an enrollment (outside LSN) the factor is 1, counted;
        2. an institution with several sites (any scope) splits each origin count over all its sites,
           ``O(k, s) = O(k, i) E_s v(k, s) / sum_s' E_s' v(k, s')`` with ``v = 1`` for a site in Kreis
           ``k`` and ``w(k, s)`` otherwise (A5); where every ``v`` is 0 (an origin beyond reach of all
           sites; its commuter rows are 0 either way) by ``E_s`` alone, counted. A censored cell's
           upper bound passes through the same scale and split;
        3. local site ``j``, home Kreis ``k`` other than the site's: ``T = O(k, j) w(k, j)``, ``commuter``;
        4. local site, own Kreis: ``T = E_j - sum of the site's commuter rows``, ``local``, weight 1;
           ``movers_persons = T - O(own Kreis, j)`` (A6; a censored own-Kreis cell counts 0, counted);
        5. surrounding site ``s``, region Kreis ``k``: ``T = O(k, s) w(k, s)``, ``out_commuter``; no other
           rows for surrounding sites.
    The table is rectangular: one row per (origin Kreis, local site) and per (region Kreis,
    surrounding site), zeros included. ``movers_persons`` is 0 on commuter and out-commuter rows (they
    live in their origin Kreis by construction). A local or mover count in (-:data:`TOLERANCE_PERSONS`,
    0) is float noise and set to exactly 0. ``enrollment_persons`` repeats ``E_s`` on every row of the
    site. Everything independent of the weights is laid out once per call; the calibration calls the
    builder once per weight triple.

    Returns:
        ``(od, diagnostics)``: ``od`` has :data:`T_COLUMNS`; ``diagnostics`` carries ``scale_factors``
        (CHE name -> factor), ``institutions_without_che_total`` and ``institutions_without_enrollment``
        (unscaled), ``multi_site_split_cells`` (``by_weight`` / ``by_enrollment_only`` over the
        (origin Kreis, multi-site institution) splits), ``local_sites_with_censored_own_kreis_cell``
        and ``rows_per_cell_kind``.

    Raises:
        ValueError: an invalid triple, a missing column, a duplicated key, a missing cell or pair, a
            local site without enrollment, a scope contradicting the region, a multi-site institution
            with a missing or zero enrollment, a non-positive CHE total, or a negative local count or
            mover count (more commuters than enrollment, or more own-Kreis origins than local students).
    """
    validate_weights(weights)
    base = _builder_inputs(cells, che_totals, sites, pairs, region_kreise)
    n_kreise, n_sites = base.distance_km.shape
    site_columns = np.arange(n_sites)

    weight = _weight_values(*base.coefficients, weights)                       # (K, S) w(k, s)
    has_own = base.own_kreis_index >= 0
    v = weight.copy()
    v[base.own_kreis_index[has_own], site_columns[has_own]] = 1.0              # A5/A6: lives at the site
    numerator = v * base.split_enrollment[None, :]
    denominator = numerator @ base.membership                                  # (K, I)
    denominator_site = denominator[:, base.institution_index]
    by_enrollment = denominator_site <= 0
    split = np.where(by_enrollment, base.enrollment_share[None, :],
                     numerator / np.where(by_enrollment, 1.0, denominator_site))
    share = np.where(base.multi_site[None, :], split, 1.0)
    multi_institutions = np.bincount(base.institution_index, minlength=len(base.institutions)) > 1
    n_split = int(n_kreise * multi_institutions.sum())
    n_split_by_enrollment = int((denominator[:, multi_institutions] <= 0).sum())

    factor_site = base.factor[base.institution_index][None, :]
    origin = base.origin[:, base.institution_index] * factor_site * share      # (K, S) O(k, s)
    upper_bound = base.upper_bound[:, base.institution_index] * factor_site * share
    students = origin * weight

    local_columns = site_columns[base.local]
    own_rows = base.own_kreis_index[local_columns]
    commuters = students[:, local_columns].copy()
    commuters[own_rows, np.arange(len(local_columns))] = 0.0
    commuter_sum = commuters.sum(axis=0)
    def checked(values: np.ndarray, column: str) -> np.ndarray:
        # Below -TOLERANCE_PERSONS raises; the float noise in (-TOLERANCE_PERSONS, 0) becomes exactly 0.
        negative = values < -TOLERANCE_PERSONS
        if negative.any():
            details = [(base.destination[j], round(float(value), 6), float(base.enrollment[j]),
                        round(float(commuter_sum[n]), 6))
                       for n, (j, value) in enumerate(zip(local_columns, values)) if negative[n]]
            raise ValueError(f"build_od_table: negative {column!r} on the own-Kreis row of local site(s) "
                             f"(destination_id, value, enrollment, commuters) {details[:5]} with weights "
                             f"{tuple(weights)}: more commuters than enrollment, or more own-Kreis origins than "
                             f"students living at the site")
        return np.maximum(values, 0.0)

    local_students = checked(base.enrollment[local_columns] - commuter_sum, "students_persons")
    movers = checked(local_students - origin[own_rows, local_columns], "movers_persons")
    students[own_rows, local_columns] = local_students
    weight_column = weight.copy()
    weight_column[own_rows, local_columns] = 1.0
    movers_column = np.zeros((n_kreise, n_sites))
    movers_column[own_rows, local_columns] = movers

    rows = base.local[None, :] | base.region_mask[:, None]
    s_idx, k_idx = np.nonzero(rows.T)                                          # site-major, Kreise in cell order
    own = base.own_kreis_index[s_idx] == k_idx
    kind = np.where(~base.local[s_idx], KIND_OUT_COMMUTER, np.where(own, KIND_LOCAL, KIND_COMMUTER))
    od = pd.DataFrame({
        "home_ars5": base.kreise[k_idx], "home_in_region": base.region_mask[k_idx],
        "destination_id": base.destination[s_idx], "institution": base.institution_label[s_idx],
        "site_kreis_ars5": base.site_kreis[s_idx], "scope": base.scope[s_idx],
        "enrollment_persons": base.enrollment[s_idx],
        "distance_km": base.distance_km[k_idx, s_idx], "coeff_short": base.coefficients[0][k_idx, s_idx],
        "coeff_middle": base.coefficients[1][k_idx, s_idx], "coeff_long": base.coefficients[2][k_idx, s_idx],
        "origin_persons_scaled": origin[k_idx, s_idx], "upper_bound_persons_scaled": upper_bound[k_idx, s_idx],
        "weight": weight_column[k_idx, s_idx], "students_persons": students[k_idx, s_idx],
        "movers_persons": movers_column[k_idx, s_idx], "cell_kind": kind.astype(object)}, columns=T_COLUMNS)
    _require_unique_row_key(od, "build_od_table")

    censored_own = [base.destination[j] for j, k in zip(local_columns, own_rows)
                    if base.censored[k, base.institution_index[j]]]
    unscaled = sorted(set(base.without_che_total) | set(base.without_enrollment))
    diagnostics = {
        "scale_factors": {name: float(base.factor[i]) for i, name in enumerate(base.institutions)},
        "institutions_without_che_total": list(base.without_che_total),
        "institutions_without_enrollment": list(base.without_enrollment),
        "multi_site_split_cells": {"by_weight": n_split - n_split_by_enrollment,
                                   "by_enrollment_only": n_split_by_enrollment},
        "local_sites_with_censored_own_kreis_cell": censored_own,
        "rows_per_cell_kind": {name: int((kind == name).sum()) for name in CELL_KINDS},
    }
    n_institutions = len(base.institutions)
    LOGGER.info("[student_od_table] T with weights %s: %d rows %s; institutions scaled (primary) %s, unscaled %s "
                "(no CHE total %s, no enrollment %s); multi-site splits by weight %s, by enrollment only %s; "
                "local sites with a censored own-Kreis cell %s %s",
                tuple(weights), len(od), diagnostics["rows_per_cell_kind"],
                _rate(n_institutions - len(unscaled), n_institutions), _rate(len(unscaled), n_institutions),
                base.without_che_total, base.without_enrollment, _rate(n_split - n_split_by_enrollment, n_split),
                _rate(n_split_by_enrollment, n_split), _rate(len(censored_own), len(local_columns)), censored_own)
    return od, diagnostics


def apply_movers(od: pd.DataFrame, share: float, site_kreise: tuple[str, ...], braunschweig_kreis: str) -> pd.DataFrame:
    """Design B3 step 7: move a share of the movers of the sites in ``site_kreise`` to Braunschweig.

    For every local row (own-Kreis row of a local site) whose site Kreis is in ``site_kreise``,
    ``share * movers_persons`` leaves the row for a new ``mover_to_braunschweig`` row with home
    ``braunschweig_kreis`` and the distance and coefficients of the (Braunschweig, site) commuter row;
    its ``weight``, ``origin_persons_scaled`` and ``upper_bound_persons_scaled`` are NaN (not a CHE
    cell). ``movers_persons`` moves along with the persons, so its sum per site is unchanged, as is
    every site total. ``share == 0`` returns the table unchanged.

    ASSUMPTION (design B3 step 7): the movers of the listed site Kreise who live in another Kreis
    live in Braunschweig; the share is a calibrated or owner-set parameter, never a default.

    Raises:
        ValueError: ``share`` outside [0, 1], ``braunschweig_kreis`` in ``site_kreise``, a missing
            column, a table that already holds mover rows, a site Kreis without a local row, or a
            site without a (Braunschweig, site) commuter row.
    """
    try:
        share_value = float(share)
    except (TypeError, ValueError):
        raise ValueError(f"apply_movers: mover share must be a number in [0, 1], got {share!r}") from None
    if not 0.0 <= share_value <= 1.0:
        raise ValueError(f"apply_movers: mover share must lie in [0, 1], got {share!r}")
    site_kreise = tuple(site_kreise)
    if braunschweig_kreis in site_kreise:
        raise ValueError(f"apply_movers: braunschweig_kreis {braunschweig_kreis!r} is in site_kreise {site_kreise}; "
                         f"movers move from another site Kreis to Braunschweig")
    _require_frame_columns(od, T_COLUMNS, "apply_movers od")
    if (od["cell_kind"] == KIND_MOVER).any():
        raise ValueError("apply_movers: the table already holds mover_to_braunschweig rows (column 'cell_kind'); "
                         "apply the movers once, to the table of build_od_table")
    is_local = od["cell_kind"] == KIND_LOCAL
    without_site = sorted(set(site_kreise) - set(od.loc[is_local, "site_kreis_ars5"]))
    if without_site:
        raise ValueError(f"apply_movers: site_kreise {without_site} have no local site (column 'site_kreis_ars5')")
    if share_value == 0.0:
        LOGGER.info("[student_od_table] movers: share 0, table unchanged")
        return od.copy()

    selected = is_local & od["site_kreis_ars5"].isin(site_kreise)
    local_rows = od.loc[selected]
    commuter = od[(od["home_ars5"] == braunschweig_kreis) & (od["cell_kind"] == KIND_COMMUTER)]
    commuter = commuter.set_index("destination_id")
    lacking = sorted(set(local_rows["destination_id"]) - set(commuter.index))
    if lacking:
        raise ValueError(f"apply_movers: no commuter row from {braunschweig_kreis!r} (column 'home_ars5') to "
                         f"site(s) {lacking}")
    moved = share_value * local_rows["movers_persons"].to_numpy(dtype=float)
    source = commuter.loc[local_rows["destination_id"]]
    new_rows = pd.DataFrame({
        "home_ars5": braunschweig_kreis, "home_in_region": source["home_in_region"].to_numpy(dtype=bool),
        "destination_id": local_rows["destination_id"].to_numpy(), "institution": local_rows["institution"].to_numpy(),
        "site_kreis_ars5": local_rows["site_kreis_ars5"].to_numpy(), "scope": local_rows["scope"].to_numpy(),
        "enrollment_persons": local_rows["enrollment_persons"].to_numpy(dtype=float),
        **{column: source[column].to_numpy(dtype=float) for column in _GEOMETRY},
        "origin_persons_scaled": np.nan, "upper_bound_persons_scaled": np.nan, "weight": np.nan,
        "students_persons": moved, "movers_persons": moved, "cell_kind": KIND_MOVER}, columns=T_COLUMNS)

    result = od.copy()
    result.loc[selected, "students_persons"] = local_rows["students_persons"].to_numpy(dtype=float) - moved
    result.loc[selected, "movers_persons"] = local_rows["movers_persons"].to_numpy(dtype=float) - moved
    result = pd.concat([result, new_rows], ignore_index=True)[T_COLUMNS]
    _require_unique_row_key(result, "apply_movers")
    LOGGER.info("[student_od_table] movers: share %g of %.1f movers at %d local site(s) in %s moved to %s "
                "(%.1f persons)", share_value, float(local_rows["movers_persons"].sum()), len(local_rows),
                list(site_kreise), braunschweig_kreis, float(moved.sum()))
    return result


def balance_tables(od: pd.DataFrame, region_kreise) -> dict[str, pd.DataFrame]:
    """The balance of T (design section 5a), head counts, no day factor.

    Keys:
        ``by_site`` (:data:`BALANCE_SITE_COLUMNS`, local sites): ``enrollment_persons`` from the column
            of the same name (one value per site); residents = rows with home in ``region_kreise``,
            in-commuters the others; ``censored_upper_bound_persons`` = scaled bounds of the site's
            censored origin cells (``upper_bound_persons_scaled - origin_persons_scaled``, 0 for a shown
            cell; a NaN bound counts 0); ``censored_upper_bound_commuters_persons`` = the same bound times
            the weight, summed over the commuter rows; ``censored_cells_without_bound`` = the site's rows
            (origin cells after the split, mover rows excluded) whose bound is NaN, i.e. censored cells
            that no CHE list bounds.
        ``out_commuters`` (:data:`OUT_COMMUTER_COLUMNS`, surrounding sites; no enrollment check: only the
            region's rows exist).
        ``in_commuters_by_origin`` (:data:`IN_COMMUTER_COLUMNS`): local-site students per home Kreis
            outside the region.
        ``by_home_kreis`` (:data:`HOME_KREIS_COLUMNS`, one row per region Kreis): students at local
            sites, out-commuting students and their sum, the students living in the Kreis.

    Raises:
        ValueError: a missing column, a local site whose ``enrollment_persons`` is NaN or differs between
            its rows, ``home_in_region`` contradicting ``region_kreise``, a region Kreis without rows, or
            ``total_persons`` differing from ``enrollment_persons`` by more than
            :data:`TOLERANCE_PERSONS` at a local site.
    """
    _require_frame_columns(od, T_COLUMNS, "balance_tables od")
    region = list(region_kreise)
    if _first_duplicates(region):
        raise ValueError(f"balance_tables region_kreise: duplicate Kreis code(s) {_first_duplicates(region)}")
    local = (od["scope"] == SCOPE_LOCAL).to_numpy()
    site_enrollment = pd.to_numeric(od.loc[local, "enrollment_persons"], errors="coerce")
    without = sorted(set(od.loc[local, "destination_id"][site_enrollment.isna()]))
    if without:
        raise ValueError(f"balance_tables: column 'enrollment_persons' is missing for local site(s) {without[:5]}")
    enrollment = site_enrollment.groupby(od.loc[local, "destination_id"], sort=False)
    differing = enrollment.nunique()
    if (differing > 1).any():
        raise ValueError(f"balance_tables: column 'enrollment_persons' differs between the rows of local site(s) "
                         f"{differing[differing > 1].index.tolist()[:5]}")
    enrollment = enrollment.first()
    in_region = od["home_ars5"].isin(region).to_numpy()
    contradicting = in_region != od["home_in_region"].to_numpy(dtype=bool)
    if contradicting.any():
        raise ValueError(f"balance_tables: column 'home_in_region' contradicts region_kreise for home Kreis(e) "
                         f"{sorted(set(od.loc[contradicting, 'home_ars5']))[:5]}")
    absent = [code for code in region if code not in set(od["home_ars5"])]
    if absent:
        raise ValueError(f"balance_tables: region Kreis(e) {absent} have no row (column 'home_ars5')")
    if (~local & ~in_region).any():
        raise ValueError("balance_tables: surrounding-site row(s) with a home outside the region; T holds only "
                         "the region's out-commuters")

    students = od["students_persons"].to_numpy(dtype=float)
    bound = (od["upper_bound_persons_scaled"] - od["origin_persons_scaled"]).to_numpy(dtype=float)
    commuting = od["cell_kind"].isin([KIND_COMMUTER, KIND_OUT_COMMUTER]).to_numpy()
    frame = pd.DataFrame({
        "home_ars5": od["home_ars5"].to_numpy(), "destination_id": od["destination_id"].to_numpy(),
        "institution": od["institution"].to_numpy(), "site_kreis_ars5": od["site_kreis_ars5"].to_numpy(),
        "students": students, "residents": np.where(in_region, students, 0.0),
        "in_commuters": np.where(in_region, 0.0, students), "bound": np.nan_to_num(bound, nan=0.0),
        "commuter_bound": np.where(commuting, np.nan_to_num(bound * od["weight"].to_numpy(dtype=float), nan=0.0), 0.0),
        "without_bound": (od["upper_bound_persons_scaled"].isna() & (od["cell_kind"] != KIND_MOVER)).to_numpy(int)})

    by_site = frame[local].groupby("destination_id", sort=False).agg(
        institution=("institution", "first"), site_kreis_ars5=("site_kreis_ars5", "first"),
        residents_region_persons=("residents", "sum"), in_commuters_persons=("in_commuters", "sum"),
        censored_upper_bound_persons=("bound", "sum"),
        censored_upper_bound_commuters_persons=("commuter_bound", "sum"),
        censored_cells_without_bound=("without_bound", "sum")).reset_index()
    by_site["enrollment_persons"] = by_site["destination_id"].map(enrollment).astype(float)
    by_site["total_persons"] = by_site["residents_region_persons"] + by_site["in_commuters_persons"]
    off = (by_site["total_persons"] - by_site["enrollment_persons"]).abs() > TOLERANCE_PERSONS
    if off.any():
        raise ValueError(f"balance_tables: column 'total_persons' differs from 'enrollment_persons' at site(s) "
                         f"{by_site.loc[off, ['destination_id', 'total_persons', 'enrollment_persons']].values.tolist()[:5]}")
    by_site = by_site[BALANCE_SITE_COLUMNS]

    out_commuters = frame[~local].groupby("destination_id", sort=False).agg(
        institution=("institution", "first"), site_kreis_ars5=("site_kreis_ars5", "first"),
        out_commuters_persons=("students", "sum"),
        censored_upper_bound_commuters_persons=("commuter_bound", "sum"),
        censored_cells_without_bound=("without_bound", "sum")).reset_index()
    out_commuters = out_commuters.reindex(columns=OUT_COMMUTER_COLUMNS)

    in_commuters = (frame[local & ~in_region].groupby("home_ars5", sort=False)["students"].sum()
                    .rename("in_commuters_persons").rename_axis("home_ars5").reset_index())

    at_local_sites = frame[local].groupby("home_ars5")["students"].sum().reindex(region, fill_value=0.0)
    out_commuting = frame[~local].groupby("home_ars5")["students"].sum().reindex(region, fill_value=0.0)
    by_home_kreis = pd.DataFrame({"home_ars5": region,
                                  "students_local_sites_persons": at_local_sites.to_numpy(dtype=float),
                                  "students_out_commuting_persons": out_commuting.to_numpy(dtype=float)})
    by_home_kreis["students_living_persons"] = (by_home_kreis["students_local_sites_persons"]
                                                + by_home_kreis["students_out_commuting_persons"])

    LOGGER.info("[student_od_table] balance: %d local site(s), enrollment %.1f = residents %.1f + in-commuters %.1f; "
                "out-commuters %.1f to %d surrounding site(s); censored bound %.1f persons, of which %.1f commuting; "
                "%d censored cell(s) without a bound (%d at local sites, %d at surrounding sites)",
                len(by_site), float(by_site["enrollment_persons"].sum()),
                float(by_site["residents_region_persons"].sum()), float(by_site["in_commuters_persons"].sum()),
                float(out_commuters["out_commuters_persons"].sum()), len(out_commuters),
                float(by_site["censored_upper_bound_persons"].sum()),
                float(by_site["censored_upper_bound_commuters_persons"].sum()), int(frame["without_bound"].sum()),
                int(by_site["censored_cells_without_bound"].sum()),
                int(out_commuters["censored_cells_without_bound"].sum()))
    return {"by_site": by_site, "out_commuters": out_commuters, "in_commuters_by_origin": in_commuters,
            "by_home_kreis": by_home_kreis}
