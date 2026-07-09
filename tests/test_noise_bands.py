"""Issue #126: aggregation + band-merge for Monte-Carlo sampling-noise bands."""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.analysis import noise_bands as nb


def _draw(seed, values):
    return pd.DataFrame({
        "draw_seed": seed,
        "metric_id": ["p13_emd", "p13_emd"],
        "group": ["03101", "03102"],
        "value": values,
    })


def test_aggregate_mean_q05_q95():
    frames = [_draw(s, [v, v + 1.0]) for s, v in zip(range(20), range(20))]
    out = nb.aggregate_draw_metrics(frames)
    row = out[(out["metric_id"] == "p13_emd") & (out["group"] == "03101")].iloc[0]
    assert row["mean"] == pytest.approx(9.5)
    assert row["q05"] == pytest.approx(pd.Series(range(20), dtype=float).quantile(0.05))
    assert row["q95"] == pytest.approx(pd.Series(range(20), dtype=float).quantile(0.95))
    assert row["n_draws"] == 20


def test_aggregate_raises_on_inconsistent_metric_sets():
    good = _draw(1, [0.1, 0.2])
    bad = good[good["group"] == "03101"].copy()
    bad["draw_seed"] = 2
    with pytest.raises(ValueError, match="metric"):
        nb.aggregate_draw_metrics([good, bad])


def test_merge_flags_within_and_outside():
    report = pd.DataFrame({
        "metric_id": ["p13_emd", "p13_emd", "unbanded"],
        "group": ["03101", "03102", "x"],
        "value": [0.05, 0.90, 1.0],
    })
    bands = pd.DataFrame({
        "metric_id": ["p13_emd", "p13_emd"], "group": ["03101", "03102"],
        "mean": [0.05, 0.05], "q05": [0.02, 0.02], "q95": [0.10, 0.10],
        "n_draws": [20, 20],
    })
    out = nb.merge_bands_into_report(report, bands)
    assert bool(out.loc[0, "within_noise_band"]) is True
    assert bool(out.loc[1, "within_noise_band"]) is False
    assert pd.isna(out.loc[2, "within_noise_band"])


def test_harvest_quality_summary(tmp_path):
    (tmp_path / "quality_summary.csv").write_text(
        "control,mean_abs_delta_pp,srmse\nhousehold_size,1.44,0.18\n",
        encoding="utf-8",
    )
    out = nb.harvest_draw_metrics(str(tmp_path), draw_seed=7, mid_validation=False)
    got = out.set_index("metric_id")["value"]
    assert got["control_household_size"] == pytest.approx(1.44)
    assert got["control_srmse_household_size"] == pytest.approx(0.18)
    assert (out["draw_seed"] == 7).all()


def test_harvest_asserts_expected_metrics(tmp_path):
    (tmp_path / "quality_summary.csv").write_text(
        "control,mean_abs_delta_pp,srmse\nhousehold_size,1.44,0.18\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected metric"):
        nb.harvest_draw_metrics(str(tmp_path), draw_seed=7, mid_validation=False,
                                expected_metric_ids={"control_cars"})
