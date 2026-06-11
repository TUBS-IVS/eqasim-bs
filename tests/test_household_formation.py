"""Tests for household formation in ``braunschweig.ipf.attributed``.

These lock the "absorb the trailing remainder into the last household" policy:
no synthetic person is dropped when a (commune, hh_size) bucket does not divide
evenly into households of size N, so the synthesized population total stays equal
to the DESTATIS-anchored input. The realised ``household_size`` attribute reflects
actual membership (N for full households, N+k for the absorbing last household,
and <N for a bucket too small to fill a single household), and the downstream
hh_type binning tolerates realised sizes above the Zensus "6+" open bin.
"""
from __future__ import annotations

import pandas as pd

from braunschweig.ipf import attributed


def _form(commune_sizes_weights):
    df = pd.DataFrame(
        commune_sizes_weights, columns=["commune_id", "household_size", "weight"]
    )
    return attributed._form_households(df, random_seed=1)


def test_remainder_is_absorbed_not_dropped():
    # 7 persons in a size-3 bucket -> 2 households (3 + 4), nobody dropped.
    out = _form([["03101", "3", 7.0]])

    assert len(out) == 7
    assert out["household_id"].nunique() == 2
    sizes = sorted(out.groupby("household_id").size().tolist())
    assert sizes == [3, 4]
    # The household_size attribute equals the actual member count per household.
    member_counts = out.groupby("household_id").size()
    for hid, cnt in member_counts.items():
        members = out.loc[out["household_id"] == hid, "household_size"]
        assert (members == cnt).all()


def test_bucket_too_small_for_one_household_is_kept():
    # 4 persons assigned the "6+" size bin cannot fill a size-6 household; keep
    # them as a single realised size-4 household instead of dropping all four.
    out = _form([["03101", "6+", 4.0]])

    assert len(out) == 4
    assert out["household_id"].nunique() == 1
    assert (out["household_size"] == 4).all()


def test_population_total_preserved_across_buckets():
    out = _form([
        ["03101", "3", 7.0],
        ["03101", "2", 5.0],
        ["03102", "6+", 13.0],
    ])
    # 7 + 5 + 13 persons, none dropped (legacy chunking would have dropped 3).
    assert len(out) == 25
    # household_ids are globally unique and contiguous.
    assert out["household_id"].nunique() == out["household_id"].max() + 1


def test_formation_is_deterministic_for_fixed_seed():
    # Same input + same seed must yield a bit-identical household assignment
    # (the reproducibility property BUG-008 protects).
    spec = [["03101", "3", 7.0], ["03101", "2", 5.0], ["03102", "6+", 13.0]]
    a = attributed._form_households(
        pd.DataFrame(spec, columns=["commune_id", "household_size", "weight"]),
        random_seed=1,
    )
    b = attributed._form_households(
        pd.DataFrame(spec, columns=["commune_id", "household_size", "weight"]),
        random_seed=1,
    )
    pd.testing.assert_frame_equal(a, b)


def test_age_aware_chunking_produces_typed_plausible_households():
    # 2 size-4 households' worth: 4 adults + 4 children in one commune.
    df = pd.DataFrame({
        "commune_id": ["03101"] * 8,
        "household_size": ["4"] * 8,
        "weight": [1.0] * 8,
        "age": [40, 38, 8, 5, 34, 36, 6, 9],
    })
    df_type = pd.DataFrame({
        "commune_id": ["03101", "03101"],
        "hh_size": ["4", "4"],
        "hh_type": ["couple_with_children", "single_parent"],
        "weight": [1.0, 1.0],
    })
    cfg = dict(min_adult_age=18, couple_age_weight=1.0,
               parent_child_weight=1.0, parent_child_gap_years=31)
    out = attributed.form_households_age_aware(
        df, random_seed=1, df_household_type=df_type, cfg=cfg)

    assert len(out) == 8                       # nobody dropped
    assert "hh_type" in out.columns
    assert out["household_id"].nunique() == 2
    # No all-children household.
    for _, g in out.groupby("household_id"):
        assert (g["age"] >= 18).sum() >= 1
    # household_size attribute equals realised member count.
    for _, g in out.groupby("household_id"):
        assert (g["household_size"] == len(g)).all()


class _StubCtx:
    def __init__(self, cfg, stages):
        self._cfg = cfg
        self._stages = stages

    def config(self, key, default=None):
        return self._cfg.get(key, default)

    def stage(self, name):
        return self._stages[name]


def _model_frame():
    return pd.DataFrame({
        "commune_id": ["03101"] * 4,
        "departement_id": ["03101"] * 4,
        "sex": ["male", "female", "male", "female"],
        "age_class": [98, 98, 98, 98],
        "employed": [False] * 4,
        "license": [False] * 4,
        "weight": [1.0] * 4,
        "hh_size": ["2", "2", "2", "2"],
    })


def _base_cfg(**kw):
    cfg = {
        "braunschweig.ipf.use_household_size_margin": True,
        "braunschweig.ipf.use_household_type_margin": False,
        "braunschweig.ipf.age_aware_chunking": False,
        "random_seed": 1,
        "braunschweig.chunking.minimum_adult_age": 18,
        "braunschweig.chunking.couple_age_weight": 1.0,
        "braunschweig.chunking.parent_child_weight": 1.0,
        "braunschweig.chunking.parent_child_gap_years": 31.0,
        # Registered default in attributed.configure(); the stub context does
        # not honour registered defaults, so it must be provided explicitly.
        "matching_attributes": [],
    }
    cfg.update(kw)
    return cfg


