"""
Loaders for the MiD 2023 reference CSVs that replace the previously
hard-coded constraint tables.

These helpers are used by:
  * ``braunschweig.data.mid.data`` for the per-person constraint tables
    (car availability, bicycle availability, PT subscription).
  * ``braunschweig.synthesis.population.enriched`` for the H7 / H12.3
    Kreis-level vehicle-count distributions.
  * ``braunschweig.data.census.household_income`` for the H4 income-by-
    household-size matrix and the class-midpoint € lookup.

All CSVs are produced by ``scripts/seed_mid_constraint_tables.py`` and
live under ``<data_path>/braunschweig/mid/``. Running the seed script is
the supported way to regenerate them; manual edits to the CSVs are also
fine and become part of the data-provenance trail.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Subdirectory inside the synpp ``data_path`` where the seed CSVs live.
MID_SUBDIR = os.path.join("braunschweig", "mid")


def _path(data_path: str, filename: str) -> str:
    return os.path.join(data_path, MID_SUBDIR, filename)


def _read_csv(path: str, dtype: dict | None = None) -> pd.DataFrame:
    """Read a seed CSV that may have ``# ...`` comment header lines.

    ``dtype`` must be passed for key columns such as ``ars5``: pandas' int64
    inference strips leading zeros from a purely numeric key column
    irreversibly ("03101" -> 3101), and a post-hoc ``.astype(str)`` cannot
    repair it. The region-aggregate row ("Gesamt"/"03ZGB") happens to force
    object dtype today, but key padding must not depend on that accident.
    """
    return pd.read_csv(path, comment="#", dtype=dtype)


# ---------------------------------------------------------------------------
# P19 / P22 / P24.1 — long-form constraint tables
# ---------------------------------------------------------------------------

# Mapping CSV ``key`` (age dimension) → constraint dict consumed by
# bavaria/synthesis/population/enriched.py:_apply_*_constraints.
def _row_to_constraint(row: pd.Series) -> dict[str, Any]:
    dim = row["dimension"]
    target = float(row["target"])
    if dim == "zone":
        return {"zone": str(row["key"]), "target": target}
    if dim == "sex":
        return {"sex": str(row["key"]), "target": target}
    if dim == "age":
        lo = float(row["age_lo"]) if str(row["age_lo"]).strip() != "" else -math.inf
        hi = float(row["age_hi"]) if str(row["age_hi"]).strip() != "" else math.inf
        return {"age": (lo, hi), "target": target}
    raise ValueError(f"Unknown constraint dimension: {dim!r}")


def load_constraint_table(data_path: str, filename: str) -> list[dict[str, Any]]:
    """Load a P19 / P22 / P24.1 constraint CSV into the list-of-dicts form
    expected by ``braunschweig.synthesis.population.enriched``."""
    path = _path(data_path, filename)
    df = _read_csv(path)
    expected = {"dimension", "key", "age_lo", "age_hi", "target"}
    missing = expected - set(df.columns)
    if missing:
        raise RuntimeError(
            f"Constraint CSV {path} is missing columns: {sorted(missing)}"
        )
    return [_row_to_constraint(row) for _, row in df.iterrows()]


# ---------------------------------------------------------------------------
# H7 / H12.3 — Kreis × count-bucket share tables
# ---------------------------------------------------------------------------

def load_kreis_share_table(
    data_path: str, filename: str, region_key: str = "Gesamt",
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray]:
    """Load a Kreis-level share CSV (e.g. H7 cars or H12.3 bikes).

    Returns
    -------
    by_kreis
        ``{ars5: shares}`` excluding the region row.
    region
        Shares for the region-wide fallback row (``ars5 == region_key``).
    values
        Numeric count buckets (e.g. ``[0, 1, 2, 3]``) parsed from the CSV
        column headers.
    """
    df = _read_csv(_path(data_path, filename), dtype={"ars5": str})
    if "ars5" not in df.columns:
        raise RuntimeError(f"{filename}: expected 'ars5' column")
    bucket_cols = [c for c in df.columns if c != "ars5"]
    try:
        values = np.array([int(c) for c in bucket_cols], dtype=int)
    except ValueError as exc:
        raise RuntimeError(
            f"{filename}: bucket column headers must be integers, got {bucket_cols!r}"
        ) from exc

    by_kreis: dict[str, np.ndarray] = {}
    region: np.ndarray | None = None
    for _, row in df.iterrows():
        ars5 = str(row["ars5"])
        shares = np.array([float(row[c]) for c in bucket_cols], dtype=float)
        if ars5 == region_key:
            region = shares
        else:
            by_kreis[ars5] = shares
    if region is None:
        raise RuntimeError(
            f"{filename}: region row 'ars5={region_key}' is missing"
        )
    return by_kreis, region, values


# ---------------------------------------------------------------------------
# H4 — income-by-household-size matrix + class-midpoint €
# ---------------------------------------------------------------------------

H4_QUINTILE_COLUMNS = ("sehr_niedrig", "niedrig", "mittel", "hoch", "sehr_hoch")


def load_income_by_size(data_path: str) -> dict[str, tuple[float, ...]]:
    """Load the H4 ``household_size → quintile_shares`` mapping."""
    df = _read_csv(_path(data_path, "mid2023_H4_income_by_size.csv"))
    out: dict[str, tuple[float, ...]] = {}
    for _, row in df.iterrows():
        key = str(row["household_size"])
        out[key] = tuple(float(row[c]) for c in H4_QUINTILE_COLUMNS)
    return out


def load_class_midpoint_eur(data_path: str) -> dict[str, float]:
    """Load the income-class-midpoint € lookup."""
    df = _read_csv(_path(data_path, "mid2023_class_midpoint_eur.csv"))
    return {str(row["income_class"]): float(row["midpoint_eur"])
            for _, row in df.iterrows()}


# ---------------------------------------------------------------------------
# P24.1 — categorical PT ticket type (full breakdown by Kreis)
# ---------------------------------------------------------------------------

# Order is significant: matches the column layout in the MiD PDF.
# Values are ENGLISH (project language rule); the raw committed CSVs keep their
# codebook-German column headers for provenance -- P24_RAW_COLUMN_BY_CATEGORY
# below is the single translation boundary.
PT_TICKET_CATEGORIES: tuple[str, ...] = (
    "single_ticket",
    "multi_ride_ticket",
    "deutschlandticket",
    "weekly_monthly_no_subscription",
    "monthly_or_annual_subscription",
    "job_or_semester_ticket",
    "other_ticket",
    "never_pt",
    "no_answer",
)

# English category -> raw column header in the committed mid2023_P24_1*.csv files
# (MiD codebook German, PDF order). Renaming the committed columns would break
# eyeball-traceability to the survey instrument, so the files stay as transcribed
# and every loader translates HERE, nowhere else.
P24_RAW_COLUMN_BY_CATEGORY: dict[str, str] = {
    "single_ticket": "einzelfahrschein",
    "multi_ride_ticket": "mehrfachkarte",
    "deutschlandticket": "deutschlandticket",
    "weekly_monthly_no_subscription": "wochen_monat_ohne_abo",
    "monthly_or_annual_subscription": "monat_abo_jahreskarte",
    "job_or_semester_ticket": "jobticket_semesterticket",
    "other_ticket": "anderes",
    "never_pt": "fahre_nie",
    "no_answer": "keine_angabe",
}

# Subset of categories that grant unlimited rides on local PT and therefore
# map to the legacy boolean ``has_pt_subscription = True``.
# ``weekly_monthly_no_subscription`` is included because MiD asks "üblicherweise
# genutzte Fahrkarte" (typically used ticket) — an unlimited weekly/monthly pass
# without auto-renew is functionally a subscription within its validity period.
PT_TICKET_FLATRATE: frozenset[str] = frozenset({
    "deutschlandticket",
    "weekly_monthly_no_subscription",
    "monthly_or_annual_subscription",
    "job_or_semester_ticket",
})

# Categories that are logically bound to a work or study relationship (A6).
# MiD P24.1 reports the Jobticket and the Semesterticket as ONE combined column
# ("Job-/Semesterticket"), so the two cannot be separated in the survey. Both
# are obtainable only through such a relationship:
#   * the Jobticket is an employer-subsidised pass (requires employment);
#   * the Semesterticket is a student Solidarmodell pass (requires enrolment).
# Therefore the COMBINED category is gated on ``employed OR studies``: a person
# who is neither employed nor a student cannot hold a job/semester ticket and is
# zeroed out of that category before sampling. The combined-column limitation
# means we cannot enforce the stricter per-ticket rule (jobticket -> employed,
# semesterticket -> studies) separately; ``employed OR studies`` is the tightest
# defensible constraint on the data we have. Used by
# ``braunschweig.synthesis.population.enriched._condition_pt_subscription_probs``.
PT_TICKET_WORK_STUDY_BOUND: frozenset[str] = frozenset({"job_or_semester_ticket"})

# Mapping Kreis name (as it appears in the MiD PDF) → AGS-5.  ``Gesamt``
# rows are kept under the synthetic key ``"03ZGB"``.
_PT_KREIS_TO_ARS5 = {
    "Braunschweig": "03101",
    "Wolfsburg": "03103",
    "Salzgitter": "03102",
    "Landkreis Gifhorn": "03151",
    "Landkreis Peine": "03157",
    "Landkreis Helmstedt": "03154",
    "Landkreis Wolfenbüttel": "03158",
    "Landkreis Goslar": "03153",
    "Gesamt": "03ZGB",
}


def load_pt_subscription_breakdown(
    data_path: str,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Load the per-Kreis PT-ticket-type probability vectors from MiD P24.1.

    Returns
    -------
    by_kreis : dict[ars5, np.ndarray]
        Per-Kreis probability vector over ``PT_TICKET_CATEGORIES`` (sums to 1).
    region : np.ndarray
        Probability vector for the ZGB-aggregate (``Gesamt`` / ``03ZGB``
        row), provided as a regional reference. The ``03ZGB`` row is
        required; if it is absent a ``RuntimeError`` is raised (the
        previously fabricated cross-Kreis average was dead -- see note
        below).

    The MiD raw values are integer percentages that may sum to 99 or 101
    because of rounding; we normalise each Kreis vector to sum to 1.

    Note
    ----
    ``region`` is **not** consumed by the live categorical-IPF path in
    ``braunschweig.synthesis.population.enriched``: persons with no Kreis
    (or below the MiD basis age) are assigned ``never_pt``
    deterministically, not the region vector. It is retained as a
    documented regional reference (and for tests).
    """
    df = _read_csv(_path(data_path, "mid2023_P24_1.csv"), dtype={"ars5": str})
    cols = list(PT_TICKET_CATEGORIES)
    by_kreis: dict[str, np.ndarray] = {}
    region: np.ndarray | None = None
    for _, row in df.iterrows():
        ars5 = str(row["ars5"])
        vec = np.asarray(
            [float(row[P24_RAW_COLUMN_BY_CATEGORY[c]]) for c in cols], dtype=float
        )
        if vec.sum() <= 0:
            # All zeros / missing — leave as uniform fallback.
            vec = np.ones(len(cols)) / len(cols)
        else:
            vec = vec / vec.sum()
        if ars5 == "03ZGB":
            region = vec
        else:
            by_kreis[ars5] = vec
    if region is None:
        raise RuntimeError(
            "mid2023_P24_1.csv: ZGB-aggregate row 'ars5=03ZGB' (Gesamt) is "
            "missing -- it is the documented source of the region vector."
        )
    return by_kreis, region


