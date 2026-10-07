import pandas as pd
import pytest

from braunschweig.data.vrb import line_scopes as ls


def _cleaned(tmp_path, routes, agencies):
    out = tmp_path / "output"
    out.mkdir()
    pd.DataFrame(routes).to_csv(out / "routes.txt", index=False)
    pd.DataFrame(agencies).to_csv(out / "agency.txt", index=False)
    return out


def test_scope_and_mode_class_follow_route_type_and_agency(tmp_path):
    out = _cleaned(tmp_path,
                   [{"route_id": "ice-1", "agency_id": "dbf", "route_type": 101, "route_short_name": "ICE 1"},
                    {"route_id": "re-2", "agency_id": "dbr", "route_type": 106, "route_short_name": "RE 2"},
                    {"route_id": "bus-3", "agency_id": "kvg", "route_type": 700, "route_short_name": "M1"},
                    {"route_id": "tram-4", "agency_id": "bsvg", "route_type": 900, "route_short_name": "1"},
                    {"route_id": "flix-5", "agency_id": "flx", "route_type": 200, "route_short_name": "FLX"},
                    {"route_id": "plain-6", "agency_id": "kvg", "route_type": 3, "route_short_name": "420"}],
                   [{"agency_id": "dbf", "agency_name": "DB Fernverkehr AG"},
                    {"agency_id": "dbr", "agency_name": "DB Regio AG"},
                    {"agency_id": "kvg", "agency_name": "KVG Braunschweig"},
                    {"agency_id": "bsvg", "agency_name": "BSVG"},
                    {"agency_id": "flx", "agency_name": "FlixBus DACH GmbH"}])
    frame = ls.build_line_scopes(out)
    rows = frame.set_index("line_id")
    assert rows.loc["ice-1", "tariff_scope"] == "long_distance" and rows.loc["ice-1", "mode_class"] == "rail"
    assert rows.loc["re-2", "tariff_scope"] == "regional" and rows.loc["re-2", "mode_class"] == "rail"
    assert rows.loc["bus-3", "mode_class"] == "bus" and rows.loc["tram-4", "mode_class"] == "tram"
    assert rows.loc["flix-5", "tariff_scope"] == "long_distance"   # agency pattern, not the route type
    assert rows.loc["plain-6", "mode_class"] == "bus" and rows.loc["plain-6", "tariff_scope"] == "regional"
    assert list(frame.columns) == ["line_id", "agency_id", "agency_name", "route_type", "mode_class", "tariff_scope"]


def test_unknown_route_type_is_reported_not_guessed(tmp_path):
    out = _cleaned(tmp_path, [{"route_id": "x", "agency_id": "a", "route_type": 9999, "route_short_name": "?"}],
                   [{"agency_id": "a", "agency_name": "A"}])
    frame = ls.build_line_scopes(out)
    assert frame.loc[0, "mode_class"] == "other" and frame.loc[0, "tariff_scope"] == "regional"


def test_missing_agency_row_fails_early(tmp_path):
    out = _cleaned(tmp_path, [{"route_id": "x", "agency_id": "ghost", "route_type": 3, "route_short_name": "1"}],
                   [{"agency_id": "a", "agency_name": "A"}])
    with pytest.raises(ValueError, match="agency_id 'ghost'"):
        ls.build_line_scopes(out)


def test_csv_round_trip_keeps_columns(tmp_path):
    out = _cleaned(tmp_path, [{"route_id": "x", "agency_id": "a", "route_type": 3, "route_short_name": "1"}],
                   [{"agency_id": "a", "agency_name": "A"}])
    path = tmp_path / "vrb_line_scopes.csv"
    ls.write_line_scopes(ls.build_line_scopes(out), path)
    again = pd.read_csv(path, dtype=str)
    assert list(again.columns) == ["line_id", "agency_id", "agency_name", "route_type", "mode_class", "tariff_scope"]
