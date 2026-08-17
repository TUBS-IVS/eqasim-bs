"""Shared guard: keep only rows whose Kreis key(s) are a clean 5-digit ARS.

Several BA / BBSR / INKAR exports mix real 5-digit Kreis Kennziffern with
Bundesland / Regierungsbezirk aggregates, or store the code in a way that a
plain ``str.zfill(5)`` turns into a non-joining garbage key (e.g. a
float-formatted ``"3101.0"``). A downstream left-merge on such a key then
silently drops or mis-joins those rows -- exactly the class of silent failure
CLAUDE.md forbids.

:func:`keep_valid_kreis5` enforces the ``\\d{5}`` contract explicitly, logs the
drop count as a visible rate, and raises when *every* row is dropped (which
signals a broken read/format rather than legitimate aggregate filtering). It
mirrors the inline ``str.fullmatch(r"\\d{5}", na=False)`` guard already used in
``braunschweig.data.census.pendler``.
"""

from __future__ import annotations

import logging
from typing import Iterable, Union

import pandas as pd


def keep_valid_kreis5(
    df: pd.DataFrame,
    cols: Union[str, Iterable[str]],
    *,
    source: str,
    logger: logging.Logger | None = None,
) -> pd.DataFrame:
    """Return the rows of ``df`` whose every key column matches ``\\d{5}``.

    Parameters
    ----------
    df:
        Input frame. Not mutated; a filtered copy is returned.
    cols:
        A single column name or an iterable of column names. Each must already
        hold the intended 5-digit Kreis key (e.g. after ``str.zfill(5)``). A row
        is kept only when ALL listed columns match ``\\d{5}``.
    source:
        Caller identifier used in the log/exception message (e.g. the stage
        module name).
    logger:
        Optional logger; defaults to ``logging.getLogger(source)``.

    Returns
    -------
    pandas.DataFrame
        Copy of ``df`` restricted to rows with valid 5-digit keys.

    Raises
    ------
    RuntimeError
        When ``df`` is non-empty but *every* row is dropped -- a strong signal
        that the key column was read in the wrong format.
    """
    log = logger or logging.getLogger(source)
    if isinstance(cols, str):
        cols = [cols]
    else:
        cols = list(cols)

    n_total = len(df)
    mask = pd.Series(True, index=df.index)
    for col in cols:
        mask &= df[col].astype(str).str.fullmatch(r"\d{5}", na=False)

    kept = df[mask].copy()
    n_dropped = n_total - len(kept)

    if n_dropped:
        log.warning(
            "[%s] Kreis-key guard: kept %d/%d rows with valid 5-digit %s; "
            "dropped %d row(s) whose key was not \\d{5} "
            "(aggregate / float-formatted / malformed).",
            source, len(kept), n_total, cols, n_dropped,
        )

    if n_total and kept.empty:
        raise RuntimeError(
            f"[{source}] Kreis-key guard dropped ALL {n_total} rows: no {cols} "
            f"value matched \\d{{5}}. This almost always means the key column "
            f"was read in the wrong format (e.g. a float-formatted '3101.0') -- "
            f"check the reader dtype."
        )

    return kept
