"""Per-run draw-summary CSV + coherence WARNs for the SrV location-category
decider (issue #262, Task 9).

TDD: written BEFORE the implementation. Covers:
    (a) ``srv_location_draw_summary`` math on synthetic ``subtype_stats`` /
        ``desired_by_category``: per-purpose drawn shares sum to 1, medians are
        correct, ``SRV_LOCATION_STAT_PREFIX`` keys are stripped back to the bare
        category name, a category with zero draws still gets its own row
        (``n_drawn=0``), and ``purpose="shop"`` reference rows are excluded.
    (b) the stage-side writer (``_write_srv_location_draw_summary``): WARNs
        above ``srv_location_share_warn_pp``, stays silent below it, and writes
        the honesty-labelled CSV artifact via a stub ``context.path()``.
    (c) OFF path: ``_build_plans_df(srv_location_decider=None)`` collects no
        desired distances, and the writer call in ``execute()`` is only ever
        reached inside the ``if srv_location_decider is not None:`` guard (no
        full stage run needed to check that -- mirrors the source-inspection
        style already used elsewhere in this test suite, e.g.
        tests/test_work_sector_aware.py).
"""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.locations import secondary_chainsolvers as sc

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MODES = ("car", "car_passenger", "pt", "bicycle", "walk")


def _flat_distribution():
    values = np.array([800.0, 1000.0, 1200.0, 1500.0])
    cdf = np.array([0.25, 0.5, 0.75, 1.0])
    return {
        mode: {
            "bounds": np.array([], dtype=float),
            "distributions": [{"values": values.copy(), "cdf": cdf.copy()}],
        }
        for mode in _MODES
    }


def _problem(person_id: int, purpose: str):
    """One bounded problem with a single leg of ``purpose`` (mirrors
    tests/test_srv_location_legloop.py's identical helper)."""
    return {
        "person_id": person_id, "activity_index": 2, "size": 1,
        "purposes": [purpose], "modes": ["car", "car"],
        "travel_times": np.array([600.0, 600.0]),
        "origin": np.array([[0.0, 0.0]]),
        "destination": np.array([[1000.0, 1000.0]]),
    }


def _by_purpose_srv_decider(category_by_purpose, used_marginal: bool = False):
    """Stub SrV decider returning a fixed per-purpose category (mirrors
    tests/test_srv_location_legloop.py's identical helper)."""
    def decide(purpose, mode, distance_m):
        return category_by_purpose[purpose], used_marginal
    return decide


def _shares_df():
    """Synthetic reference table mirroring
    ``srv2023_secondary_type_shares.csv``'s column universe (including a
    purpose="shop" validation-only row that must be excluded from the
    summary)."""
    rows = [
        ("leisure", "leisure_culture", 0.30, 100, 4.0, 3.0),
        ("leisure", "leisure_gastronomy", 0.20, 80, 2.0, 1.5),
        ("leisure", "leisure_misc", 0.15, 60, 3.0, 2.0),
        ("leisure", "leisure_outdoor", 0.15, 60, 1.0, 0.8),
        ("leisure", "leisure_sports", 0.10, 40, 3.5, 2.5),
        ("leisure", "leisure_visit", 0.10, 40, 3.0, 2.2),
        ("other", "errand_authority_medical", 0.45, 90, 4.5, 3.5),
        ("other", "errand_service", 0.35, 70, 2.5, 1.8),
        ("other", "other_misc", 0.20, 40, 2.8, 2.0),
        ("shop", "shop_daily", 0.70, 400, 1.5, 1.2),
        ("shop", "shop_non_daily", 0.30, 150, 3.0, 2.3),
    ]
    return pd.DataFrame(rows, columns=[
        "purpose", "category", "weight_share", "n_legs_unweighted",
        "weighted_median_gis_km", "weighted_median_euclid_km",
    ])


def _subtype_stats(leisure_counts, other_counts):
    """Build a ``subtype_stats`` dict with prefixed SrV draw counters, exactly
    as ``_build_plans_df`` allocates and increments them: every category in
    ``SRV_LEISURE_CATEGORIES``/``SRV_OTHER_CATEGORIES`` gets a zero-initialised
    key, then the ``*_counts`` overrides are applied."""
    stats = {
        sc.SRV_LOCATION_STAT_PREFIX + name: 0
        for name in sc.SRV_LEISURE_CATEGORIES + sc.SRV_OTHER_CATEGORIES
    }
    for category, count in leisure_counts.items():
        stats[sc.SRV_LOCATION_STAT_PREFIX + category] = count
    for category, count in other_counts.items():
        stats[sc.SRV_LOCATION_STAT_PREFIX + category] = count
    stats[sc.srv_location_marginal_fallback_stat("leisure")] = 0
    stats[sc.srv_location_marginal_fallback_stat("other")] = 0
    return stats


