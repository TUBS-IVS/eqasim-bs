"""Model-side absence composition by age band (issue #426).

Computes, on a pipeline persons table (``persons.csv`` of ``synthesis.output``: ``person_id``,
``household_id``, ``age``, ``day_absence_state``), the SAME three-way composition of ABSENT persons
that :func:`braunschweig.calibration.srv_absence.build_absence_composition_by_band` computes on
the SrV microdata -- whole household away / part of the household away WITH another absent adult
/ part away WITHOUT one -- and compares it to the committed reference
``srv2023_absence_composition_by_age_band.csv`` under the pre-registered bound: the model share
of the ``0-17`` children row must lie inside the Wilson 95 % interval of the reference row's
UNWEIGHTED counts (the seven age bands are diagnostic only).

Why a separate measurement: the draw's own diagnostics report per-band rates and clustering, not
this composition, and the figure quoted for arm 4 in issue #426 before this module existed
("1,721 of 1,739 individually absent children have a present adult at home") was an ad-hoc probe
that no run manifest carries. Every number cited for the #426 criterion comes from here, on a
named persons file (path and md5 in the header ``scripts/measure_absence_composition.py`` writes).

Pure module: no file I/O. Weights are 1.0 -- a synthetic population is a 100 % sample, so the
reused builder's weighted shares equal k/n.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from braunschweig.calibration import srv_absence as A
from braunschweig.synthesis.day_absence.absence import STATE_PRESENT, STATES

logger = logging.getLogger(__name__)
_LOG_TAG = "[absence composition]"

MODEL_PERSON_COLUMNS = ["person_id", "household_id", "age", "day_absence_state"]
COMPARISON_COLUMNS = ["band", "pattern", "evaluated", "n_model_absent", "n_model_pattern", "p_model",
                      "n_srv_absent_unweighted", "n_srv_pattern_unweighted", "p_srv_unweighted",
                      "p_srv_weighted", "srv_ci_low", "srv_ci_high", "inside_interval"]


def persons_to_absence_frame(persons: pd.DataFrame) -> pd.DataFrame:
    """Persons table -> the ``prepared``-shaped frame the srv_absence builders consume.

    Raises when the state column is missing (``day_absence_state`` is written only with
    ``day_absence_enabled`` true), when a state is not one of the draw's states, when an age is
    non-numeric, missing or negative (pipeline ages must be valid; silently coercing them would
    drop persons from every band), or when NOBODY is absent -- an empty composition must never
    pass for a measurement."""
    missing = [column for column in MODEL_PERSON_COLUMNS if column not in persons.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} persons table lacks column(s) {missing}; day_absence_state exists in "
                         "persons.csv only when day_absence_enabled is true")
    state = persons["day_absence_state"]
    unknown = sorted(set(state.dropna().astype(str).unique()) - set(STATES))
    if unknown or state.isna().any():
        raise ValueError(f"{_LOG_TAG} day_absence_state carries unknown value(s) {unknown} and/or "
                         f"{int(state.isna().sum())} missing values; expected exactly {STATES}")
    absent = (state != STATE_PRESENT).to_numpy()
    if not absent.any():
        raise ValueError(f"{_LOG_TAG} no absent person among {len(persons)} rows -- the draw is off or "
                         "broken, there is nothing to compose")
    age = pd.to_numeric(persons["age"], errors="coerce")
    invalid_age = age.isna() | (age < 0)
    if invalid_age.any():
        examples = persons.loc[invalid_age.to_numpy(), "person_id"].head(5).tolist()
        raise ValueError(f"{_LOG_TAG} {int(invalid_age.sum())} person(s) have a non-numeric, missing or negative "
                         f"age; pipeline ages must be valid (example person_ids {examples})")
    frame = pd.DataFrame({"hhnr": persons["household_id"].to_numpy(), "pnr": persons["person_id"].to_numpy(),
                          "age": age.to_numpy(), "weight": 1.0, "absent": absent})
    frame["band"] = A.age_band(frame["age"]).to_numpy()
    logger.info("%s %d/%d persons absent (%.2f%%)", _LOG_TAG, int(absent.sum()), len(frame),
                100.0 * absent.sum() / len(frame))
    return frame


def model_absence_composition_by_band(persons: pd.DataFrame) -> pd.DataFrame:
    """The SrV composition table's columns and rows, computed on the model population."""
    return A.build_absence_composition_by_band(persons_to_absence_frame(persons))


def compare_composition(model: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Long table: one row per (band, pattern) with the model share, the reference counts and
    shares, the Wilson 95 % interval on the reference's UNWEIGHTED counts and ``inside_interval``
    (``None`` where the reference row has no absent person -- never a substituted verdict).
    ``evaluated`` marks the acceptance row (``0-17``); every other row is diagnostic."""
    expected_rows = list(A.AGE_BAND_LABELS) + [A.CHILDREN_ROW, A.ALL_BAND]
    for name, table in (("model", model), ("reference", reference)):
        if list(table["band"]) != expected_rows:
            raise ValueError(f"{_LOG_TAG} {name} table must carry exactly the rows {expected_rows}")
    model_by_band, reference_by_band = model.set_index("band"), reference.set_index("band")
    rows = []
    for band in expected_rows:
        n_model = int(model_by_band.loc[band, "n_absent_unweighted"])
        n_srv = int(reference_by_band.loc[band, "n_absent_unweighted"])
        for pattern, count_column, share_column in zip(A.PATTERNS, A.COMPOSITION_COUNT_COLUMNS,
                                                       A.COMPOSITION_SHARE_COLUMNS):
            k_srv = int(reference_by_band.loc[band, count_column])
            p_model = float(model_by_band.loc[band, share_column])
            low, high = A.wilson_interval(k_srv, n_srv)
            inside = None if (math.isnan(p_model) or math.isnan(low)) else bool(low <= p_model <= high)
            rows.append({"band": band, "pattern": pattern, "evaluated": band == A.CHILDREN_ROW,
                         "n_model_absent": n_model, "n_model_pattern": int(model_by_band.loc[band, count_column]),
                         "p_model": p_model, "n_srv_absent_unweighted": n_srv, "n_srv_pattern_unweighted": k_srv,
                         "p_srv_unweighted": k_srv / n_srv if n_srv else float("nan"),
                         "p_srv_weighted": float(reference_by_band.loc[band, share_column]),
                         "srv_ci_low": low, "srv_ci_high": high, "inside_interval": inside})
    table = pd.DataFrame(rows, columns=COMPARISON_COLUMNS)
    for _, row in table[table["evaluated"]].iterrows():
        logger.info("%s %s %s: model %.4f (n=%d) vs SrV %.4f unweighted / %.4f weighted (n=%d), Wilson95 "
                    "[%.4f, %.4f] -> %s", _LOG_TAG, row["band"], row["pattern"], row["p_model"],
                    row["n_model_absent"], row["p_srv_unweighted"], row["p_srv_weighted"],
                    row["n_srv_absent_unweighted"], row["srv_ci_low"], row["srv_ci_high"],
                    {True: "INSIDE", False: "OUTSIDE", None: "not evaluable"}[row["inside_interval"]])
    return table
