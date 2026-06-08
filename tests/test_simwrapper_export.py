from pathlib import Path

import pytest
import yaml

from braunschweig.analysis.simwrapper import export as ex


@pytest.fixture
def record() -> dict:
    """Minimal record matching build_dashboard.assemble_run_record output."""
    return {
        "run_id": "20260101_000000_test",
        "label": "test",
        "created_at": "2026-01-01T00:00:00",
        "sample_rate": 0.25,
        "eqasim": {
            "available": True, "n_persons": 1000, "n_households": 500,
            "n_trips": 3000, "trips_per_person": 3.0, "mean_age": 43.2,
            "share_employed_pct": 50.1, "share_license_pct": 80.0,
            "share_pt_sub_pct": 30.0, "share_female_pct": 51.0,
            "share_urban_pct": 60.0,
            "trip_purpose_pct": {"work": 20.0, "home": 40.0, "leisure": 40.0},
        },
        "matsim": {
            "available": True, "modes": ["car", "pt", "bicycle", "walk"],
            "last_iteration": 9, "terminated_early": True,
            "mean_trip_km": 9.4, "median_trip_km": 5.1, "score_final": 123.4,
            "avg_trip_km_final": 9.4, "avg_leg_km_final": 4.2,
            "mode_share_pct_final": {"car": 55.0, "pt": 15.0, "bicycle": 18.0, "walk": 12.0},
            "mean_km_by_mode": {"car": 12.0, "pt": 14.0, "bicycle": 3.0, "walk": 1.0},
            "mode_share_evolution": {
                "iterations": [0, 1, 2],
                "car": [60.0, 57.0, 55.0], "pt": [13.0, 14.0, 15.0],
                "bicycle": [16.0, 17.0, 18.0], "walk": [11.0, 12.0, 12.0],
            },
            "score_evolution": {
                "iterations": [0, 1, 2], "avg_executed": [100.0, 115.0, 123.4],
                "avg_best": [101.0, 116.0, 124.0], "avg_worst": [90.0, 110.0, 120.0],
            },
            "distance_evolution": {
                "iterations": [0, 1, 2], "avg_leg_km": [4.0, 4.1, 4.2],
                "avg_trip_km": [9.0, 9.2, 9.4],
            },
            "time_of_day": {
                "hours": list(range(24)),
                "by_mode": {"car": [0] * 24, "pt": [0] * 24},
                "by_purpose": {"work": [0] * 24},
                "total_per_hour": [10] * 24,
            },
            "per_kreis_sim": {
                "03101": {"name": "Braunschweig", "n_trips": 100, "mean_km": 8.0,
                          "median_km": 5.0, "mode_share_pct": {"car": 50.0, "pt": 20.0}},
            },
            "od_matrix": {
                "zones": ["03101", "03102", "external"],
                "zone_names": ["Braunschweig", "Salzgitter", "Outside ZGB"],
                "purposes": ["work", "home"],
                "matrices": {
                    "work": [[10, 2, 1], [3, 8, 0], [1, 0, 0]],
                    "home": [[9, 1, 0], [2, 7, 1], [0, 1, 0]],
                },
            },
        },
        "mid_reference": {
            "available": True, "p13_mean_km_zgb": 11.0,
            "p13_distance_pct_zgb": {"0-5": 30.0, "5-10": 20.0, "10-20": 20.0,
                                      "20-30": 10.0, "30-50": 10.0, "50-100": 7.0, "100+": 3.0},
            "p12_modal_split_zgb": {"Car": 58.0, "PT": 14.0, "Bicycle": 16.0, "Walk": 12.0},
            "p13_per_kreis": [{"ars5": "03101", "name": "Braunschweig", "mean_km": 9.5, "n_weighted": 1.0}],
            "p12_per_kreis": [{"ars5": "03101", "name": "Braunschweig", "auto": 55.0,
                                "oeffentlich": 18.0, "fahrrad": 15.0, "zu_fuss": 12.0}],
        },
        "comparisons": {
            "commute_mean_km": {"sim": 10.2, "mid": 11.0, "diff_km": -0.8, "diff_pct": -7.3},
            "distance_distribution": {
                "bands": ["0-5", "5-10", "10-20", "20-30", "30-50", "50-100", "100+"],
                "sim_pct": [32.0, 19.0, 21.0, 9.0, 9.0, 7.0, 3.0],
                "mid_pct": [30.0, 20.0, 20.0, 10.0, 10.0, 7.0, 3.0],
                "emd": 0.03, "tolerance": 0.08, "ok": True,
            },
            "work_mode_share": {
                "modes": ["Car", "PT", "Bicycle", "Walk"],
                "sim_pct": [55.0, 15.0, 18.0, 12.0],
                "mid_pct": [58.0, 14.0, 16.0, 12.0],
                "note": "MiD P12_1 reports any-mode-used.",
            },
        },
    }


def test_export_run_writes_tabs(tmp_path: Path, record: dict):
    paths = ex.export_run(record, tmp_path)
    yamls = sorted(p.name for p in tmp_path.glob("dashboard-*.yaml"))
    assert len(yamls) >= 8
    for y in tmp_path.glob("dashboard-*.yaml"):
        doc = yaml.safe_load(y.read_text(encoding="utf-8"))
        assert "header" in doc and "layout" in doc
    for y in tmp_path.glob("dashboard-*.yaml"):
        doc = yaml.safe_load(y.read_text(encoding="utf-8"))
        for row in doc["layout"].values():
            for card in row:
                if "dataset" in card:
                    assert (tmp_path / card["dataset"]).exists(), card["dataset"]


