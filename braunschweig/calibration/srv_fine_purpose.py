"""SrV 2023 fine-purpose reference: within-coarse shares and distances per ``V_ZWECK`` code.

Builds the committed aggregate table ``srv2023_fine_purpose_reference.csv`` from the LOCAL-ONLY
SrV 2023 "Braunschweig und RGB" scientific-use trip microdata: for every FINE purpose code the
model's coarse purposes are built from (shop 8/9, other errand 10/11, leisure 13-18), the
GEWICHT_W_ZENSUS-weighted share WITHIN its coarse purpose and the weighted GIS-distance
percentiles / median trip duration.

Role (issue #242, sub-project C "purpose correctness"): a MEASUREMENT REFERENCE for the MiD
``W_ZWD`` subtype split of :mod:`braunschweig.popsim.purpose_subtype` and
:mod:`braunschweig.popsim.shop_subtype`. It is NOT a control target and NOT a validated
calibration target: no synthesis, location or distribution stage reads it, and nothing in this
package re-estimates a subtype model from it. It exists so that the regional (SrV) fine-purpose
mix can be compared with the national (MiD) subtype mix the model actually estimates on, and so
that a large divergence becomes visible instead of staying implicit.

Universe: every delivered SrV trip whose ``V_ZWECK`` is a MEASURED fine code
(:data:`COARSE_BY_FINE`). Trips of the other delivered purposes (:data:`OUT_OF_SCOPE_V_ZWECK`:
work, education, escort, home, "Sonstiges" and the "Unplausibel" code) are dropped with a logged
rate; a ``V_ZWECK`` in NEITHER map raises, so a future delivery that adds a purpose code cannot
land in a silent bucket (the same guard idea as
:func:`braunschweig.popsim.purpose_subtype.code_coverage_guard`).

Weights: ``GEWICHT_W_ZENSUS`` (expansion to Zensus 2022 counts). The stratum-internal
``GEWICHT_W`` must NOT be used across strata (ADR-0055) -- the same convention as
:mod:`braunschweig.calibration.srv_plan_structure` and
:mod:`braunschweig.calibration.srv_distance_targets`.

Pure module: no file I/O. ``scripts/extract_srv_fine_purpose_reference.py`` owns the raw reading,
the CLI and the provenance header.

Deviation from the task brief's sketch signature (documented deliberately):
:func:`build_fine_purpose_reference` returns ``(table, diagnostics)`` rather than the table
alone, so the exclusion counts can be written INTO the committed file's provenance header
instead of living only in a run log -- the convention ``scripts/extract_srv_absence.py``
establishes.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_distance_targets import weighted_quantiles

logger = logging.getLogger(__name__)

_LOG_TAG = "[srv fine purpose]"

FINE_PURPOSE_TABLE = "srv2023_fine_purpose_reference.csv"

# --------------------------------------------------------------------------- SrV code maps
# Labels transcribed from the SrV 2023 codebook SrV2023_Datenkodierung_SciUse.xlsx, sheet
# "Tabelle1", variable V_ZWECK ("Ziel/Zweck"), cells B8236-B8256 -- the codebook is the ONLY
# source for these strings. German umlauts are transliterated (ae/oe/ue/ss) to keep the module
# ASCII (CLAUDE.md "MATSim and eqasim style"); the codebook cell is named beside each code so the
# original spelling stays traceable.
#: Fine ``V_ZWECK`` code -> the model's coarse purpose. The three coarse purposes are exactly the
#: ones for which the model estimates a W_ZWD subtype split; every other delivered purpose is in
#: :data:`OUT_OF_SCOPE_V_ZWECK`.
COARSE_BY_FINE = {
    8: "shop", 9: "shop",
    10: "other_errand", 11: "other_errand",
    13: "leisure", 14: "leisure", 15: "leisure", 16: "leisure", 17: "leisure", 18: "leisure",
}

#: Codebook value labels of the measured fine codes (cells as noted).
FINE_LABELS = {
    8: "Einkauf taeglicher Bedarf",                                        # F8244
    9: "Sonstiger Einkauf",                                                # F8245
    10: "Behoerdengang, Arztbesuch",                                       # F8246
    11: "Dienstleistungseinrichtung (z. B. Post, Bank, Friseur, Apotheke)",  # F8247
    13: "Kultur, Theater, Kino",                                           # F8249
    14: "Gaststaette/Kneipe",                                              # F8250
    15: "Privater Besuch (fremde Wohnung)",                                # F8251
    16: "Erholung/Sport im Freien (auch Wandern, Hund ausfuehren o. ae.)",  # F8252
    17: "Sportstaette (allgemein)",                                        # F8253
    18: "Andere Freizeitaktivitaet",                                       # F8254
}

#: Delivered ``V_ZWECK`` codes that are deliberately NOT measured here, with their codebook
#: label: the model estimates no W_ZWD subtype split for them, so they carry no fine-purpose
#: share to compare. Listing them explicitly (rather than filtering silently on COARSE_BY_FINE)
#: is what lets the builder raise on an unknown code.
OUT_OF_SCOPE_V_ZWECK = {
    -10: "Unplausibel",                                       # F8236
    1: "Eigener Arbeitsplatz",                                # F8237
    2: "Anderer Dienstort/-weg",                              # F8238
    3: "Kinderkrippe/-garten",                                # F8239
    4: "Grundschule",                                         # F8240
    5: "Weiterfuehrende Schule (z. B. Mittelschule, Oberschule, Gymnasium)",  # F8241
    6: "Berufs-, Fach-, Hochschule",                          # F8242
    7: "Andere Bildungseinrichtung",                          # F8243
    12: "Bringen oder Holen von Personen",                    # F8248
    19: "Eigene Wohnung",                                     # F8255
    70: "Sonstiges",                                          # F8256
}

COARSE_PURPOSES = ("shop", "other_errand", "leisure")

# --------------------------------------------------------------------------- MiD subtype bridge
#: How each MiD ``W_ZWD`` subtype group of :mod:`braunschweig.popsim.purpose_subtype` /
#: :mod:`braunschweig.popsim.shop_subtype` maps onto SrV fine ``V_ZWECK`` codes, with an
#: EXACTNESS grade that says how much the comparison of the two shares is worth:
#:
#: * ``exact``          -- the SrV code and the MiD group describe the same activity
#:                        ("Einkauf taeglicher Bedarf" vs W_ZWD 501 "Einkauf taeglich").
#: * ``approximate``    -- the two describe overlapping but not identical activities, so a
#:                        difference may be a taxonomy artefact rather than a regional one.
#: * ``aggregate_only`` -- SrV codes no counterpart at all; the group can only be compared
#:                        against the coarse purpose as a whole, so no share is emitted.
#:
#: ASSUMPTION (issue #242 Task 6, not an established mapping from a published source): the
#: exactness grades below are the author's reading of the two codebooks' value labels. They are
#: recorded here so the comparison states its own confidence explicitly; they are not a
#: validated crosswalk.
SUBTYPE_TO_SRV_FINE = {
    # 8 "Einkauf taeglicher Bedarf" == W_ZWD 501 "Einkauf taeglich".
    "shop_daily": ((8,), "exact"),
    # 9 "Sonstiger Einkauf" == W_ZWD 502-505 (non-daily shopping).
    "shop_non_daily": ((9,), "exact"),
    # 10 "Behoerdengang, Arztbesuch" == W_ZWD 601 "Arztbesuch" + 602 "Behoerde, Bank, Post".
    "other_errand_short": ((10,), "exact"),
    # 11 "Dienstleistungseinrichtung" overlaps but is not identical to W_ZWD 603/604/605
    # (errand for another person, other errand, caring for family members).
    "other_errand_long": ((11,), "approximate"),
    # 15 "Privater Besuch (fremde Wohnung)" == W_ZWD 701 "Besuch/Treffen Freunde, Verwandte".
    "leisure_visit": ((15,), "exact"),
    # 14 "Gaststaette/Kneipe" + 16 "Erholung/Sport im Freien" vs W_ZWD 706/710/711/713/716.
    "leisure_local": ((14, 16), "approximate"),
    # 13 "Kultur, Theater, Kino" + 17 "Sportstaette" vs W_ZWD 702/703/704/707/720/721(/799).
    "leisure_activity": ((13, 17), "approximate"),
    # SrV 2023 codes no day-trip / holiday purpose at all (18 "Andere Freizeitaktivitaet" is a
    # residual, not an excursion code), so W_ZWD 708/709/722 have no fine counterpart.
    "leisure_excursion": ((), "aggregate_only"),
}

EXACTNESS_VALUES = ("exact", "approximate", "aggregate_only")

#: A subtype group is flagged as a re-estimation CANDIDATE when its MiD and SrV shares differ by
#: more than this many percentage points AND the crosswalk is ``exact`` (issue #242 Task 6). The
#: flag is a measurement signal, not a decision: nothing is re-estimated in this package.
CANDIDATE_DELTA_PP_THRESHOLD = 10.0

# --------------------------------------------------------------------------- data conventions
#: Trip columns read from ``SrV2023_Wege.csv``.
TRIP_COLUMNS = ["HHNR", "PNR", "WNR", "V_ZWECK", "GEWICHT_W_ZENSUS", "GIS_LAENGE_GUELTIG",
                "E_DAUER", "MITTL_WERKTAG"]

REFERENCE_COLUMNS = ["coarse", "fine_code", "label", "n_unweighted", "share_within_coarse",
                     "gis_km_p25", "gis_km_p50", "gis_km_p75", "duration_min_p50"]

AVERAGE_WEEKDAY = 1                 # MITTL_WERKTAG == 1 "Mittlerer Werktag" (codebook F320)
#: ``GIS_LAENGE_GUELTIG`` [km] "Laenge des Weges (berechnet, nur fuer gueltige Wege)"; -7 is
#: "Berechnung nicht moeglich" (codebook F8826). A length must be strictly positive to enter the
#: percentiles -- 0 km would be a degenerate trip, and no delivered trip carries it.
GIS_INVALID_CODE = -7.0
#: ``E_DAUER`` [min] "Dauer des Weges"; -10 "Unplausibel" (F8836), -7 "Berechnung nicht moeglich"
#: (F8837). Both are missing-value codes, not durations.
DURATION_INVALID_CODES = (-10, -7)
QUANTILE_PROBABILITIES = (0.25, 0.5, 0.75)


def coarse_of(fine_code) -> str:
    """Coarse purpose of a MEASURED fine ``V_ZWECK`` code; raises for any other code."""
    try:
        return COARSE_BY_FINE[int(fine_code)]
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            f"{_LOG_TAG} V_ZWECK {fine_code} is not a measured fine purpose code "
            f"(measured: {sorted(COARSE_BY_FINE)})."
        ) from None


def label_of(fine_code) -> str:
    """Codebook label of a MEASURED fine ``V_ZWECK`` code; raises for any other code."""
    try:
        return FINE_LABELS[int(fine_code)]
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            f"{_LOG_TAG} V_ZWECK {fine_code} has no codebook label in this module "
            f"(labelled: {sorted(FINE_LABELS)})."
        ) from None


def _require_columns(frame: pd.DataFrame, required, name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} {name}: missing required column(s) {missing}")


def _weighted_percentiles(values, weights, probabilities=QUANTILE_PROBABILITIES) -> np.ndarray:
    """Weighted percentiles, NaN (never 0.0) when no observation carries a valid value.

    Reuses :func:`braunschweig.calibration.srv_distance_targets.weighted_quantiles` (Hazen
    midpoint-CDF convention) so the SrV side and the MiD side of the comparison use ONE
    percentile definition -- a second implementation would make the two sides silently
    incomparable.
    """
    return weighted_quantiles(np.asarray(values, dtype=float), np.asarray(weights, dtype=float),
                              list(probabilities))


def build_fine_purpose_reference(trips: pd.DataFrame) -> tuple:
    """Within-coarse shares and weighted distance/duration percentiles per fine ``V_ZWECK``.

    Parameters
    ----------
    trips : DataFrame
        Raw-shaped SrV trip frame carrying :data:`TRIP_COLUMNS`.

    Returns
    -------
    tuple[DataFrame, dict]
        The table with :data:`REFERENCE_COLUMNS` -- one row per code in
        :data:`COARSE_BY_FINE`, in coarse-purpose then code order, an empty code emitted with
        ``n_unweighted = 0`` and NaN statistics rather than dropped -- and the diagnostics dict
        the extraction script writes into the committed file's provenance header.

    Raises
    ------
    ValueError
        On a missing column, on a ``MITTL_WERKTAG`` other than :data:`AVERAGE_WEEKDAY` (the
        delivery is not the pure average-weekday universe this table assumes), on a
        missing/non-positive ``GEWICHT_W_ZENSUS`` (ADR-0055 expansion weights must be present),
        or on a ``V_ZWECK`` that is in neither :data:`COARSE_BY_FINE` nor
        :data:`OUT_OF_SCOPE_V_ZWECK`.

    Notes
    -----
    ``share_within_coarse`` is GEWICHT_W_ZENSUS-WEIGHTED while ``n_unweighted`` is the plain row
    count, so the two columns answer different questions on purpose. A trip whose GIS length or
    duration is a missing-value code still counts towards ``n_unweighted`` and towards the share
    -- only its distance/duration is unknown, not the trip itself.
    """
    _require_columns(trips, TRIP_COLUMNS, "trips")

    werktag = pd.to_numeric(trips["MITTL_WERKTAG"], errors="coerce")
    if not (werktag == AVERAGE_WEEKDAY).all():
        raise ValueError(
            f"{_LOG_TAG} MITTL_WERKTAG is not {AVERAGE_WEEKDAY} for every trip (found "
            f"{sorted(werktag[werktag != AVERAGE_WEEKDAY].dropna().unique().tolist())}); the "
            "delivery is not the pure average-weekday universe this table assumes")

    weight = pd.to_numeric(trips["GEWICHT_W_ZENSUS"], errors="coerce")
    n_invalid_weight = int((~(weight > 0)).sum())
    if n_invalid_weight:
        raise ValueError(
            f"{_LOG_TAG} {n_invalid_weight}/{len(trips)} trips have a missing or non-positive "
            "GEWICHT_W_ZENSUS; an expansion weight must be present for every trip of this "
            "delivery (ADR-0055). Investigate the delivery rather than dropping the rows.")

    fine = pd.to_numeric(trips["V_ZWECK"], errors="coerce")
    if fine.isna().any():
        raise ValueError(f"{_LOG_TAG} {int(fine.isna().sum())} trips have a non-numeric V_ZWECK")
    fine = fine.astype(int)
    unknown_codes = sorted(set(fine.unique()) - set(COARSE_BY_FINE) - set(OUT_OF_SCOPE_V_ZWECK))
    if unknown_codes:
        raise ValueError(
            f"{_LOG_TAG} V_ZWECK code(s) {unknown_codes} are in neither COARSE_BY_FINE nor "
            "OUT_OF_SCOPE_V_ZWECK; add them explicitly (with their codebook label) rather than "
            "letting a new purpose code fall into a silent bucket.")

    n_trips_raw = int(len(trips))
    measured_mask = fine.isin(COARSE_BY_FINE).values
    n_out_of_scope = int((~measured_mask).sum())
    logger.info("%s dropped %d/%d trips (%.2f%%) whose V_ZWECK is out of scope (%s)", _LOG_TAG,
                n_out_of_scope, n_trips_raw,
                100.0 * n_out_of_scope / n_trips_raw if n_trips_raw else float("nan"),
                "work/education/escort/home/other/implausible")

    gis_km = pd.to_numeric(trips["GIS_LAENGE_GUELTIG"], errors="coerce")
    duration_min = pd.to_numeric(trips["E_DAUER"], errors="coerce")
    measured = pd.DataFrame({
        "fine_code": fine.values,
        "weight": weight.astype(float).values,
        "gis_km": gis_km.values,
        "duration_min": duration_min.values,
    })[measured_mask].copy()
    measured["coarse"] = [COARSE_BY_FINE[code] for code in measured["fine_code"]]
    measured["gis_valid"] = measured["gis_km"] > 0
    measured["duration_valid"] = ~measured["duration_min"].isin(DURATION_INVALID_CODES) \
        & measured["duration_min"].notna()

    n_measured = int(len(measured))
    n_gis_invalid = int((~measured["gis_valid"]).sum())
    n_duration_invalid = int((~measured["duration_valid"]).sum())
    logger.info("%s measured %d/%d trips (%.2f%%); GIS length valid for %d/%d (%.2f%%), "
                "duration valid for %d/%d (%.2f%%)", _LOG_TAG, n_measured, n_trips_raw,
                100.0 * n_measured / n_trips_raw if n_trips_raw else float("nan"),
                n_measured - n_gis_invalid, n_measured,
                100.0 * (n_measured - n_gis_invalid) / n_measured if n_measured else float("nan"),
                n_measured - n_duration_invalid, n_measured,
                100.0 * (n_measured - n_duration_invalid) / n_measured if n_measured else float("nan"))

    coarse_weight_total = measured.groupby("coarse")["weight"].sum().to_dict()
    rows = []
    for coarse in COARSE_PURPOSES:
        for code in sorted(code for code, name in COARSE_BY_FINE.items() if name == coarse):
            group = measured[measured["fine_code"] == code]
            total = float(coarse_weight_total.get(coarse, 0.0))
            share = float(group["weight"].sum() / total) if total > 0 else float("nan")
            valid_gis = group[group["gis_valid"]]
            valid_duration = group[group["duration_valid"]]
            p25, p50, p75 = _weighted_percentiles(valid_gis["gis_km"], valid_gis["weight"])
            duration_p50 = _weighted_percentiles(valid_duration["duration_min"],
                                                 valid_duration["weight"], [0.5])[0]
            rows.append({"coarse": coarse, "fine_code": code, "label": FINE_LABELS[code],
                         "n_unweighted": int(len(group)), "share_within_coarse": share,
                         "gis_km_p25": p25, "gis_km_p50": p50, "gis_km_p75": p75,
                         "duration_min_p50": duration_p50})

    table = pd.DataFrame(rows, columns=REFERENCE_COLUMNS)
    diagnostics = {
        "n_trips_raw": n_trips_raw,
        "n_trips_out_of_scope": n_out_of_scope,
        "n_trips_measured": n_measured,
        "n_trips_gis_invalid": n_gis_invalid,
        "n_trips_duration_invalid": n_duration_invalid,
    }
    return table, diagnostics


def check_invariants(table: pd.DataFrame) -> None:
    """Raise unless the table is complete, ordered and internally consistent."""
    _require_columns(table, REFERENCE_COLUMNS, "fine purpose table")
    expected = [code for coarse in COARSE_PURPOSES
                for code in sorted(c for c, name in COARSE_BY_FINE.items() if name == coarse)]
    if list(table["fine_code"]) != expected:
        raise ValueError(f"{_LOG_TAG} table must carry exactly the fine codes {expected}, in that "
                         f"order; found {list(table['fine_code'])}")
    shares = table["share_within_coarse"].dropna()
    if ((shares < 0) | (shares > 1)).any():
        raise ValueError(f"{_LOG_TAG} share_within_coarse outside [0, 1]")
    for coarse, group in table.groupby("coarse"):
        total = float(group["share_within_coarse"].sum())
        if group["share_within_coarse"].notna().any() and abs(total - 1.0) > 1e-9:
            raise ValueError(f"{_LOG_TAG} share_within_coarse of '{coarse}' sums to {total}, not 1")
    for column in ("gis_km_p25", "gis_km_p50", "gis_km_p75", "duration_min_p50"):
        values = table[column].dropna()
        if (values <= 0).any():
            raise ValueError(f"{_LOG_TAG} {column} must be positive where it is defined")
    ordered = table.dropna(subset=["gis_km_p25", "gis_km_p50", "gis_km_p75"])
    if ((ordered["gis_km_p25"] > ordered["gis_km_p50"])
            | (ordered["gis_km_p50"] > ordered["gis_km_p75"])).any():
        raise ValueError(f"{_LOG_TAG} GIS percentiles are not monotone (p25 <= p50 <= p75)")
