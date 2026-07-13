"""Shared reader for the MiD 2023 Tabelle P9 (Umfang der Erwerbstaetigkeit)
per-Kreis percentage table.

P9 reports, per Kreis, the percentage of persons aged 14+ falling into each of
the seven ``EMPLOYMENT_STATUS_CATEGORIES`` classes (plus ``keine_angabe``,
item non-response). This module centralises the CSV read + row-normalisation
logic so it is written ONCE and reused by every consumer instead of being
copy-pasted:

* :func:`braunschweig.analysis.population_validation.controls.employment_target`
  and ``employment_status_target`` (the existing per-Kreis validation targets).
* :func:`mid_p9_employment_status_by_kreis` (this module), the MiD side of the
  MiD x SrV ``employment_status`` control blend (feature #172, Task 2) -- built
  in the SAME blend-ready shape as the SrV side
  (``srv2023_employment_status_by_kreis.csv``: columns ``code``, the 7
  employment_status class shares, ``n_unweighted``) so a later task can align
  and blend the two frames.

Input file: ``<data_path>/braunschweig/mid/mid2023_P9.csv``. Columns:
``kreis`` (German Kreis name / "Gesamt"), ``ars5`` (5-digit ARS Kreis code /
"03ZGB" for the region aggregate), ``n_weighted``, ``n_unweighted`` (the
table's own per-Kreis sample-size columns), the 7
``EMPLOYMENT_STATUS_CATEGORIES`` percentage columns, and ``keine_angabe``.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim.attributes import EMPLOYMENT_STATUS_CATEGORIES

LOGGER = logging.getLogger("braunschweig.popsim.mid_p9")

P9_RELATIVE_PATH = "braunschweig/mid/mid2023_P9.csv"

# The P9 CSV's own region-aggregate row: kreis="Gesamt", ars5="03ZGB".
ZGB_AGGREGATE_ARS5 = "03ZGB"
GESAMT_LABEL = "Gesamt"


def read_p9_table(data_path: str) -> pd.DataFrame:
    """Read the raw MiD 2023 P9 CSV, unfiltered (includes the ``03ZGB``
    region-aggregate row)."""
    path = f"{data_path}/{P9_RELATIVE_PATH}"
    return pd.read_csv(path, comment="#", dtype={"ars5": str})


def read_p9_kreis_table(data_path: str) -> pd.DataFrame:
    """Per-Kreis-only P9 frame: drops the ``03ZGB`` region-aggregate row.

    This is the frame the existing per-Kreis validation targets
    (``employment_target`` / ``employment_status_target``) consume -- they
    compare against one row per Kreis, not the region aggregate.
    """
    df = read_p9_table(data_path)
    return df[df["ars5"] != ZGB_AGGREGATE_ARS5].copy()


def p9_class_shares(df: pd.DataFrame) -> pd.DataFrame:
    """Row-normalise ``df``'s ``EMPLOYMENT_STATUS_CATEGORIES`` columns to shares.

    The denominator is the sum of the SEVEN class columns, EXCLUDING
    ``keine_angabe`` (item non-response) -- the same convention already used
    by ``employment_target`` / ``employment_status_target``: the published
    P9 rows are integer percentages that do not always sum to exactly 100, so
    the actual row total (not a literal 100) is used as the denominator.

    Returns a frame with the same index as ``df`` and columns
    ``EMPLOYMENT_STATUS_CATEGORIES`` (each row summing to 1.0).

    Raises
    ------
    ValueError
        If any row's seven class columns sum to a non-positive total (a
        malformed/corrupt input row) -- per the project's no-silent-fallback
        rule, this cannot be masked by dividing by zero into NaN shares.
    """
    cats = list(EMPLOYMENT_STATUS_CATEGORIES)
    class_values = df[cats].fillna(0.0)
    denom = class_values.sum(axis=1)
    if (denom <= 0).any():
        bad_mask = denom <= 0
        bad = df.loc[bad_mask, "ars5"].tolist() if "ars5" in df.columns else denom[bad_mask].index.tolist()
        raise ValueError(f"mid2023_P9.csv: non-positive class total for {bad}")
    return class_values.div(denom, axis=0)


def mid_p9_employment_status_by_kreis(data_path: str) -> pd.DataFrame:
    """MiD-P9 side of the ``employment_status`` MiD x SrV blend (feature #172).

    Returns a frame with columns ``code`` (5-digit ARS Kreis string, plus one
    region-aggregate row labelled ``"Gesamt"``), the 7
    ``EMPLOYMENT_STATUS_CATEGORIES`` class-share columns (each row summing to
    1.0), and ``n_unweighted``. This is deliberately the SAME shape as the SrV
    side (``srv2023_employment_status_by_kreis.csv``, built by
    ``scripts/extract_srv_employment_status_kreis.py``), so a later task can
    align both frames on ``code`` and blend them (e.g. via
    ``braunschweig.popsim.blended_targets.blend_kreis_target``).

    The ``Gesamt`` row reuses the P9 table's OWN published region-aggregate
    row (``kreis="Gesamt"``, ``ars5="03ZGB"``) rather than re-deriving a
    region aggregate from the per-Kreis rows here -- the source already
    publishes a real region-wide figure, so using it is more faithful than
    approximating a new one (CLAUDE.md: no invented reference values).

    ASSUMPTION: ``n_unweighted`` is taken VERBATIM from the P9 CSV's own
    ``n_unweighted`` column (the per-Kreis/region MiD 2023 P9 respondent
    count, age>=14 basis, INCLUDING ``keine_angabe`` item-non-response rows).
    P9 is published only as a percentage table (no underlying person-level
    microdata is redistributed in this repository), so the table's own
    ``n_unweighted`` column is the only per-Kreis sample-size figure
    available -- there is no finer-grained alternative to derive it from.
    Using the table's own base (rather than inventing a constant) keeps the
    blend's implicit precision-weighting between the MiD and SrV sides
    grounded in each source's real sample size.

    Raises
    ------
    ValueError
        If any row's seven class columns sum to a non-positive total (see
        :func:`p9_class_shares`).
    """
    df = read_p9_table(data_path)
    shares = p9_class_shares(df)

    codes = np.where(df["ars5"].to_numpy() == ZGB_AGGREGATE_ARS5,
                     GESAMT_LABEL, df["ars5"].to_numpy())
    out = pd.DataFrame({"code": codes})
    for category in EMPLOYMENT_STATUS_CATEGORIES:
        out[category] = shares[category].to_numpy()
    out["n_unweighted"] = df["n_unweighted"].astype(int).to_numpy()

    n_kreis_rows = int((out["code"] != GESAMT_LABEL).sum())
    LOGGER.info(
        "[mid_p9] employment_status per-Kreis frame: %d Kreis row(s) + 1 %s "
        "aggregate row; n_unweighted taken verbatim from mid2023_P9.csv's own "
        "n_unweighted column (ASSUMPTION: per-Kreis P9 respondent base, "
        "includes keine_angabe non-response rows -- see docstring).",
        n_kreis_rows, GESAMT_LABEL,
    )
    return out[["code", *EMPLOYMENT_STATUS_CATEGORIES, "n_unweighted"]]
