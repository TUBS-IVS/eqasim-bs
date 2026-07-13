"""Tests for the categorical `employment_status` attribute (Phase-0 P9 taxonomy).

`employment_status` is derived from the MiD `P_BKAT` (Umfang der Erwerbstaetigkeit)
donor column, with an Azubi overlay from `P_TAET == 8` (in Ausbildung) taking
precedence. Grounded in the MiD 2023 Codeplan B1 (Personen sheet), not invented.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.popsim import attributes as A


def _persons(**cols):
    return pd.DataFrame(cols)


def test_p_bkat_maps_to_seven_classes():
    persons = _persons(
        P_BKAT=[1, 2, 3, 4, 5, 7],
        P_TAET=[1, 2, 3, 4, 1, 12],
        alter_gr1=[3, 3, 3, 3, 3, 3],
    )
    out = A.map_employment_status(persons, rng=np.random.RandomState(0))
    assert list(out["employment_status"]) == [
        "vollzeit", "teilzeit", "geringfuegig", "sonstiges",
        "erwerbstaetig_unspec", "nicht_erwerbstaetig",
    ]


def test_azubi_overlay_takes_precedence_over_p_bkat():
    # P_TAET==8 (Azubi) must be in_ausbildung even though P_BKAT==1 (Vollzeit).
    persons = _persons(P_BKAT=[1], P_TAET=[8], alter_gr1=[2])
    out = A.map_employment_status(persons, rng=np.random.RandomState(0))
    assert out["employment_status"].iloc[0] == "in_ausbildung"