def load_pt_subscription_margins(
    data_path: str,
) -> tuple[dict[str, np.ndarray], list[tuple[int, int, np.ndarray]]]:
    """Load the sex- and age-margin probability vectors from MiD P24.1.

    Returns
    -------
    by_sex : dict[str, np.ndarray]
        Mapping ``"male"|"female"`` -> probability vector over
        ``PT_TICKET_CATEGORIES`` (sums to 1).
    by_age : list[(age_lo, age_hi, np.ndarray)]
        One entry per age band from the MiD table (``14..17``, ``18..29``,
        …, ``80..999``).  Vector sums to 1.
    """
    cols = list(PT_TICKET_CATEGORIES)
    df_sex = _read_csv(_path(data_path, "mid2023_P24_1_by_sex.csv"))
    by_sex: dict[str, np.ndarray] = {}
    for _, row in df_sex.iterrows():
        vec = np.asarray(
            [float(row[P24_RAW_COLUMN_BY_CATEGORY[c]]) for c in cols], dtype=float
        )
        s = vec.sum()
        by_sex[str(row["sex"])] = vec / s if s > 0 else np.ones(len(cols)) / len(cols)

    df_age = _read_csv(_path(data_path, "mid2023_P24_1_by_age.csv"))
    by_age: list[tuple[int, int, np.ndarray]] = []
    for _, row in df_age.iterrows():
        vec = np.asarray(
            [float(row[P24_RAW_COLUMN_BY_CATEGORY[c]]) for c in cols], dtype=float
        )
        s = vec.sum()
        norm = vec / s if s > 0 else np.ones(len(cols)) / len(cols)
        by_age.append((int(row["age_lo"]), int(row["age_hi"]), norm))
    return by_sex, by_age


