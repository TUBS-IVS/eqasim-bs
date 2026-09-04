"""SrV 2023 work-participation reference per Kreis (spec 2026-09-04-commute-day-state-design.md).

Builds a small committed aggregate: for each home Kreis (plus the ZGB region total) the
GEWICHT_P_ZENSUS-weighted share of employed persons who worked a full home-office day on the
reporting day, who instead made a work trip, or neither. This is a REGIONAL (Braunschweig +
Regionalverband Grossraum Braunschweig, "RGB") counterpart to the national MiD-side
commute-day-state reference in ``braunschweig.calibration.commute_day_state_reference`` --
both feed the validation of the far/weekly commuter model (spec 2026-09-04, issue #244), NOT a
control target. This module is a pure builder over already-loaded SrV person/trip/household
frames; it has no synpp dependency and is not imported by any population-synthesis or location
stage. Called by ``scripts/extract_srv_work_participation.py`` against the LOCAL-ONLY SrV 2023
"Braunschweig und RGB" scientific-use microdata (never committed).

SrV 2023 variables (codebook ``SrV2023_Datenkodierung_SciUse.xlsx``):
- ``V_WOHNUNG_HO`` ("Homeoffice am Stichtag", Personen file): 1 = worked the WHOLE reporting
  day from home, 2 = no, -8 = not asked (question not applicable -- not employed), -10 = no
  answer. The universe of this module is ``V_WOHNUNG_HO in (1, 2)``, i.e. employed persons who
  were actually asked the question (measured on the 2026-09-04 raw extract: 1,175 code 1;
  6,841 code 2; 10,176 code -8; 31 code -10; universe = 8,016).
- ``V_ZWECK`` (trip purpose, Wege file): 1 = "Arbeit" (own workplace). A person with at least
  one such trip on the reporting day is counted as having made a work trip.
- ``GEWICHT_P_ZENSUS`` (Personen file): person expansion weight to Zensus 2022 population
  counts (the stratum-internal ``GEWICHT_P`` must not be used across strata, same convention
  as ``braunschweig.calibration.srv_distance_targets``/``scripts.extract_srv_kreis_tables``).
- ``MITTL_WERKTAG`` (Personen file): 1 marks an average-weekday person. Every person in the
  delivered file already carries ``MITTL_WERKTAG == 1`` (measured: 18,223/18,223); the filter
  is applied explicitly regardless, so the assumption is documented and its (here zero) drop
  rate is logged rather than silently assumed.
- Household ``AGS`` (Haushalte file) -> Kreis: first 5 digits of the household's 8-digit,
  zero-padded AGS, joined to each person via ``HHNR``. Mirrors
  ``braunschweig.calibration.srv_distance_targets._kreis_from_ags`` (re-implemented locally,
  see ``_kreis_from_household_ags``, so this module stays a self-contained pure builder with
  no cross-module private-symbol dependency). ``ZGB_KREISE``/``WOLFSBURG_KREIS`` themselves ARE
  imported from ``srv_distance_targets`` (the single source of truth for the 8 ZGB Kreis codes,
  already reused the same way by ``braunschweig.analysis.synthesis.commute_distance_by_kreis``).

Classification (a person cannot be both -- ``share_home_office_day`` takes priority): measured
on the 2026-09-04 raw extract, 0 universe persons report BOTH ``V_WOHNUNG_HO == 1`` and a work
trip -- the ASSUMPTION that a home-office day dominates a same-day work trip is therefore
untested on real SrV data so far, but the count is logged every run (no silent fallback) so a
future re-extraction that DOES exhibit the combination is visible rather than silently absorbed.

- ``share_home_office_day``: weighted share with ``V_WOHNUNG_HO == 1``.
- ``share_work_trip``: weighted share with ``V_WOHNUNG_HO != 1`` AND at least one work trip.
- ``share_neither``: the remainder (``V_WOHNUNG_HO != 1`` and no work trip).

The three shares sum to 1.0 for every row with ``n_persons > 0``. Wolfsburg (03103) is not
surveyed by SrV: its row is still emitted (one row per code in ``ZGB_KREISE``, per the
project's no-silent-fallback convention of never dropping an expected geography row), with
``n_persons == 0`` and ``NaN`` shares.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_distance_targets import WOLFSBURG_KREIS, ZGB_KREISE

logger = logging.getLogger(__name__)

_LOG_TAG = "[srv work participation]"

HOME_OFFICE_DAY = 1        # V_WOHNUNG_HO: worked the whole reporting day from home
NO_HOME_OFFICE_DAY = 2     # V_WOHNUNG_HO: did not work from home on the reporting day
V_WOHNUNG_HO_ASKED = (HOME_OFFICE_DAY, NO_HOME_OFFICE_DAY)
WORK_TRIP_PURPOSE = 1      # V_ZWECK: "Arbeit" (own workplace)
AVERAGE_WEEKDAY = 1        # MITTL_WERKTAG

STATE_HOME_OFFICE = "home_office"
STATE_WORK_TRIP = "work_trip"
STATE_NEITHER = "neither"

WORK_PARTICIPATION_TABLE = "srv2023_work_participation_by_kreis.csv"


def _kreis_from_household_ags(ags: pd.Series) -> pd.Series:
    """5-digit Kreis key from the household AGS; ``NaN`` for missing/non-positive input.

    Same SrV missing/sentinel-AGS convention as
    ``braunschweig.calibration.srv_distance_targets._kreis_from_ags``: a naive pandas
    ``Int64``-then-``str`` conversion turns a missing or non-positive sentinel AGS into a
    plausible-looking garbage key (``pd.NA`` -> ``"<NA>"``, ``-9`` -> ``"-0000009"``), which
    would then silently pass a ``notna()`` filter downstream. Resolving to a real ``NaN``
    first keeps that filter correct.
    """
    numeric = pd.to_numeric(ags, errors="coerce")
    valid = numeric.notna() & (numeric > 0)
    padded = numeric.fillna(0).astype("int64").astype(str).str.zfill(8)
    return padded.where(valid, np.nan).str[:5]


def build_srv_work_participation_table(persons: pd.DataFrame, trips: pd.DataFrame,
                                        households: pd.DataFrame) -> pd.DataFrame:
    """Weighted work-participation state (home office / work trip / neither) per home Kreis.

    ``persons`` must carry ``HHNR, PNR, V_WOHNUNG_HO, GEWICHT_P_ZENSUS, MITTL_WERKTAG``;
    ``trips`` must carry ``HHNR, PNR, V_ZWECK``; ``households`` must carry ``HHNR, AGS``.

    Universe filters, applied in order and each logged separately (rate + count, so a
    collapsed universe is diagnosable from the logs alone without re-deriving it):

    1. ``MITTL_WERKTAG == AVERAGE_WEEKDAY`` (average-weekday person).
    2. ``V_WOHNUNG_HO in V_WOHNUNG_HO_ASKED`` (employed persons who were asked the home-office
       question; drops -8 "not asked/not employed" and -10 "no answer" separately).
    3. ``GEWICHT_P_ZENSUS`` valid (non-missing, non-negative) -- defensive guard; the delivered
       SrV file has no such row, but a future delivery is not assumed to stay that way.
    4. Household AGS resolves to a Kreis (drops a missing/sentinel household AGS, logged; not
       expected to fire on the delivered file since every household carries a valid AGS).

    Returns one row per code in ``ZGB_KREISE`` (``level == "kreis"``, even for Wolfsburg
    (``WOLFSBURG_KREIS``), which has zero SrV persons and gets ``n_persons == 0`` with ``NaN``
    shares -- SrV does not survey Wolfsburg) plus one ``level == "zgb"`` row over every universe
    person. Columns: ``level, code, n_persons`` (unweighted person count) ``,
    share_home_office_day, share_work_trip, share_neither`` (weighted shares, summing to 1.0
    for ``n_persons > 0``; ``NaN`` for an empty Kreis).
    """
    n_input = len(persons)
    working = persons.copy()
    working["_weekday_ok"] = pd.to_numeric(working["MITTL_WERKTAG"], errors="coerce") == AVERAGE_WEEKDAY
    n_not_average_weekday = int((~working["_weekday_ok"]).sum())
    _log_or_warn(n_not_average_weekday, n_input,
                 "average-weekday filter (MITTL_WERKTAG == %d)" % AVERAGE_WEEKDAY)
    working = working[working["_weekday_ok"]].copy()

    ho_code = pd.to_numeric(working["V_WOHNUNG_HO"], errors="coerce")
    n_not_asked = int((ho_code == -8).sum())
    n_no_answer = int((ho_code == -10).sum())
    n_before_universe = len(working)
    universe = working[ho_code.isin(V_WOHNUNG_HO_ASKED)].copy()
    logger.info(
        "%s universe filter (V_WOHNUNG_HO in %s): %d/%d employed-and-asked persons kept "
        "(%d not asked/not employed [-8], %d no answer [-10], %d other/unmapped)",
        _LOG_TAG, V_WOHNUNG_HO_ASKED, len(universe), n_before_universe, n_not_asked, n_no_answer,
        n_before_universe - len(universe) - n_not_asked - n_no_answer,
    )

    weight = pd.to_numeric(universe["GEWICHT_P_ZENSUS"], errors="coerce")
    valid_weight = weight.notna() & (weight >= 0)
    n_invalid_weight = int((~valid_weight).sum())
    _log_or_warn(n_invalid_weight, len(universe), "weight validity filter (GEWICHT_P_ZENSUS >= 0)")
    universe = universe[valid_weight].copy()
    universe["weight"] = weight[valid_weight]

    work_trip_persons = trips.loc[
        pd.to_numeric(trips["V_ZWECK"], errors="coerce") == WORK_TRIP_PURPOSE, ["HHNR", "PNR"]
    ].drop_duplicates()
    universe = universe.merge(
        work_trip_persons.assign(_has_work_trip=True), on=["HHNR", "PNR"], how="left")
    universe["_has_work_trip"] = universe["_has_work_trip"].fillna(False)

    hh = households[["HHNR", "AGS"]].copy()
    hh["kreis"] = _kreis_from_household_ags(hh["AGS"])
    n_before_kreis = len(universe)
    universe = universe.merge(hh[["HHNR", "kreis"]], on="HHNR", how="left", validate="m:1")
    n_no_kreis = int(universe["kreis"].isna().sum())
    _log_or_warn(n_no_kreis, n_before_kreis, "household-AGS-to-Kreis resolution")
    universe = universe[universe["kreis"].notna()].copy()

    is_home_office = pd.to_numeric(universe["V_WOHNUNG_HO"], errors="coerce") == HOME_OFFICE_DAY
    n_both = int((is_home_office & universe["_has_work_trip"]).sum())
    logger.info(
        "%s %d/%d universe persons report BOTH a full home-office day (V_WOHNUNG_HO == 1) AND "
        "a work trip (V_ZWECK == 1); classed home_office per the module convention "
        "(share_home_office_day takes priority over share_work_trip)",
        _LOG_TAG, n_both, len(universe),
    )
    universe["state"] = np.select(
        [is_home_office, (~is_home_office) & universe["_has_work_trip"]],
        [STATE_HOME_OFFICE, STATE_WORK_TRIP],
        default=STATE_NEITHER,
    )

    rows = [_row("kreis", kreis, universe[universe["kreis"] == kreis]) for kreis in ZGB_KREISE]
    rows.append(_row("zgb", "zgb", universe))
    table = pd.DataFrame(rows, columns=[
        "level", "code", "n_persons", "share_home_office_day", "share_work_trip", "share_neither",
    ])

    wolfsburg_n = table.loc[table["code"] == WOLFSBURG_KREIS, "n_persons"].iloc[0]
    if wolfsburg_n == 0:
        logger.info("%s %s (Wolfsburg) has 0 SrV persons in this universe -- not surveyed by "
                    "SrV; row emitted with n_persons=0 and NaN shares", _LOG_TAG, WOLFSBURG_KREIS)
    logger.info("%s work-participation table: %d rows (%d Kreis + 1 zgb), %d persons total",
                _LOG_TAG, len(table), len(ZGB_KREISE), len(universe))
    return table


def _log_or_warn(n_dropped: int, n_before: int, step_description: str) -> None:
    """Log one universe-filter step; warns instead of informs when nothing survives a non-empty
    input, per the project's no-silent-fallback convention (a near-total drop almost always
    signals a broken filter, not a genuinely rare subpopulation)."""
    n_after = n_before - n_dropped
    rate = 100.0 * n_dropped / n_before if n_before else 0.0
    message = "%s %s: %d/%d rows dropped (%.1f%%), %d kept"
    args = (_LOG_TAG, step_description, n_dropped, n_before, rate, n_after)
    if n_before > 0 and n_after == 0:
        logger.warning(message, *args)
    else:
        logger.info(message, *args)


def _row(level: str, code: str, sub: pd.DataFrame) -> dict:
    weight = sub["weight"].astype(float)
    total_weight = weight.sum()
    row = {"level": level, "code": code, "n_persons": int(len(sub))}
    if len(sub) == 0 or total_weight <= 0:
        row.update(share_home_office_day=float("nan"), share_work_trip=float("nan"),
                   share_neither=float("nan"))
        return row
    for state, column in ((STATE_HOME_OFFICE, "share_home_office_day"),
                          (STATE_WORK_TRIP, "share_work_trip"),
                          (STATE_NEITHER, "share_neither")):
        row[column] = float(weight[sub["state"] == state].sum() / total_weight)
    return row


def _load(directory, name: str) -> pd.DataFrame:
    path = os.path.join(str(directory), name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Committed SrV work-participation reference missing: {path}. Regenerate with "
            "scripts/extract_srv_work_participation.py (the raw SrV 2023 microdata is "
            "local-only and never committed)."
        )
    return pd.read_csv(path, comment="#")


def load_srv_work_participation(srv_dir) -> pd.DataFrame:
    """Load the committed SrV 2023 work-participation-by-Kreis table (see
    ``WORK_PARTICIPATION_TABLE``); raises ``FileNotFoundError`` with a regeneration hint if
    absent."""
    return _load(srv_dir, WORK_PARTICIPATION_TABLE)
