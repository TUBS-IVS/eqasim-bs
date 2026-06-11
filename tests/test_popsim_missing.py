from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from braunschweig.popsim import missing as m
from braunschweig.popsim import missing


def test_classify_codes():
    assert m.classify_code(1, structural={202, 402}) == "valid_or_unknown"
    assert m.classify_code(99, structural={202, 402}) == "nonresponse"
    assert m.classify_code(402, structural={202, 402}) == "structural"


def test_resolve_structural_is_deterministic_and_nonresponse_imputed_and_logged():
    df = pd.DataFrame({
        "P_FSCHEIN": [1, 1, 2, 403, 9],
        "age_band":  ["a", "a", "a", "c", "a"],
    })
    spec = m.AttributeSpec(
        name="has_license",
        source_col="P_FSCHEIN",
        value_map={1: True, 2: False},
        structural={403: False, 202: False},
        group_cols=("age_band",),
    )
    out, report = m.resolve(df, spec, rng=np.random.RandomState(0))
    assert out.tolist()[:4] == [True, True, False, False]
    assert out.tolist()[4] in (True, False)
    assert report.n_structural == 1 and report.n_nonresponse == 1
    assert report.nonresponse_share == 1 / 5


def test_resolve_raises_on_unenumerated_code():
    """A code that is neither valid, structural, nor nonresponse must raise,
    not silently become NaN (which .astype(bool) would coerce to True)."""
    df = pd.DataFrame({"P_TAET": [1, 2, 17]})  # 17 is not in value_map 1..16
    spec = missing.AttributeSpec(
        name="employed",
        source_col="P_TAET",
        value_map={c: (c <= 7) for c in range(1, 17)},  # 1..16
        structural={},
    )
    with pytest.raises(ValueError, match=r"unenumerated.*17"):
        missing.resolve(df, spec, rng=np.random.RandomState(0))
