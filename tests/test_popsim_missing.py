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


def _resolve_reference(df, spec, *, rng):
    """Pre-vectorisation reference implementation of missing.resolve.

    Kept verbatim (per-row classify_code map + per-nonresponse-row O(n_valid)
    equality mask) so the vectorised production implementation can be proven
    draw-for-draw identical on randomised input with the same seeded rng.
    """
    src = df[spec.source_col]
    structural_codes = set(spec.structural)
    nonresponse_set = m.NONRESPONSE_CODES | set(spec.impute_codes)
    klass = src.map(lambda c: m.classify_code(c, structural_codes, nonresponse_set))

    valid_codes = set(spec.value_map)
    is_valid = (klass == "valid_or_unknown") & src.isin(valid_codes)

    out = pd.Series(index=df.index, dtype=object)
    out[is_valid] = src[is_valid].map(spec.value_map)
    out[klass == "structural"] = src[klass == "structural"].map(spec.structural)

    valid_pool = out[is_valid]
    nonresp_idx = out.index[klass == "nonresponse"]
    for idx in nonresp_idx:
        pool = valid_pool
        if spec.group_cols:
            mask = pd.Series(True, index=valid_pool.index)
            for col in spec.group_cols:
                mask &= df.loc[valid_pool.index, col].values == df.at[idx, col]
            grouped = valid_pool[mask.values]
            if len(grouped) > 0:
                pool = grouped
        out.at[idx] = pool.iloc[rng.randint(len(pool))] if len(pool) > 0 else spec.default
    return out


def test_resolve_vectorised_is_draw_identical_to_reference():
    """The grouped-pool vectorisation must reproduce the old per-row loop
    draw-for-draw (same seeded rng -> same imputed values), including NaN
    group keys (-> global pool) and a group key absent from the valid pool."""
    rng_data = np.random.RandomState(42)
    n = 400
    codes = rng_data.choice([1, 1, 1, 2, 2, 403, 9, 99], size=n)
    groups = rng_data.choice(["a", "b", "c"], size=n).astype(object)
    # NaN group keys on some nonresponse rows must fall back to the global pool.
    groups[rng_data.choice(n, size=10, replace=False)] = np.nan
    df = pd.DataFrame({"P_FSCHEIN": codes, "age_band": groups})
    # Make one group key carry ONLY nonresponse codes (no valid donors): every
    # "d" row is nonresponse, so its group pool is empty -> global fallback.
    df.loc[df.index[:3], ["P_FSCHEIN", "age_band"]] = [[9, "d"], [99, "d"], [9, "d"]]
    spec = m.AttributeSpec(
        name="has_license",
        source_col="P_FSCHEIN",
        value_map={1: True, 2: False},
        structural={403: False},
        group_cols=("age_band",),
    )
    expected = _resolve_reference(df, spec, rng=np.random.RandomState(7))
    actual, _report = m.resolve(df, spec, rng=np.random.RandomState(7))
    pd.testing.assert_series_equal(actual, expected)


def test_resolve_vectorised_identical_without_group_cols():
    rng_data = np.random.RandomState(1)
    codes = rng_data.choice([1, 2, 9, 95, 403], size=200)
    df = pd.DataFrame({"P_FSCHEIN": codes})
    spec = m.AttributeSpec(
        name="has_license",
        source_col="P_FSCHEIN",
        value_map={1: True, 2: False},
        structural={403: False},
    )
    expected = _resolve_reference(df, spec, rng=np.random.RandomState(3))
    actual, _report = m.resolve(df, spec, rng=np.random.RandomState(3))
    pd.testing.assert_series_equal(actual, expected)


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