class _Ctx:
    """Minimal synpp ExecuteContext stub (declared-config semantics: one
    -argument ``config``; a fixed stage output directory for ``path()``),
    mirroring tests/test_srv_location_legloop.py's ``_Ctx``."""
    def __init__(self, cfg, output_dir):
        self._cfg = cfg
        self._output_dir = str(output_dir)

    def config(self, key):
        if key not in self._cfg:
            raise KeyError(f"_Ctx: no value for config key {key!r}.")
        return self._cfg[key]

    def path(self):
        return self._output_dir


def _write_shares_csv(tmp_path):
    path = tmp_path / "srv2023_secondary_type_shares.csv"
    _shares_df().to_csv(path, index=False)
    return str(path)


# ---------------------------------------------------------------------------
# (a) srv_location_draw_summary: pure math
# ---------------------------------------------------------------------------


def test_drawn_share_sums_to_one_per_purpose():
    stats = _subtype_stats(
        leisure_counts={"leisure_culture": 30, "leisure_gastronomy": 20,
                        "leisure_outdoor": 50},
        other_counts={"errand_service": 40, "other_misc": 60},
    )
    summary = sc.srv_location_draw_summary(stats, {}, _shares_df())

    leisure_share_sum = summary.loc[summary["purpose"] == "leisure", "drawn_share"].sum()
    other_share_sum = summary.loc[summary["purpose"] == "other", "drawn_share"].sum()
    assert leisure_share_sum == pytest.approx(1.0)
    assert other_share_sum == pytest.approx(1.0)


def test_drawn_share_matches_expected_fraction():
    stats = _subtype_stats(
        leisure_counts={"leisure_culture": 30, "leisure_outdoor": 70},
        other_counts={},
    )
    summary = sc.srv_location_draw_summary(stats, {}, _shares_df())
    row = summary[(summary["purpose"] == "leisure") & (summary["category"] == "leisure_culture")].iloc[0]
    assert row["drawn_share"] == pytest.approx(0.3)
    assert row["n_drawn"] == 30


def test_drawn_median_desired_km_correct():
    stats = _subtype_stats(
        leisure_counts={"leisure_culture": 3}, other_counts={},
    )
    desired_by_category = {"leisure_culture": [1.0, 2.0, 3.0]}
    summary = sc.srv_location_draw_summary(stats, desired_by_category, _shares_df())
    row = summary[(summary["purpose"] == "leisure") & (summary["category"] == "leisure_culture")].iloc[0]
    assert row["drawn_median_desired_km"] == pytest.approx(2.0)


def test_drawn_median_desired_km_nan_when_no_legs_drawn():
    stats = _subtype_stats(leisure_counts={}, other_counts={})
    summary = sc.srv_location_draw_summary(stats, {}, _shares_df())
    row = summary[(summary["purpose"] == "leisure") & (summary["category"] == "leisure_culture")].iloc[0]
    assert pd.isna(row["drawn_median_desired_km"])


def test_prefix_stripping_maps_srv_location_stat_prefix_to_bare_category():
    """subtype_stats carries SRV_LOCATION_STAT_PREFIX-namespaced keys (Task 8:
    "leisure_visit" is both a MiD subtype and an SrV category); the summary
    must read those, not an unprefixed "leisure_visit" key."""
    stats = _subtype_stats(
        leisure_counts={"leisure_visit": 12}, other_counts={},
    )
    # Pollute an UNPREFIXED "leisure_visit" key the way the MiD subtype
    # counter would (Task 4); the summary must NOT read it.
    stats["leisure_visit"] = 999
    summary = sc.srv_location_draw_summary(stats, {}, _shares_df())
    row = summary[(summary["purpose"] == "leisure") & (summary["category"] == "leisure_visit")].iloc[0]
    assert row["n_drawn"] == 12