# Filename of the (optional, manually-downloaded) MiD P24.1 x Pkw-Verfuegbarkeit
# cross-tab. It is NOT seeded by ``scripts/seed_mid_constraint_tables.py`` because
# the underlying MiD regional export has not yet been obtained (see
# ``load_pt_subscription_by_car_availability``).
PT_BY_CAR_AVAILABILITY_FILENAME = "mid2023_P24_1_by_car_availability.csv"

# Car-availability categories expected as row keys in the cross-tab CSV. These
# mirror ``enriched.CAR_AVAILABILITY_CATEGORIES``; the "some" row is optional
# (MiD typically reports only Pkw "jederzeit verfuegbar" yes/no, i.e. all/none).
PT_BY_CAR_AVAILABILITY_KEYS: tuple[str, ...] = ("none", "some", "all")


def load_pt_subscription_by_car_availability(
    data_path: str,
) -> dict[str, np.ndarray] | None:
    """Load the OPTIONAL MiD P24.1 x Pkw-Verfuegbarkeit cross-tab (A6 hook).

    This wires the carless<->PT-pass correlation into the PT-subscription IPF as
    an additional margin. The required MiD regional export
    (P24.1 broken down by Pkw-Verfuegbarkeit) has NOT yet been obtained, so the
    CSV is normally absent. When it is absent this function returns ``None`` and
    logs an INFO message: this is a DOCUMENTED fallback (the carless<->PT coupling
    is simply not calibrated yet), not a silent one. When the CSV arrives, drop it
    at ``<data_path>/braunschweig/mid/mid2023_P24_1_by_car_availability.csv`` and
    the PT IPF picks up the extra margin automatically.

    Expected schema (one row per car-availability category, header order free).
    The raw CSV keeps the codebook-German column headers (see
    ``P24_RAW_COLUMN_BY_CATEGORY``); the ``PT_TICKET_CATEGORIES`` English names
    below are shown for reference only::

        car_availability,einzelfahrschein,mehrfachkarte,deutschlandticket,
        wochen_monat_ohne_abo,monat_abo_jahreskarte,jobticket_semesterticket,
        anderes,fahre_nie,keine_angabe
        none,<...9 integer percentages...>
        all,<...9 integer percentages...>

    where ``car_availability`` is one of ``PT_BY_CAR_AVAILABILITY_KEYS`` and the
    remaining 9 columns are the ``PT_TICKET_CATEGORIES`` ticket-type shares
    (integer percentages; each row is normalised to sum to 1, mirroring the
    other P24.1 loaders).

    Returns
    -------
    by_car_availability : dict[str, np.ndarray] | None
        ``{car_availability -> probability vector over PT_TICKET_CATEGORIES}``
        (each sums to 1), or ``None`` when the CSV is absent.
    """
    path = _path(data_path, PT_BY_CAR_AVAILABILITY_FILENAME)
    if not os.path.exists(path):
        logger.info(
            "MiD P24.1 x Pkw-Verfuegbarkeit cross-tab not found at %s -- the "
            "carless<->PT-subscription coupling is not yet calibrated; the PT "
            "IPF runs WITHOUT the car-availability margin (documented fallback). "
            "Place the file there to enable it.",
            path,
        )
        return None

    df = _read_csv(path)
    if "car_availability" not in df.columns:
        raise RuntimeError(
            f"{path}: expected a 'car_availability' column "
            f"(rows keyed by {sorted(PT_BY_CAR_AVAILABILITY_KEYS)})"
        )
    cols = list(PT_TICKET_CATEGORIES)
    missing = {P24_RAW_COLUMN_BY_CATEGORY[c] for c in cols} - set(df.columns)
    if missing:
        raise RuntimeError(
            f"{path}: missing PT ticket-type columns: {sorted(missing)}"
        )

    by_car_availability: dict[str, np.ndarray] = {}
    for _, row in df.iterrows():
        key = str(row["car_availability"])
        if key not in PT_BY_CAR_AVAILABILITY_KEYS:
            raise RuntimeError(
                f"{path}: unexpected car_availability key {key!r}; "
                f"expected one of {sorted(PT_BY_CAR_AVAILABILITY_KEYS)}"
            )
        vec = np.asarray(
            [float(row[P24_RAW_COLUMN_BY_CATEGORY[c]]) for c in cols], dtype=float
        )
        s = vec.sum()
        by_car_availability[key] = (
            vec / s if s > 0 else np.ones(len(cols)) / len(cols)
        )
    return by_car_availability


