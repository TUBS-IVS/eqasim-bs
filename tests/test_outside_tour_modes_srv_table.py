import pandas as pd
import pytest

from braunschweig.calibration import srv_mode_by_distance_band as M
from braunschweig.synthesis.outside_tour_modes.srv_table import ModeShareTable


def _frame():
    rows = []
    for group, bands in (("work", [(0.0, 5.0), (5.0, float("inf"))]), ("all", [(0.0, float("inf"))])):
        for lower, upper in bands:
            label = M.band_label(lower, upper)
            shares = {"walk": 0.3, "bicycle": 0.2, "car": 0.4, "car_passenger": 0.05, "pt": 0.05} if lower == 0.0 \
                else {"walk": 0.0, "bicycle": 0.005, "car": 0.8, "car_passenger": 0.1, "pt": 0.095}
            for mode in M.MODES:
                rows.append((group, label, lower, upper, mode, shares[mode], 100, int(shares[mode] * 100)))
    return pd.DataFrame(rows, columns=M.TABLE_COLUMNS)


def test_cell_returns_the_band_label_and_the_shares_of_the_band_containing_km():
    table = ModeShareTable.from_frame(_frame())
    label, shares = table.cell("work", 4.99)
    assert label == "0_5" and shares["walk"] == 0.3
    label, shares = table.cell("work", 5.0)
    assert label == "5_plus" and shares["walk"] == 0.0 and shares["car"] == 0.8
    label, shares = table.cell("work", 500.0)
    assert label == "5_plus"


def test_top_cell_is_the_open_band():
    table = ModeShareTable.from_frame(_frame())
    assert table.top_cell("all")[0] == "0_plus"


def test_unknown_group_and_negative_distance_raise():
    table = ModeShareTable.from_frame(_frame())
    with pytest.raises(KeyError, match="education"):
        table.cell("education", 1.0)
    with pytest.raises(ValueError, match="-1.0"):
        table.cell("work", -1.0)


def test_plausible_modes_filters_by_min_share_in_a_stable_order():
    table = ModeShareTable.from_frame(_frame())
    _, shares = table.cell("work", 10.0)
    assert ModeShareTable.plausible_modes(shares, 0.01) == ("car", "car_passenger", "pt")
    assert ModeShareTable.plausible_modes(shares, 0.1) == ("car", "car_passenger")


def test_assert_always_available_mode_plausible_raises_when_a_cell_has_neither_pt_nor_walk():
    frame = _frame()
    frame.loc[(frame["purpose_group"] == "work") & (frame["band"] == "5_plus") & (frame["mode"] == "pt"), "share"] = 0.0
    frame.loc[(frame["purpose_group"] == "work") & (frame["band"] == "5_plus") & (frame["mode"] == "car"), "share"] = 0.895
    table = ModeShareTable.from_frame(frame)
    with pytest.raises(ValueError, match="work/5_plus"):
        table.assert_always_available_mode_plausible(0.01)
    ModeShareTable.from_frame(_frame()).assert_always_available_mode_plausible(0.01)


def test_from_directory_reads_the_committed_layout(tmp_path):
    path = tmp_path / M.TABLE_FILE_NAME
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("# header\n")
        _frame().to_csv(handle, index=False)
    table = ModeShareTable.from_directory(tmp_path)
    assert table.groups == ("all", "work")
