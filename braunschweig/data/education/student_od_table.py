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

Pure module (no I/O); the caller reads the CHE table with ``comment="#"`` and ``dtype=str`` so
that ``origin_ars5`` keeps its leading zeros.
"""
from __future__ import annotations

import logging
import math
import re

import pandas as pd

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
        ``(cells, persons)`` over ``origin_kreise``, ``listed_outside_choice_set`` (name -> persons over
        the cells of ``region_kreise`` shown in either view, neither in the choice set nor distance
        learning), ``one_view_cells_above_other_tenth`` (cells shown in one view whose value exceeds
        the bound of the other view's list: a transcription signal) and
        ``origin_kreise_without_kreis_list``.

    Raises:
        ValueError: invalid CHE table (see :func:`_validate_che`), a cell shown in both views with
            different values, a duplicated or malformed Kreis code, a duplicated choice-set
            institution, an institution absent from the table, or a distance-learning institution in
            the choice set.
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
    without_kreis_list = sorted(ars5 for ars5 in origin_kreise if ars5 not in kreis_bounds)

    diagnostics = {
        "cells_per_source": per_source,
        "censored_upper_bound_persons": int(censored["upper_bound_persons"].sum(skipna=True)),
        "censored_cells_without_bound": without_bound,
        "distance_learning_cells_dropped": (len(dropped), int(sum(dropped))),
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
    table (see :func:`_validate_che`) or an institution with more than one total row.
    """
    frame = _validate_che(che)
    totals = frame[(frame["view"] == VIEW_INSTITUTION) & (frame["row_kind"] == "total")]
    duplicated = totals["institution"][totals["institution"].duplicated()].tolist()
    if duplicated or totals["institution"].isna().any():
        raise ValueError(f"CHE table: institution-view total rows need one per institution in column "
                         f"'institution', got duplicates {duplicated} and "
                         f"{int(totals['institution'].isna().sum())} without a name")
    return pd.Series(totals["students_persons"].to_numpy(dtype="int64"), index=totals["institution"].to_numpy(),
                     name="che_total_persons")
