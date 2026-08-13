"""ENTD schema helpers: column-presence validation and the donor-schema rename.

- :func:`_require_columns`: fail-fast helper raising a clear ``ValueError``
  when required columns are missing from a donor/persons/households table.
- :func:`entd_persons_to_donor_schema`: renames raw ENTD persons to the MiD
  donor demographic schema (``H_ID``/``P_ID``/``HP_ALTER``/``HP_SEX``)
  consumed by :mod:`braunschweig.popsim.expand`.

Extracted verbatim from ``braunschweig.popsim.sources.entd`` (issue #267);
``entd.py`` re-exports both names so external imports of the facade module
are unaffected.
"""

from __future__ import annotations

import pandas as pd


def _require_columns(df: pd.DataFrame, required: list, *, table_name: str) -> None:
    """Raise a clear ValueError if any required column is missing (fail-fast)."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"[EntdSource] {table_name} is missing required column(s) {missing}; "
            f"available: {list(df.columns)}."
        )


def entd_persons_to_donor_schema(persons: pd.DataFrame) -> pd.DataFrame:
    """Rename ENTD persons to the MiD donor demographic schema used by expand.

    The PopulationSim output (``combined``) carries ``H_ID`` -- the ENTD
    household_id that ``EntdSource.build_seed`` wrote as the seed household key.
    ``braunschweig.popsim.expand.expand_to_persons`` joins the donor persons onto
    that output by ``H_ID`` and ``expand.map_demographics`` reads ``HP_ALTER`` /
    ``HP_SEX``. So the DONOR persons that ``assembly.build_persons`` expands must
    use the same names (``H_ID``, ``P_ID``, ``HP_ALTER``, ``HP_SEX``), symmetric
    with ``MidSource.load_donor`` (whose MiD persons carry those names natively).
    All ENTD attribute columns (``employed``, ``has_license``, …) are retained so
    ``EntdSource.map_person_attributes`` can read them after expand.

    This is the donor-side counterpart of the seed transform in ``build_seed``;
    without it ``expand_to_persons`` raises ``KeyError: 'H_ID'`` because the raw
    ENTD donor still carries ``household_id`` / ``person_id`` / ``age`` / ``sex``.
    """
    out = persons.rename(columns={
        "household_id": "H_ID",
        "person_id": "P_ID",
        "age": "HP_ALTER",
    })
    sex_map = {"male": 1, "female": 2}
    unmapped = set(out["sex"].unique()) - set(sex_map)
    if unmapped:
        raise ValueError(
            f"[EntdSource] donor persons 'sex' has unmapped value(s) {unmapped!r}; "
            "only 'male'/'female' are accepted."
        )
    out["HP_SEX"] = out["sex"].map(sex_map)
    return out