def test_execute_uses_legacy_path_when_age_aware_off(monkeypatch):
    calls = []
    monkeypatch.setattr(attributed, "_form_households",
                        lambda df, seed: (calls.append("legacy"), df)[1])
    monkeypatch.setattr(attributed, "form_households_age_aware",
                        lambda *a, **k: (calls.append("age_aware"), a[0])[1])
    ctx = _StubCtx(_base_cfg(), {"braunschweig.ipf.model": _model_frame()})
    attributed.execute(ctx)
    assert calls == ["legacy"]   # flag OFF -> legacy formation, age-aware untouched


def test_execute_uses_age_aware_path_when_flag_on(monkeypatch):
    calls = []
    monkeypatch.setattr(attributed, "_form_households",
                        lambda df, seed: (calls.append("legacy"), df)[1])
    monkeypatch.setattr(attributed, "form_households_age_aware",
                        lambda *a, **k: (calls.append("age_aware"), a[0])[1])
    df_type = pd.DataFrame({"commune_id": ["03101"], "hh_size": ["2"],
                            "hh_type": ["couple"], "weight": [1.0]})
    ctx = _StubCtx(
        _base_cfg(**{"braunschweig.ipf.age_aware_chunking": True}),
        {"braunschweig.ipf.model": _model_frame(),
         "braunschweig.data.census.households_type": df_type},
    )
    attributed.execute(ctx)
    assert calls == ["age_aware"]   # flag ON -> age-aware path only


def test_age_aware_chunking_is_deterministic():
    df = pd.DataFrame({
        "commune_id": ["03101"] * 6,
        "household_size": ["3"] * 6,
        "weight": [1.0] * 6,
        "age": [40, 8, 5, 34, 7, 4],
    })
    df_type = pd.DataFrame({
        "commune_id": ["03101"], "hh_size": ["3"],
        "hh_type": ["single_parent"], "weight": [1.0],
    })
    cfg = dict(min_adult_age=18, parent_child_weight=1.0, parent_child_gap_years=31)
    a = attributed.form_households_age_aware(df, 1, df_type, cfg)
    b = attributed.form_households_age_aware(df, 1, df_type, cfg)
    pd.testing.assert_frame_equal(a, b)


def test_hh_type_binning_tolerates_oversized_household():
    # A realised size-7 household (6+ bucket with an absorbed remainder) must
    # bin to the Zensus "6+" open class, not raise.
    df_formed = pd.DataFrame({
        "household_id": [0] * 7,
        "commune_id": ["03101"] * 7,
        "household_size": [7] * 7,
    })
    df_ht = pd.DataFrame({
        "commune_id": ["03101"],
        "hh_size": ["6+"],
        "hh_type": ["other_multi"],
        "weight": [10.0],
    })

    out = attributed._assign_household_types(df_formed, df_ht, random_seed=1)

    assert (out["hh_type"] == "other_multi").all()


def test_type_share_index_matches_boolean_mask_row_order():
    """The pre-indexed ``(commune_id, hh_size)`` lookup must return sub-frames
    that are row-order-identical to the previous boolean-mask filter, including
    for buckets with no matching rows (empty -> same fallback). Row order matters
    because the sampler's RNG draw order depends on it; reordering would silently
    change the output. Several hh_type rows per bucket and several communes/sizes
    are included so a stable group order is actually exercised."""
    df_household_type = pd.DataFrame({
        "commune_id": [
            "03101", "03101", "03101",  # 03101 / size 1: three types
            "03101", "03101",           # 03101 / size 2: two types
            "03102", "03102",           # 03102 / size 1: two types
            "03102",                    # 03102 / size 3: one type
        ],
        "hh_size": [
            "1", "1", "1",
            "2", "2",
            "1", "1",
            "3",
        ],
        "hh_type": [
            "single", "couple", "other_multi",
            "couple", "single_parent",
            "single", "other_multi",
            "couple_with_children",
        ],
        "weight": [10.0, 5.0, 1.0, 7.0, 3.0, 4.0, 2.0, 9.0],
    })

    # Normalise types exactly as the production helper does, then build the index.
    df_zt, _fallback, index = attributed._prepare_type_shares(df_household_type)

    # Every present (commune, hh_size) bucket plus one absent bucket.
    buckets = [
        ("03101", "1"), ("03101", "2"),
        ("03102", "1"), ("03102", "3"),
        ("03101", "3"),  # absent: no matching rows in df_zt
        ("09999", "1"),  # absent: unknown commune
    ]

    for cid, hsb in buckets:
        expected = df_zt[(df_zt["commune_id"] == cid) & (df_zt["hh_size"] == hsb)]
        actual = index.get((cid, hsb), attributed._EMPTY_TYPE_SHARES)
        # Same number of rows, same values in the SAME order (RNG-relevant).
        assert list(actual["hh_type"]) == list(expected["hh_type"])
        assert list(actual["weight"]) == list(expected["weight"])
        assert float(actual["weight"].sum()) == float(expected["weight"].sum())
