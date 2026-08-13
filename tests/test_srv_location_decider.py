"""Pinned SrV-2023 location-type probability loader + per-leg decider
(issue #262, Task 7)."""
import pathlib

import pytest

from braunschweig.synthesis.locations import secondary_chainsolvers as sc


class _Ctx:
    """Minimal synpp ExecuteContext stub. ``config(self, key)`` takes the key
    alone, mirroring ``synpp.pipeline.ExecuteContext.config`` (declared
    options only, no default parameter) -- see
    tests/test_execute_context_config_contract.py for the two-argument crash
    this avoids. Tests must supply every config key the decider under test
    actually reads."""
    def __init__(self, cfg):
        self._cfg = cfg

    def config(self, key):
        if key not in self._cfg:
            raise KeyError(
                f"_Ctx: no value for config key {key!r} -- declared-config "
                "semantics require the test to supply it explicitly."
            )
        return self._cfg[key]


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

_BASIC_CSV = """\
# Source: synthetic test fixture for load_srv_location_type_probs (#262).
# Band edges (euclidean km): [0.0, 1.0, 2.0].
purpose,mode,band_lower_km,band_upper_km,is_marginal,category,probability,n_legs_unweighted
leisure,all,0.0,inf,1,leisure_culture,0.5,10
leisure,all,0.0,inf,1,leisure_outdoor,0.5,10
other,all,0.0,inf,1,errand_service,1.0,10
leisure,car,0.0,1.0,0,leisure_culture,1.0,50
leisure,car,1.0,2.0,0,leisure_outdoor,1.0,50
"""

_UNKNOWN_CATEGORY_CSV = """\
# Synthetic fixture with an invalid category for the leisure purpose.
purpose,mode,band_lower_km,band_upper_km,is_marginal,category,probability,n_legs_unweighted
leisure,all,0.0,inf,1,leisure_culture,0.5,10
leisure,all,0.0,inf,1,leisure_alien,0.5,10
other,all,0.0,inf,1,errand_service,1.0,10
leisure,car,0.0,1.0,0,leisure_culture,1.0,50
"""

_BAD_SUM_CSV = """\
# Synthetic fixture whose leisure marginal does not sum to 1.
purpose,mode,band_lower_km,band_upper_km,is_marginal,category,probability,n_legs_unweighted
leisure,all,0.0,inf,1,leisure_culture,0.5,10
leisure,all,0.0,inf,1,leisure_outdoor,0.3,10
other,all,0.0,inf,1,errand_service,1.0,10
leisure,car,0.0,1.0,0,leisure_culture,1.0,50
"""

_MISSING_MARGINAL_CSV = """\
# Synthetic fixture missing a marginal row set for the "other" purpose.
purpose,mode,band_lower_km,band_upper_km,is_marginal,category,probability,n_legs_unweighted
leisure,all,0.0,inf,1,leisure_culture,0.5,10
leisure,all,0.0,inf,1,leisure_outdoor,0.5,10
leisure,car,0.0,1.0,0,leisure_culture,1.0,50
other,car,0.0,1.0,0,errand_service,1.0,50
"""

_MISSING_COLUMN_CSV = """\
# Synthetic fixture missing the "mode" column entirely.
purpose,band_lower_km,band_upper_km,is_marginal,category,probability,n_legs_unweighted
leisure,0.0,inf,1,leisure_culture,1.0,10
"""


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# load_srv_location_type_probs
# ---------------------------------------------------------------------------

def test_loader_round_trip(tmp_path):
    path = _write(tmp_path, "basic.csv", _BASIC_CSV)
    tables = sc.load_srv_location_type_probs(path)

    assert set(tables) == {"leisure", "other"}
    assert tables["leisure"]["band_edges_km"] == (0.0, 1.0, 2.0)
    assert tables["other"]["band_edges_km"] == (0.0, 1.0, 2.0)

    assert tables["leisure"]["marginal"] == {"leisure_culture": 0.5, "leisure_outdoor": 0.5}
    assert tables["other"]["marginal"] == {"errand_service": 1.0}

    assert tables["leisure"]["cells"] == {
        ("car", 0): {"leisure_culture": 1.0},
        ("car", 1): {"leisure_outdoor": 1.0},
    }
    # "other" has no non-marginal rows of its own -- it must still get the
    # shared band edges reconstructed from leisure's rows, with an empty
    # cell table (every draw falls back to the marginal).
    assert tables["other"]["cells"] == {}


def test_loader_missing_column_raises(tmp_path):
    path = _write(tmp_path, "missing_column.csv", _MISSING_COLUMN_CSV)
    with pytest.raises(ValueError, match="missing required column"):
        sc.load_srv_location_type_probs(path)


def test_loader_unknown_category_raises(tmp_path):
    path = _write(tmp_path, "unknown_category.csv", _UNKNOWN_CATEGORY_CSV)
    with pytest.raises(ValueError, match="unknown"):
        sc.load_srv_location_type_probs(path)


