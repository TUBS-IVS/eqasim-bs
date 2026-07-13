"""Tests for the categorical `employment_status` attribute (Phase-0 P9 taxonomy).

`employment_status` is derived directly from the MiD `P_BKAT` (Umfang der
Erwerbstaetigkeit) donor column via `EMPLOYMENT_STATUS_BY_P_BKAT`; code 6 IS
"in Ausbildung" and needs no `P_TAET` overlay. This mapping is grounded in the
MiD 2023 Codeplan B1 (Personen sheet) and was cross-checked against the raw
MiD2023_Personen.csv (P_BKAT vs `erwerb`), not invented.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.popsim import attributes as A


def _persons(**cols):
    return pd.DataFrame(cols)


def test_p_bkat_maps_to_all_seven_classes():
    # All seven substantive P_BKAT codes 1-7, incl. code 6 = in Ausbildung.
    persons = _persons(
        P_BKAT=[1, 2, 3, 4, 5, 6, 7],
        alter_gr1=[3, 3, 3, 3, 3, 3, 3],
    )
    out = A.map_employment_status(persons, rng=np.random.RandomState(0))
    assert list(out["employment_status"]) == [
        "vollzeit", "teilzeit", "geringfuegig", "sonstiges",
        "erwerbstaetig_unspec", "in_ausbildung", "nicht_erwerbstaetig",
    ]


def test_p_bkat_6_is_in_ausbildung_directly():
    # P_BKAT==6 IS "in Ausbildung" (codeplan + raw-data verified) -- no P_TAET overlay.
    persons = _persons(P_BKAT=[6], alter_gr1=[2])
    out = A.map_employment_status(persons, rng=np.random.RandomState(0))
    assert out["employment_status"].iloc[0] == "in_ausbildung"


def test_p_bkat_missing_code_9_is_imputed_not_raised():
    # 9 = keine Angabe must be imputed via the missing policy, never raise.
    persons = _persons(P_BKAT=[1, 2, 9], alter_gr1=[3, 3, 3])
    out = A.map_employment_status(persons, rng=np.random.RandomState(0))
    assert out["employment_status"].isin(A.EMPLOYMENT_STATUS_CATEGORIES).all()