def test_zero_drawn_category_gets_its_own_row():
    stats = _subtype_stats(
        leisure_counts={"leisure_culture": 30, "leisure_outdoor": 70},
        other_counts={},
    )
    summary = sc.srv_location_draw_summary(stats, {}, _shares_df())
    never_drawn = ("leisure_gastronomy", "leisure_misc", "leisure_sports", "leisure_visit")
    for category in never_drawn:
        row = summary[(summary["purpose"] == "leisure") & (summary["category"] == category)].iloc[0]
        assert row["n_drawn"] == 0
        assert row["drawn_share"] == pytest.approx(0.0)
        assert pd.isna(row["drawn_median_desired_km"])


def test_shop_reference_rows_excluded_from_summary():
    stats = _subtype_stats(leisure_counts={}, other_counts={})
    summary = sc.srv_location_draw_summary(stats, {}, _shares_df())
    assert "shop" not in set(summary["purpose"])
    assert not summary["category"].str.startswith("shop").any()


def test_reference_columns_joined_from_shares_df():
    stats = _subtype_stats(leisure_counts={"leisure_culture": 10}, other_counts={})
    summary = sc.srv_location_draw_summary(stats, {}, _shares_df())
    row = summary[(summary["purpose"] == "leisure") & (summary["category"] == "leisure_culture")].iloc[0]
    assert row["reference_share"] == pytest.approx(0.30)
    assert row["reference_median_euclid_km"] == pytest.approx(3.0)


def test_summary_columns_match_expected_schema():
    stats = _subtype_stats(leisure_counts={}, other_counts={})
    summary = sc.srv_location_draw_summary(stats, {}, _shares_df())
    assert list(summary.columns) == [
        "purpose", "category", "drawn_share", "reference_share",
        "drawn_median_desired_km", "reference_median_euclid_km", "n_drawn",
    ]


# ---------------------------------------------------------------------------
# (b) stage-side writer: WARN thresholds + CSV artifact
# ---------------------------------------------------------------------------


def test_writer_warns_above_threshold(tmp_path, capsys):
    shares_path = _write_shares_csv(tmp_path)
    # leisure_culture reference share is 0.30; drawn share 0.60 -> 30pp
    # deviation, above the 5.0pp default threshold.
    stats = _subtype_stats(
        leisure_counts={"leisure_culture": 60, "leisure_outdoor": 40},
        other_counts={},
    )
    ctx = _Ctx({
        "srv_location_type_shares_path": shares_path,
        "srv_location_share_warn_pp": 5.0,
    }, tmp_path / "out")
    (tmp_path / "out").mkdir()

    sc._write_srv_location_draw_summary(ctx, stats, {})

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "leisure/leisure_culture" in captured.out


def test_writer_no_warn_below_threshold(tmp_path, capsys):
    shares_path = _write_shares_csv(tmp_path)
    # leisure_culture reference share is 0.30; drawn share ~0.31 -> ~1pp
    # deviation, below the 5.0pp default threshold. leisure_outdoor is the
    # exact reference share (0.15) for the same reason.
    stats = _subtype_stats(
        leisure_counts={"leisure_culture": 31, "leisure_gastronomy": 20,
                        "leisure_misc": 15, "leisure_outdoor": 15,
                        "leisure_sports": 9, "leisure_visit": 10},
        other_counts={"errand_authority_medical": 45, "errand_service": 35,
                      "other_misc": 20},
    )
    ctx = _Ctx({
        "srv_location_type_shares_path": shares_path,
        "srv_location_share_warn_pp": 5.0,
    }, tmp_path / "out")
    (tmp_path / "out").mkdir()

    sc._write_srv_location_draw_summary(ctx, stats, {})

    captured = capsys.readouterr()
    assert "WARNING" not in captured.out


def test_writer_writes_csv_with_honesty_header(tmp_path, capsys):
    shares_path = _write_shares_csv(tmp_path)
    stats = _subtype_stats(leisure_counts={"leisure_culture": 10}, other_counts={})
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx = _Ctx({
        "srv_location_type_shares_path": shares_path,
        "srv_location_share_warn_pp": 5.0,
    }, out_dir)

    sc._write_srv_location_draw_summary(ctx, stats, {"leisure_culture": [2.5]})

    out_path = out_dir / sc.SRV_LOCATION_DRAW_SUMMARY_FILENAME
    assert out_path.exists()
    raw_text = out_path.read_text(encoding="utf-8")
    # Honesty labelling (MANDATORY, CLAUDE.md "No invented reference values" /
    # issue #262 plan): a draw-coherence check, never phrased as "validated".
    assert "draw-coherence" in raw_text
    assert "DRAWN desired-distance" in raw_text
    assert "NOT a" in raw_text
    assert "Never read this file as" in raw_text

    reloaded = pd.read_csv(out_path, comment="#")
    assert set(reloaded.columns) == {
        "purpose", "category", "drawn_share", "reference_share",
        "drawn_median_desired_km", "reference_median_euclid_km", "n_drawn",
    }
    row = reloaded[(reloaded["purpose"] == "leisure") & (reloaded["category"] == "leisure_culture")].iloc[0]
    assert row["drawn_median_desired_km"] == pytest.approx(2.5)


