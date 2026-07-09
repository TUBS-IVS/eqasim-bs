"""Aggregation + report-merge helpers for Monte-Carlo sampling-noise bands (issue #126).

A band quantifies how much a validation metric moves under pure re-seeding at a
FIXED sampling rate. It is a triage signal ("this deviation is indistinguishable
from sampling noise"), never a significance test and never a calibration input.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

LOGGER = logging.getLogger("braunschweig.analysis.noise_bands")

REQUIRED_DRAW_COLUMNS = ("draw_seed", "metric_id", "group", "value")

# Metric ids pinned from the ACTUAL braunschweig.analysis.run_mid_validation.run()
# result dict (see that module, function `run`, the block building `report =
# {...}` around line 975). The dict carries no Earth-Mover-Distance metrics --
# it reports per-Kreis MEAN deltas (synthetic - MiD), not distribution EMDs, so
# the ids below are named accordingly rather than invented to match an assumed
# "p13_emd"/"w12_emd"/"p36_dev" naming scheme:
#   - "commute_mean_km_synth" / "commute_mean_km_mid" (per ars5 Kreis)
#     -> "commute_mean_km_delta_km", group=ars5
#   - "license_pct_synth" / "license_pct_mid" (per ars5 Kreis, MiD P17.1)
#     -> "license_pct_delta_pp", group=ars5
#   - "employment_pct_synth" / "employment_pct_mid" (per ars5 Kreis, MiD P9)
#     -> "employment_pct_delta_pp", group=ars5
#   - "education_distance_vs_t43" (list of records with a precomputed
#     "delta_km" per RegioStaR-7 x school level, MiD Tabelle 43)
#     -> "education_distance_delta_km", group="<regiostar7>_<level>"
# "mode_share_commute_vs_p12_1" (work-commute modal split vs MiD P12_1) is
# flattened opportunistically when present, but deliberately excluded from
# MID_METRIC_IDS: it only gets populated by run_mid_validation.run() when a
# --sim-cache is given, and harvest_draw_metrics does not plumb one through
# (a Monte-Carlo synthesis-noise draw has no MATSim mobility-simulation
# output to read modes from), so requiring it would make the assertion below
# permanently unsatisfiable rather than catching a real regression.
MID_METRIC_IDS: tuple[str, ...] = (
    "commute_mean_km_delta_km",
    "license_pct_delta_pp",
    "employment_pct_delta_pp",
    "education_distance_delta_km",
)


def metric_keyset(frame: pd.DataFrame) -> frozenset:
    """Return the set of ``(metric_id, group)`` pairs carried by a draw frame.

    Shared by ``aggregate_draw_metrics`` below (the final backstop, run once
    all draws are already collected) and ``scripts/run_noise_bands.py``'s
    sweep loop (which compares each draw's keyset against the first
    successful draw's IMMEDIATELY after harvesting it, so an inconsistent
    draw is caught and dropped before its working directory is deleted,
    rather than only surfacing once aggregation runs after every workdir is
    already gone).
    """
    return frozenset(zip(frame["metric_id"], frame["group"]))


def aggregate_draw_metrics(frames: list) -> pd.DataFrame:
    if not frames:
        raise ValueError("[noise_bands] no draw frames to aggregate.")
    keysets = []
    for f in frames:
        missing = [c for c in REQUIRED_DRAW_COLUMNS if c not in f.columns]
        if missing:
            raise ValueError(f"[noise_bands] draw frame missing columns {missing}.")
        keysets.append(metric_keyset(f))
    if len(set(keysets)) != 1:
        raise ValueError(
            "[noise_bands] draws carry inconsistent metric/group sets -- a "
            "missing metric means the validation module changed mid-sweep; "
            "re-run the whole sweep on one code state."
        )
    df = pd.concat(frames, ignore_index=True)
    out = (
        df.groupby(["metric_id", "group"])["value"]
        .agg(mean="mean", q05=lambda x: x.quantile(0.05),
             q95=lambda x: x.quantile(0.95), n_draws="count")
        .reset_index()
    )
    return out


def merge_bands_into_report(report: pd.DataFrame, bands: pd.DataFrame, *,
                            metric_col: str = "metric_id", group_col: str = "group",
                            value_col: str = "value") -> pd.DataFrame:
    b = bands.rename(columns={"q05": "band_q05", "q95": "band_q95"})
    out = report.merge(
        b[[metric_col, group_col, "band_q05", "band_q95"]],
        on=[metric_col, group_col], how="left",
    )
    inside = (out[value_col] >= out["band_q05"]) & (out[value_col] <= out["band_q95"])
    out["within_noise_band"] = inside.astype("boolean")
    out.loc[out["band_q05"].isna(), "within_noise_band"] = pd.NA
    return out


# ---------------------------------------------------------------------------
# Per-draw metric harvest
# ---------------------------------------------------------------------------


def _assert_metrics_present(present_ids, expected_ids, *, context: str) -> None:
    """Raise a ValueError listing every id in ``expected_ids`` absent from
    ``present_ids``. Used both for the internal MID_METRIC_IDS pin and for the
    caller-supplied ``expected_metric_ids`` -- a missing metric almost always
    means an upstream stage silently produced nothing for it (see the
    "no silent fallbacks" project rule), so this fails loudly rather than
    returning a partial frame."""
    missing = sorted(set(expected_ids) - set(present_ids))
    if missing:
        raise ValueError(
            f"[noise_bands] {context}: missing expected metric id(s) {missing}."
        )


def _find_quality_summary(output_dir: Path) -> Path:
    """Locate the population-validation stage's ``quality_summary.csv`` under
    ``output_dir``. That stage (braunschweig.analysis.population_validation.
    run_population_validation.run) writes it to ``<source_path>/analysis/
    population_validation/quality_summary.csv`` by default, but the exact
    ``--analysis-out`` may differ, so this globs recursively rather than
    hard-coding the relative path. Raises FileNotFoundError (not a silent
    empty frame) when absent, and ValueError when more than one is found,
    because a noise-band draw without a quality summary means the validation
    stage never ran for that draw -- a pipeline bug, not a legitimate gap."""
    candidates = sorted(Path(output_dir).rglob("quality_summary.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"[noise_bands] no quality_summary.csv found under {output_dir} "
            "(expected at <output_dir>/analysis/population_validation/"
            "quality_summary.csv, written by "
            "population_validation.run_population_validation.run); run the "
            "population-validation stage for this draw before harvesting metrics."
        )
    if len(candidates) > 1:
        raise ValueError(
            f"[noise_bands] multiple quality_summary.csv found under {output_dir}: "
            f"{candidates}; pass a more specific output_dir."
        )
    return candidates[0]


def _harvest_quality_summary(output_dir: Path, draw_seed: int) -> pd.DataFrame:
    """Tidy rows from one draw's ``quality_summary.csv``.

    Input columns (written by ``quality_assessment.assess``): ``control``,
    ``mean_abs_delta_pp``, ``srmse`` (plus others, ignored here). Each control
    row becomes two tidy rows: ``metric_id="control_<name>"`` carrying the mean
    absolute delta in percentage points, and ``metric_id="control_srmse_<name>"``
    carrying the SRMSE. Both use ``group=""`` because the summary is already
    aggregated over geo cells within the control.
    """
    path = _find_quality_summary(output_dir)
    quality = pd.read_csv(path)
    required_columns = {"control", "mean_abs_delta_pp", "srmse"}
    missing_columns = required_columns - set(quality.columns)
    if missing_columns:
        raise ValueError(
            f"[noise_bands] {path} is missing column(s) {sorted(missing_columns)}; "
            "expected the quality_assessment.assess() output schema."
        )
    rows: list[dict[str, Any]] = []
    for _, row in quality.iterrows():
        control = str(row["control"])
        rows.append({
            "draw_seed": draw_seed, "metric_id": f"control_{control}",
            "group": "", "value": float(row["mean_abs_delta_pp"]),
        })
        rows.append({
            "draw_seed": draw_seed, "metric_id": f"control_srmse_{control}",
            "group": "", "value": float(row["srmse"]),
        })
    return pd.DataFrame(rows, columns=list(REQUIRED_DRAW_COLUMNS))


def _flatten_mid_report(report: dict, draw_seed: int) -> pd.DataFrame:
    """Flatten a ``run_mid_validation.run()`` result dict into tidy rows.

    See the ``MID_METRIC_IDS`` module docstring for which report keys map to
    which metric id, and why no Earth-Mover-Distance metric exists here (the
    module reports per-Kreis mean deltas, not distributional EMDs).
    """
    rows: list[dict[str, Any]] = []

    synth = report.get("commute_mean_km_synth") or {}
    mid = report.get("commute_mean_km_mid") or {}
    for ars5, synth_km in synth.items():
        mid_km = mid.get(ars5)
        if mid_km is None or pd.isna(mid_km) or pd.isna(synth_km):
            continue
        rows.append({
            "draw_seed": draw_seed, "metric_id": "commute_mean_km_delta_km",
            "group": str(ars5), "value": float(synth_km) - float(mid_km),
        })

    for metric_id, synth_key, mid_key in (
        ("license_pct_delta_pp", "license_pct_synth", "license_pct_mid"),
        ("employment_pct_delta_pp", "employment_pct_synth", "employment_pct_mid"),
    ):
        synth_pct = report.get(synth_key) or {}
        mid_pct = report.get(mid_key) or {}
        for ars5, synth_value in synth_pct.items():
            mid_value = mid_pct.get(ars5)
            if mid_value is None or pd.isna(mid_value) or pd.isna(synth_value):
                continue
            rows.append({
                "draw_seed": draw_seed, "metric_id": metric_id,
                "group": str(ars5), "value": float(synth_value) - float(mid_value),
            })

    for record in report.get("education_distance_vs_t43") or []:
        if "delta_km" not in record:
            continue
        group = f"{record['regiostar7']}_{record['level']}"
        rows.append({
            "draw_seed": draw_seed, "metric_id": "education_distance_delta_km",
            "group": group, "value": float(record["delta_km"]),
        })

    # Opportunistic only (never asserted, see MID_METRIC_IDS): populated only
    # when run_mid_validation was given a --sim-cache, which harvest_draw_metrics
    # does not plumb through.
    for record in report.get("mode_share_commute_vs_p12_1") or []:
        if "delta_pp" not in record or pd.isna(record["delta_pp"]):
            continue
        rows.append({
            "draw_seed": draw_seed, "metric_id": "mode_share_commute_delta_pp",
            "group": str(record["mode"]), "value": float(record["delta_pp"]),
        })

    return pd.DataFrame(rows, columns=list(REQUIRED_DRAW_COLUMNS))


def _harvest_mid_validation(output_dir: Path, draw_seed: int) -> pd.DataFrame:
    """Run the MiD validation for this draw's output dir and flatten its
    report. Reuses ``run_mid_validation._parse_args`` (rather than building
    the ``_Args`` dataclass by hand) so prefix auto-detection and the
    ``--analysis-out`` directory creation stay single-sourced with the CLI."""
    # Imported lazily: run_mid_validation pulls in geopandas/matplotlib, which
    # the quality-summary-only path (mid_validation=False) should not require.
    from braunschweig.analysis import run_mid_validation as RMV

    args = RMV._parse_args(["--output-dir", str(output_dir)])
    LOGGER.info("[noise_bands] running MiD validation for draw_seed=%s on %s",
                draw_seed, output_dir)
    report = RMV.run(args)
    frame = _flatten_mid_report(report, draw_seed)
    _assert_metrics_present(
        frame["metric_id"].tolist(), MID_METRIC_IDS,
        context=f"draw_seed={draw_seed} run_mid_validation result",
    )
    return frame


def harvest_draw_metrics(
    output_dir: str,
    draw_seed: int,
    *,
    mid_validation: bool = True,
    expected_metric_ids: set | None = None,
) -> pd.DataFrame:
    """Harvest one Monte-Carlo draw's validation metrics into a tidy frame.

    Always reads the population-validation stage's ``quality_summary.csv``
    under ``output_dir`` (see ``_find_quality_summary``; fails loudly if
    absent -- a missing summary means the validation stage never ran for this
    draw, not a legitimate empty result). When ``mid_validation`` is True
    (default), additionally runs ``run_mid_validation.run`` against the same
    ``output_dir`` and flattens its result, asserting every id in
    ``MID_METRIC_IDS`` is present (a missing MiD metric means the validation
    module's output shape changed and the noise-band sweep would silently
    compare apples to oranges across draws).

    Parameters
    ----------
    output_dir:
        One draw's eqasim run output directory (contains ``*_persons.csv``
        and, once population validation has run, ``quality_summary.csv``).
    draw_seed:
        The Monte-Carlo re-seed identifier for this draw; stamped onto every
        returned row.
    mid_validation:
        Whether to additionally run the MiD 2023 validation for this draw.
        Set to False in tests / when only the population-validation controls
        are needed (the MiD path needs the committed MiD reference CSVs and a
        full eqasim output with homes/activities GPKGs).
    expected_metric_ids:
        Optional caller-supplied set of metric ids that must be present in the
        returned frame (checked in addition to, and independently of, the
        built-in ``MID_METRIC_IDS`` check above). Raises ValueError listing
        the missing ids when not satisfied.

    Returns
    -------
    A tidy ``pandas.DataFrame`` with columns ``REQUIRED_DRAW_COLUMNS``
    (``draw_seed``, ``metric_id``, ``group``, ``value``), ready to be passed to
    ``aggregate_draw_metrics`` alongside the other draws of the same sweep.
    """
    output_path = Path(output_dir)
    frames = [_harvest_quality_summary(output_path, draw_seed)]
    if mid_validation:
        frames.append(_harvest_mid_validation(output_path, draw_seed))
    out = pd.concat(frames, ignore_index=True)
    if expected_metric_ids is not None:
        _assert_metrics_present(
            out["metric_id"].tolist(), expected_metric_ids,
            context=f"draw_seed={draw_seed} harvest_draw_metrics",
        )
    return out