# ---------------------------------------------------------------------------
# MiD P17.1 — Driving licence (PKW-Führerscheinbesitz)
# ---------------------------------------------------------------------------
#
# Categories from MiD P17.1 (rows are people aged 17+; below 17 driving is
# legally impossible, so persons below the minimum-age threshold are forced
# to ``nein`` deterministically before sampling).
LICENSE_CATEGORIES: tuple[str, ...] = ("ja", "nein", "keine_angabe")

# ``has_driving_license`` is the boolean derived attribute used downstream
# (eqasim, MATSim).  We map the categorical answer onto it via:
#   ja            -> True
#   nein          -> False
#   keine_angabe  -> False  (conservative: treat unknown as no licence)
LICENSE_TRUE: frozenset[str] = frozenset({"ja"})

# Minimum legal driving age in Germany for the regular Pkw-Führerschein
# (Klasse B): 18 years.  We deliberately ignore the BF17 special case
# (begleitetes Fahren ab 17, common in Niedersachsen) because it requires
# an accompanying licensed adult and does not grant independent car use.
# MiD reports licence ownership starting at 14 to keep the denominator
# consistent across tables; we treat all responses below 18 as "nein"
# deterministically before the IPF-based assignment runs.
LICENSE_MIN_AGE: int = 18


def load_license_breakdown(
    data_path: str,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Load the per-Kreis driving-licence probability vectors from MiD P17.1.

    Mirrors :func:`load_pt_subscription_breakdown` but for the
    licence-ownership table (``ja`` / ``nein`` / ``keine_angabe``).

    Returns
    -------
    by_kreis : dict[ars5, np.ndarray]
        Per-Kreis probability vector over ``LICENSE_CATEGORIES`` (sums to 1).
    region : np.ndarray
        Probability vector for the ZGB-aggregate (``Gesamt`` / ``03ZGB``
        row), provided as a regional reference. The ``03ZGB`` row is
        required; if it is absent a ``RuntimeError`` is raised (the
        previously fabricated cross-Kreis average was dead).

    Note
    ----
    ``region`` is **not** consumed by the live licence categorical-IPF path
    in ``braunschweig.synthesis.population.enriched``: persons below
    ``LICENSE_MIN_AGE`` (or with no Kreis) are assigned ``nein``
    deterministically, not the region vector. It is retained as a
    documented regional reference (and for tests).
    """
    df = _read_csv(_path(data_path, "mid2023_P17_1.csv"), dtype={"ars5": str})
    cols = list(LICENSE_CATEGORIES)
    by_kreis: dict[str, np.ndarray] = {}
    region: np.ndarray | None = None
    for _, row in df.iterrows():
        ars5 = str(row["ars5"])
        vec = np.asarray([float(row[c]) for c in cols], dtype=float)
        if vec.sum() <= 0:
            vec = np.ones(len(cols)) / len(cols)
        else:
            vec = vec / vec.sum()
        if ars5 == "03ZGB":
            region = vec
        else:
            by_kreis[ars5] = vec
    if region is None:
        raise RuntimeError(
            "mid2023_P17_1.csv: ZGB-aggregate row 'ars5=03ZGB' (Gesamt) is "
            "missing -- it is the documented source of the region vector."
        )
    return by_kreis, region


def load_license_margins(
    data_path: str,
) -> tuple[dict[str, np.ndarray], list[tuple[int, int, np.ndarray]]]:
    """Load the sex- and age-margin probability vectors from MiD P17.1.

    Returns
    -------
    by_sex : dict[str, np.ndarray]
        Mapping ``"male"|"female"`` -> probability vector over
        ``LICENSE_CATEGORIES`` (sums to 1).
    by_age : list[(age_lo, age_hi, np.ndarray)]
        One entry per MiD age band (``14..17``, ``18..29``, …, ``80..999``);
        each vector sums to 1.
    """
    cols = list(LICENSE_CATEGORIES)
    df_sex = _read_csv(_path(data_path, "mid2023_P17_1_by_sex.csv"))
    by_sex: dict[str, np.ndarray] = {}
    for _, row in df_sex.iterrows():
        vec = np.asarray([float(row[c]) for c in cols], dtype=float)
        s = vec.sum()
        by_sex[str(row["sex"])] = vec / s if s > 0 else np.ones(len(cols)) / len(cols)

    df_age = _read_csv(_path(data_path, "mid2023_P17_1_by_age.csv"))
    by_age: list[tuple[int, int, np.ndarray]] = []
    for _, row in df_age.iterrows():
        vec = np.asarray([float(row[c]) for c in cols], dtype=float)
        s = vec.sum()
        norm = vec / s if s > 0 else np.ones(len(cols)) / len(cols)
        by_age.append((int(row["age_lo"]), int(row["age_hi"]), norm))
    return by_sex, by_age