def test_writer_warns_loudly_for_category_absent_from_reference(tmp_path, capsys):
    """(Review finding 1, Important -- silent vocabulary drift.) A category the
    fixed code vocabulary (SRV_LEISURE_CATEGORIES) carries but the pinned
    reference table has NO row for (NaN reference_share) is exactly the "wrong
    key / vocabulary drift" case the fallback-transparency rule requires
    surfacing LOUDLY (e.g. the CSV was regenerated with a renamed/dropped
    category) -- it must never be a silent ``continue``."""
    shares_path = tmp_path / "shares_missing_category.csv"
    partial = _shares_df()
    partial = partial[partial["category"] != "leisure_visit"]
    partial.to_csv(shares_path, index=False)
    stats = _subtype_stats(leisure_counts={"leisure_visit": 50}, other_counts={})
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx = _Ctx({
        "srv_location_type_shares_path": str(shares_path),
        "srv_location_share_warn_pp": 5.0,
    }, out_dir)

    sc._write_srv_location_draw_summary(ctx, stats, {})

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "leisure/leisure_visit" in captured.out
    assert "no matching row" in captured.out.lower()
    assert "drift" in captured.out.lower()


def test_writer_warns_once_per_purpose_with_zero_drawn_legs(tmp_path, capsys):
    """(Review Minor, folded in as mandatory -- fallback-transparency rule.) A
    purpose with reference rows but ZERO drawn legs in total is near-100%
    non-coverage and must be loud, not silent: the per-category loop alone
    would say nothing (every category's drawn_share is NaN/0.0, which reads
    like "no deviation" unless the purpose total is checked)."""
    shares_path = _write_shares_csv(tmp_path)
    stats = _subtype_stats(
        leisure_counts={"leisure_culture": 30, "leisure_outdoor": 70},
        other_counts={},  # zero drawn "other" legs entirely
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx = _Ctx({
        "srv_location_type_shares_path": shares_path,
        "srv_location_share_warn_pp": 5.0,
    }, out_dir)

    sc._write_srv_location_draw_summary(ctx, stats, {})

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "0 drawn legs for purpose 'other'" in captured.out
    # The unaffected "leisure" purpose must NOT get the same warning.
    assert "0 drawn legs for purpose 'leisure'" not in captured.out


# ---------------------------------------------------------------------------
# (d) Integration: the REAL _build_plans_df output feeds
# srv_location_draw_summary without an adapter (review finding 2, Important
# -- "halves never joined"). Reuses the leg-loop fixture machinery from
# tests/test_srv_location_legloop.py (small ON-path problems + a stub
# per-purpose decider), verified against the module's OWN subtype_stats /
# desired_by_category, not a hand-rolled expectation.
# ---------------------------------------------------------------------------


def test_build_plans_df_output_feeds_srv_location_draw_summary_consistently():
    layered = {"leisure": _flat_distribution(), "other": _flat_distribution(),
               "shop": _flat_distribution()}
    problems = [
        _problem(1, "leisure"), _problem(2, "leisure"), _problem(3, "leisure"),
        _problem(4, "other"), _problem(5, "other"),
    ]
    decider = _by_purpose_srv_decider(
        {"leisure": "leisure_outdoor", "other": "errand_service"})

    plans_df, _meta, _unbounded, stats, desired_by_category = sc._build_plans_df(
        problems, layered, 2.0, np.random.RandomState(7), srv_location_decider=decider,
    )
    summary = sc.srv_location_draw_summary(stats, desired_by_category, _shares_df())

    variable_legs = plans_df[plans_df["to_act_type"] != "home"]
    assert len(variable_legs) == 5  # one secondary leg per problem

    # (a) n_drawn matches the REAL subtype_stats counters for every category
    # the decider actually drew (neither hand-rolled from the problem count).
    leisure_row = summary[(summary["purpose"] == "leisure")
                          & (summary["category"] == "leisure_outdoor")].iloc[0]
    other_row = summary[(summary["purpose"] == "other")
                        & (summary["category"] == "errand_service")].iloc[0]
    assert leisure_row["n_drawn"] == stats[sc.SRV_LOCATION_STAT_PREFIX + "leisure_outdoor"] == 3
    assert other_row["n_drawn"] == stats[sc.SRV_LOCATION_STAT_PREFIX + "errand_service"] == 2

    # (b) drawn_median_desired_km equals the median of plans_df's
    # distance_meters/1000 RESTRICTED TO THAT CATEGORY'S LEGS. The category is
    # recoverable directly from plans_df here: neither "leisure_outdoor" nor
    # "errand_service" is an SRV_AGGREGATE_PLACEMENT alias, so the placement
    # activity IS the drawn category name.
    expected_leisure_km = (
        variable_legs.loc[variable_legs["to_act_type"] == "leisure_outdoor", "distance_meters"] / 1000.0
    ).median()
    expected_other_km = (
        variable_legs.loc[variable_legs["to_act_type"] == "errand_service", "distance_meters"] / 1000.0
    ).median()
    assert leisure_row["drawn_median_desired_km"] == pytest.approx(expected_leisure_km)
    assert other_row["drawn_median_desired_km"] == pytest.approx(expected_other_km)

    # Cross-check against the collected dict directly too (belt-and-braces:
    # the two halves -- subtype_stats and desired_by_category -- must agree
    # with each other AND with plans_df, not just pairwise).
    assert leisure_row["drawn_median_desired_km"] == pytest.approx(
        float(np.median(desired_by_category["leisure_outdoor"])))
    assert other_row["drawn_median_desired_km"] == pytest.approx(
        float(np.median(desired_by_category["errand_service"])))

    # The collected dict's total leg count equals the leisure+other variable
    # leg count in plans_df: every one of the 5 problems is leisure/other, so
    # every variable leg must have been drawn a category.
    n_total_desired = sum(len(v) for v in desired_by_category.values())
    assert n_total_desired == len(variable_legs) == 5


# ---------------------------------------------------------------------------
# (c) OFF path
# ---------------------------------------------------------------------------


def test_build_plans_df_collects_no_desired_distances_when_decider_off():
    problem = {
        "person_id": 1, "activity_index": 2, "size": 1,
        "purposes": ["leisure"], "modes": ["car", "car"],
        "travel_times": np.array([600.0, 600.0]),
        "origin": np.array([[0.0, 0.0]]),
        "destination": np.array([[1000.0, 1000.0]]),
    }
    _df, _meta, _unbounded, stats, desired_by_category = sc._build_plans_df(
        [problem], {"leisure": _flat_distribution(), "other": _flat_distribution(),
                    "shop": _flat_distribution()},
        2.0, np.random.RandomState(1), srv_location_decider=None,
    )
    assert desired_by_category == {}
    assert stats == {}


def test_execute_only_calls_writer_inside_the_srv_location_decider_guard():
    """Source-inspection regression check (no full stage run needed -- mirrors
    tests/test_work_sector_aware.py's style): the writer call must live inside
    the SAME ``if srv_location_decider is not None:`` block that gates the
    existing draw-summary log lines, so the OFF path never touches it."""
    source = inspect.getsource(sc.execute)
    marker = "if srv_location_decider is not None:"
    assert source.count("_write_srv_location_draw_summary(") == 1

    guard_index = source.index(marker)
    call_index = source.index("_write_srv_location_draw_summary(")
    assert guard_index < call_index

    # The guarded block ends at the next line dedented back to (or below) the
    # "if" statement's own indentation; the writer call must appear before
    # that dedent, i.e. still inside the block.
    guard_indent = len(source[:guard_index].rsplit("\n", 1)[-1])
    block_lines = source[guard_index:call_index + len("_write_srv_location_draw_summary(")].split("\n")[1:]
    for line in block_lines[:-1]:
        if line.strip():
            assert len(line) - len(line.lstrip()) > guard_indent, (
                "a line dedented back to (or below) the guard's indentation "
                "was found between the guard and the writer call -- the call "
                "is no longer inside the 'if srv_location_decider is not "
                "None:' block."
            )
