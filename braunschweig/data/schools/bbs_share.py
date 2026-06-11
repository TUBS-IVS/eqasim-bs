"""Age-resolved BBS share for the upper-secondary (16-19) education cohort.

The education gravity model splits age-16-19 pupils between vocational BBS
(sparse, regional catchment, long trips) and the local gymnasiale Oberstufe.
By default a single scalar share applies (``education_bbs_share``); real BBS
enrollment rises steeply from 16 to 19, so the scalar mis-allocates the 16-19
trip-distance distribution.

This loader reads the optional reference CSV (expected at
``<data_path>/braunschweig/schools/nds_bbs_share_by_age.csv``) holding the
per-age pupil counts of the two school forms and derives

    bbs_share(age) = bbs_pupils / (bbs_pupils + oberstufe_pupils).

Expected CSV schema (one row per single age year; ``# Source:`` comment lines
allowed before the header)::

    age,bbs_pupils,oberstufe_pupils
    16,<count>,<count>
    ...

Data source (to be downloaded, not committed by default): regionalstatistik.de
/ GENESIS statistic 21211 (Berufliche Schulen: Schueler nach Altersgruppen,
Niedersachsen) for ``bbs_pupils`` and 21111 (Allgemeinbildende Schulen,
gymnasiale Oberstufe Sek II by age) for ``oberstufe_pupils``; alternatively the
LSN Statistische Berichte B I detail tables. Hard-coding shares in Python or
YAML without this provenance is prohibited (CLAUDE.md).

When the CSV is absent the loader returns ``None`` with an explicit INFO line
(the scalar ``education_bbs_share`` then applies) -- the absence is observable,
never silent.
"""
from __future__ import annotations

import os

import pandas as pd

REQUIRED_COLUMNS = ("age", "bbs_pupils", "oberstufe_pupils")


def load_bbs_share_by_age(path):
    """Load the per-age BBS shares from ``path``.

    Returns ``{age: bbs_share}`` (int -> float in [0, 1]) or ``None`` when the
    file does not exist (logged explicitly). Raises on schema/value errors --
    a malformed table must never silently degrade to the scalar share.
    """
    if not os.path.exists(path):
        print(
            f"[schools.bbs_share] no by-age BBS-share CSV at {path}; the scalar "
            "education_bbs_share applies to all ages 16-19. Drop the "
            "regionalstatistik 21211+21111 extract there to activate "
            "age-resolved shares."
        )
        return None

    df = pd.read_csv(path, comment="#")
    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(
            f"[schools.bbs_share] {path} is missing columns {sorted(missing)}; "
            f"expected schema {list(REQUIRED_COLUMNS)}."
        )
    if df["age"].duplicated().any():
        dupes = sorted(df.loc[df["age"].duplicated(), "age"].unique().tolist())
        raise ValueError(
            f"[schools.bbs_share] {path} has duplicated age rows: {dupes}."
        )

    shares = {}
    for _, row in df.iterrows():
        age = int(row["age"])
        bbs = float(row["bbs_pupils"])
        oberstufe = float(row["oberstufe_pupils"])
        if bbs < 0 or oberstufe < 0:
            raise ValueError(
                f"[schools.bbs_share] {path}: negative pupil count at age {age}."
            )
        total = bbs + oberstufe
        if total <= 0:
            raise ValueError(
                f"[schools.bbs_share] {path}: age {age} has zero pupils in both "
                "school forms; remove the row or fix the counts."
            )
        shares[age] = bbs / total

    print(
        "[schools.bbs_share] loaded age-resolved BBS shares from %s: %s"
        % (path, ", ".join(f"{a}: {s:.3f}" for a, s in sorted(shares.items())))
    )
    return shares