def test_loader_probability_sum_violation_raises(tmp_path):
    path = _write(tmp_path, "bad_sum.csv", _BAD_SUM_CSV)
    with pytest.raises(ValueError, match="sum to"):
        sc.load_srv_location_type_probs(path)


def test_loader_missing_marginal_raises(tmp_path):
    path = _write(tmp_path, "missing_marginal.csv", _MISSING_MARGINAL_CSV)
    with pytest.raises(ValueError, match="missing marginal"):
        sc.load_srv_location_type_probs(path)


def test_loader_real_pinned_csv():
    # Task 1's committed, real derive-script output -- must load cleanly with
    # both purposes present, every cell/marginal summing to 1, and every
    # category drawn from the known vocabulary.
    csv_path = pathlib.Path(__file__).resolve().parents[1] / "eqasim-data" / "data" \
        / "braunschweig" / "srv" / "srv2023_location_type_by_distance.csv"
    tables = sc.load_srv_location_type_probs(str(csv_path))

    assert set(tables) == {"leisure", "other"}
    allowed = {
        "leisure": set(sc.SRV_LEISURE_CATEGORIES),
        "other": set(sc.SRV_OTHER_CATEGORIES),
    }
    for purpose, table in tables.items():
        assert sum(table["marginal"].values()) == pytest.approx(1.0, abs=1e-6)
        assert set(table["marginal"]) <= allowed[purpose]
        for (mode, band_idx), probs in table["cells"].items():
            assert sum(probs.values()) == pytest.approx(1.0, abs=1e-6)
            assert set(probs) <= allowed[purpose]
        edges = table["band_edges_km"]
        assert all(a < b for a, b in zip(edges, edges[1:]))


# ---------------------------------------------------------------------------
# _build_srv_location_decider
# ---------------------------------------------------------------------------

def test_decider_off_returns_none():
    ctx = _Ctx({"secondary_srv_location_types": False})
    assert sc._build_srv_location_decider(ctx, random_seed=1) is None


def test_decider_determinism(tmp_path):
    path = _write(tmp_path, "basic.csv", _BASIC_CSV)
    cfg = {"secondary_srv_location_types": True, "srv_location_type_probs_path": path}

    decide_a = sc._build_srv_location_decider(_Ctx(cfg), random_seed=42)
    decide_b = sc._build_srv_location_decider(_Ctx(cfg), random_seed=42)

    calls = [("leisure", "bike", 1500.0 + 37.0 * i) for i in range(20)]
    draws_a = [decide_a(*call) for call in calls]
    draws_b = [decide_b(*call) for call in calls]
    assert draws_a == draws_b  # same seed -> identical stream


def test_decider_marginal_fallback_flag(tmp_path):
    path = _write(tmp_path, "basic.csv", _BASIC_CSV)
    cfg = {"secondary_srv_location_types": True, "srv_location_type_probs_path": path}
    decide = sc._build_srv_location_decider(_Ctx(cfg), random_seed=1)

    # mode "car" has a cell for band 0 -> the primary path, not the fallback.
    category, used_marginal = decide("leisure", "car", 200.0)
    assert category == "leisure_culture"
    assert used_marginal is False

    # mode "bike" has no cell at all -> falls back to the leisure marginal.
    category, used_marginal = decide("leisure", "bike", 200.0)
    assert used_marginal is True
    assert category in {"leisure_culture", "leisure_outdoor"}

    # "other" purpose has no cells whatsoever in this fixture -> always the
    # marginal fallback, even for a mode that does appear in "leisure"'s cells.
    category, used_marginal = decide("other", "car", 200.0)
    assert used_marginal is True
    assert category == "errand_service"


def test_decider_edge_boundary_goes_to_upper_band(tmp_path):
    path = _write(tmp_path, "basic.csv", _BASIC_CSV)
    cfg = {"secondary_srv_location_types": True, "srv_location_type_probs_path": path}
    decide = sc._build_srv_location_decider(_Ctx(cfg), random_seed=1)

    # Band 0 = [0, 1) km -> leisure_culture=1.0; band 1 = [1, 2) km ->
    # leisure_outdoor=1.0. A distance exactly on the 1.0 km edge must resolve
    # to the UPPER band (side="right"), i.e. leisure_outdoor, deterministically
    # (probability 1.0 in that band regardless of the draw).
    category, used_marginal = decide("leisure", "car", 1000.0)
    assert category == "leisure_outdoor"
    assert used_marginal is False

    # Just below the edge stays in the lower band.
    category, used_marginal = decide("leisure", "car", 999.0)
    assert category == "leisure_culture"
    assert used_marginal is False


def test_decider_unknown_purpose_raises(tmp_path):
    path = _write(tmp_path, "basic.csv", _BASIC_CSV)
    cfg = {"secondary_srv_location_types": True, "srv_location_type_probs_path": path}
    decide = sc._build_srv_location_decider(_Ctx(cfg), random_seed=1)

    with pytest.raises(ValueError, match="unknown purpose"):
        decide("escort", "car", 500.0)
