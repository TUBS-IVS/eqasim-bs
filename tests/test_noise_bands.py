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


# ---------------------------------------------------------------------------
# Task 4: run_mid_validation --noise-bands integration
# ---------------------------------------------------------------------------


def test_annotate_with_bands_adds_columns_and_leaves_unbanded_tables():
    tables = {"p13": pd.DataFrame({"metric_id": ["p13_emd"], "group": ["03101"],
                                    "value": [0.05]}),
              "other": pd.DataFrame({"foo": [1]})}
    bands = pd.DataFrame({"metric_id": ["p13_emd"], "group": ["03101"],
                          "mean": [0.05], "q05": [0.02], "q95": [0.10], "n_draws": [20]})
    from braunschweig.analysis.run_mid_validation import annotate_with_bands
    out = annotate_with_bands(tables, bands)
    assert "within_noise_band" in out["p13"].columns
    assert "within_noise_band" not in out["other"].columns


def test_annotate_with_bands_returns_new_dict_and_does_not_mutate_inputs():
    from braunschweig.analysis.run_mid_validation import annotate_with_bands

    tables = {"other": pd.DataFrame({"foo": [1]})}
    bands = pd.DataFrame({"metric_id": [], "group": [], "mean": [], "q05": [],
                          "q95": [], "n_draws": []})
    out = annotate_with_bands(tables, bands)
    assert out is not tables
    assert list(out["other"].columns) == ["foo"]


def _noise_band_csv(tmp_path, *, metric_id="commute_mean_km_delta_km", group="03101",
                    mean=0.1, q05=-0.5, q95=0.5, n_draws=20, sampling_rate=0.01,
                    pipeline_commit="abc1234", created_utc="2026-07-09T00:00:00+00:00"):
    path = tmp_path / "noise_bands.csv"
    pd.DataFrame({
        "metric_id": [metric_id], "group": [group], "mean": [mean],
        "q05": [q05], "q95": [q95], "n_draws": [n_draws],
        "sampling_rate": [sampling_rate], "pipeline_commit": [pipeline_commit],
        "created_utc": [created_utc],
    }).to_csv(path, index=False)
    return path


def test_load_noise_bands_reads_valid_csv_and_keeps_group_as_string(tmp_path):
    from braunschweig.analysis.run_mid_validation import _load_noise_bands

    path = _noise_band_csv(tmp_path)
    bands = _load_noise_bands(path)
    assert bands.loc[0, "group"] == "03101"
    assert isinstance(bands.loc[0, "group"], str)


def test_load_noise_bands_raises_on_missing_columns(tmp_path):
    from braunschweig.analysis.run_mid_validation import _load_noise_bands

    path = tmp_path / "bad_bands.csv"
    pd.DataFrame({"metric_id": ["x"], "group": ["y"]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="sampling_rate"):
        _load_noise_bands(path)


def _minimal_mid_report():
    # Mirrors the shape of run_mid_validation.run()'s result dict, restricted
    # to the keys _flatten_mid_report reads (see noise_bands.MID_METRIC_IDS).
    return {
        "commute_mean_km_synth": {"03101": 12.3},
        "commute_mean_km_mid": {"03101": 12.2},
        "license_pct_synth": {}, "license_pct_mid": {},
        "employment_pct_synth": {}, "employment_pct_mid": {},
        "education_distance_vs_t43": [],
    }


def test_apply_noise_bands_is_noop_when_path_is_none(tmp_path, capsys):
    from braunschweig.analysis.run_mid_validation import _apply_noise_bands

    report = _minimal_mid_report()
    out_report = _apply_noise_bands(report, None, tmp_path)
    assert out_report is report
    assert not list(tmp_path.glob("noise_band_annotated_metrics.csv"))
    assert capsys.readouterr().out == ""


def test_apply_noise_bands_annotates_writes_csv_and_prints_note(tmp_path, capsys):
    from braunschweig.analysis.run_mid_validation import _apply_noise_bands

    report = _minimal_mid_report()
    bands_path = _noise_band_csv(
        tmp_path, metric_id="commute_mean_km_delta_km", group="03101",
        q05=-0.5, q95=0.5, sampling_rate=0.01, n_draws=20, pipeline_commit="abc1234",
    )
    out_report = _apply_noise_bands(report, bands_path, tmp_path)

    assert "noise_band_annotated_metrics" in out_report
    rows = out_report["noise_band_annotated_metrics"]
    assert len(rows) == 1
    assert rows[0]["within_noise_band"] is True

    written = pd.read_csv(tmp_path / "noise_band_annotated_metrics.csv")
    assert "within_noise_band" in written.columns

    printed = capsys.readouterr().out
    assert "sampling_rate=0.01" in printed
    assert "N=20" in printed
    assert "commit abc1234" in printed
    assert "indistinguishable from sampling noise" in printed
    assert "not a significance test" in printed


def test_apply_noise_bands_report_is_json_serialisable_when_band_coverage_is_partial(tmp_path):
    # The band file only covers "commute_mean_km_delta_km"; the report also
    # carries a license_pct_delta_pp metric with no matching band row, so
    # merge_bands_into_report leaves that row's within_noise_band as
    # pandas' nullable pd.NA -- json.dumps must not choke on it (see
    # _apply_noise_bands).
    import json

    from braunschweig.analysis.run_mid_validation import _apply_noise_bands

    report = _minimal_mid_report()
    report["license_pct_synth"] = {"03101": 80.0}
    report["license_pct_mid"] = {"03101": 78.0}
    bands_path = _noise_band_csv(tmp_path, metric_id="commute_mean_km_delta_km", group="03101")

    out_report = _apply_noise_bands(report, bands_path, tmp_path)
    json.dumps(out_report)  # must not raise TypeError on pd.NA

    rows = {row["metric_id"]: row for row in out_report["noise_band_annotated_metrics"]}
    assert rows["commute_mean_km_delta_km"]["within_noise_band"] in (True, False)
    assert rows["license_pct_delta_pp"]["within_noise_band"] is None


class TestNoiseBandsArgParser:
    def test_defaults_to_none(self, tmp_path):
        from braunschweig.analysis.run_mid_validation import _parse_args

        out = tmp_path / "run"
        out.mkdir()
        (out / "demo_persons.csv").write_text("person_id\n1\n", encoding="utf-8")
        args = _parse_args(["--output-dir", str(out)])
        assert args.noise_bands is None

    def test_accepts_existing_csv(self, tmp_path):
        from braunschweig.analysis.run_mid_validation import _parse_args

        out = tmp_path / "run"
        out.mkdir()
        (out / "demo_persons.csv").write_text("person_id\n1\n", encoding="utf-8")
        bands_path = _noise_band_csv(tmp_path)
        args = _parse_args(["--output-dir", str(out), "--noise-bands", str(bands_path)])
        assert args.noise_bands == bands_path.resolve()

    def test_errors_on_missing_csv(self, tmp_path):
        from braunschweig.analysis.run_mid_validation import _parse_args

        out = tmp_path / "run"
        out.mkdir()
        (out / "demo_persons.csv").write_text("person_id\n1\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            _parse_args(["--output-dir", str(out), "--noise-bands",
                        str(tmp_path / "nope.csv")])