def test_emit_overview(tmp_path, record):
    from braunschweig.analysis.simwrapper import export as ex
    board = ex.emit_overview(record, tmp_path)
    assert board["header"]["tab"] == "Overview"
    assert (tmp_path / "overview_kpis.csv").exists()
    import pandas as pd
    df = pd.read_csv(tmp_path / "overview_kpis.csv")
    assert {"metric", "value"}.issubset(df.columns)
    metrics = set(df["metric"])
    assert "Persons" in metrics and "Mean commute (km)" in metrics


def test_emit_mode_share(tmp_path, record):
    from braunschweig.analysis.simwrapper import export as ex
    import pandas as pd
    board = ex.emit_mode_share(record, tmp_path)
    assert board["header"]["tab"] == "Mode share"
    final = pd.read_csv(tmp_path / "mode_share_final.csv")
    assert {"mode", "share_pct"}.issubset(final.columns)
    cmp = pd.read_csv(tmp_path / "mode_share_commute_vs_mid.csv")
    assert {"mode", "sim_pct", "mid_pct"}.issubset(cmp.columns)
    evo = pd.read_csv(tmp_path / "mode_share_evolution.csv")
    assert "iteration" in evo.columns and "car" in evo.columns
    types = {c["type"] for row in board["layout"].values() for c in row}
    assert {"bar", "line"}.issubset(types)


def test_emit_distances(tmp_path, record):
    from braunschweig.analysis.simwrapper import export as ex
    import pandas as pd
    board = ex.emit_distances(record, tmp_path)
    assert board["header"]["tab"] == "Distances"
    dist = pd.read_csv(tmp_path / "commute_distance_vs_mid.csv")
    assert {"band", "sim_pct", "mid_pct"}.issubset(dist.columns)
    mk = pd.read_csv(tmp_path / "mean_km_by_mode.csv")
    assert {"mode", "mean_km"}.issubset(mk.columns)


def test_emit_time_of_day(tmp_path, record):
    from braunschweig.analysis.simwrapper import export as ex
    import pandas as pd
    board = ex.emit_time_of_day(record, tmp_path)
    assert board["header"]["tab"] == "Time of day"
    df = pd.read_csv(tmp_path / "trips_by_hour_mode.csv")
    assert "hour" in df.columns and len(df) == 24


def test_emit_convergence(tmp_path, record):
    from braunschweig.analysis.simwrapper import export as ex
    import pandas as pd
    board = ex.emit_convergence(record, tmp_path)
    assert board["header"]["tab"] == "Convergence"
    sc = pd.read_csv(tmp_path / "score_evolution.csv")
    assert {"iteration", "avg_executed"}.issubset(sc.columns)
    di = pd.read_csv(tmp_path / "distance_evolution.csv")
    assert {"iteration", "avg_trip_km"}.issubset(di.columns)


def test_emit_quality(tmp_path, record):
    from braunschweig.analysis.simwrapper import export as ex
    import pandas as pd
    board = ex.emit_quality(record, tmp_path)
    assert board["header"]["tab"] == "Quality"
    df = pd.read_csv(tmp_path / "quality_checks.csv")
    assert {"check", "value", "threshold", "status"}.issubset(df.columns)


def test_emit_od_table(tmp_path, record):
    from braunschweig.analysis.simwrapper import export as ex
    import pandas as pd
    board = ex.emit_od(record, tmp_path)
    assert board["header"]["tab"] == "OD"
    odf = pd.read_csv(tmp_path / "od_flows.csv", sep=";")
    assert list(odf.columns)[:2] == ["origin", "destination"]
    assert "work" in odf.columns and "home" in odf.columns
    assert (tmp_path / "od_matrix_long.csv").exists()


def test_emit_od_spider_real(tmp_path, record):
    """Primary-path test: with real VG250 geometry the aggregate-od spider
    must actually be produced (no silent fallback to table-only)."""
    import pytest
    from braunschweig.analysis.simwrapper import export as ex
    from braunschweig.analysis.dashboard.build_dashboard import _load_zgb_kreise
    if _load_zgb_kreise() is None:
        pytest.skip("VG250 geometry not available in this environment")
    board = ex.emit_od(record, tmp_path)
    assert (tmp_path / "zones.shp").exists()
    card_types = {c["type"] for row in board["layout"].values() for c in row}
    assert "aggregate-od" in card_types


def test_emit_per_kreis(tmp_path, record):
    from braunschweig.analysis.simwrapper import export as ex
    import pandas as pd
    board = ex.emit_per_kreis(record, tmp_path)
    assert board["header"]["tab"] == "Per-Kreis"
    df = pd.read_csv(tmp_path / "per_kreis_sim.csv")
    assert {"ars5", "name", "mean_km", "n_trips"}.issubset(df.columns)
    types = {c["type"] for row in board["layout"].values() for c in row}
    assert "csv" in types and "bar" in types
