"""Unit tests for the pure ``build_*_table`` functions in
``scripts/extract_srv_kreis_tables.py`` that are not otherwise covered by the
committed-CSV structural tests in ``test_srv_reference_tables.py``.

Covers the four-group PT-ticket table (issue #329): ``build_ticket_groups4_table``
splits the three-group ``not_flatrate`` collapse into ``never_pt`` (E_OEV_FK code
-8 only) and ``occasional_ticket`` (codes 1, 2, 70, 60).
"""
from __future__ import annotations

import pandas as pd
import pytest

from scripts.extract_srv_kreis_tables import build_ticket_groups4_table

# _iter_levels (scripts/extract_srv_kreis_tables.py) enumerates ALL 7 ZGB Kreise
# (EXPECTED_KREIS_CODES) and every ST_CODE/ST_CODE_NAME stratum present in the
# universe, regardless of whether the fixture actually has data for them; a
# Kreis with zero rows would give build_ticket_groups4_table a zero-weight
# group and a division by zero. The filler rows below give every OTHER Kreis
# exactly one valid, in-universe, known-code (E_OEV_FK == 1) respondent so the
# function can iterate all levels without crashing, while all of the actual
# test data lives in Braunschweig (03101), the one Kreis the assertions read.
_OTHER_KREIS_CODES = ["03102", "03151", "03153", "03154", "03157", "03158"]


def _filler_rows_for_other_kreise() -> pd.DataFrame:
    n = len(_OTHER_KREIS_CODES)
    return pd.DataFrame({
        "E_OEV_FK": [1] * n,
        "V_ALTER": [30] * n,
        "GEWICHT_P_ZENSUS": [1.0] * n,
        "ST_CODE": [173] * n,
        "ST_CODE_NAME": ["staedtisch"] * n,
        "kreis": _OTHER_KREIS_CODES,
    })


def test_build_ticket_groups4_table_splits_never_pt():
    # 6 persons aged >= 14 in Braunschweig (03101): codes 50 (D-Ticket), 3
    # (other flatrate), -8 (no PT use in 12 months -> never_pt), 1, 60, 70
    # (-> occasional_ticket); plus one under-14 respondent that must be
    # excluded from the universe entirely.
    braunschweig = pd.DataFrame({
        "E_OEV_FK": [50, 3, -8, 1, 60, 70, 1],
        "V_ALTER": [30, 30, 30, 30, 30, 30, 10],
        "GEWICHT_P_ZENSUS": [1.0] * 7,
        "ST_CODE": [173] * 7,
        "ST_CODE_NAME": ["staedtisch"] * 7,
        "kreis": ["03101"] * 7,
    })
    persons = pd.concat(
        [braunschweig, _filler_rows_for_other_kreise()], ignore_index=True)

    out = build_ticket_groups4_table(persons)
    kreis_row = out[(out["level"] == "kreis") & (out["code"] == "03101")].iloc[0]

    # The under-14 respondent is excluded: only 6 of the 7 Braunschweig rows count.
    # Shares are rounded to 4 decimals by build_ticket_groups4_table, so the
    # tolerance must exceed the rounding step, not just floating-point noise.
    assert int(kreis_row["n_unweighted"]) == 6
    assert abs(kreis_row["deutschlandticket"] - 1 / 6) < 1e-4
    assert abs(kreis_row["other_flatrate"] - 1 / 6) < 1e-4
    assert abs(kreis_row["never_pt"] - 1 / 6) < 1e-4
    assert abs(kreis_row["occasional_ticket"] - 3 / 6) < 1e-4


def test_build_ticket_groups4_table_raises_on_unmapped_code():
    persons = pd.DataFrame({
        "E_OEV_FK": [99], "V_ALTER": [30], "GEWICHT_P_ZENSUS": [1.0],
        "ST_CODE": [173], "ST_CODE_NAME": ["staedtisch"], "kreis": ["03101"],
    })
    with pytest.raises(RuntimeError):
        build_ticket_groups4_table(persons)
