"""Two-pass secondary chainsolver for passive escort joint locations (issue #385, ADR-0118):
flag declaration, prerequisites, and the pass composition."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.synthesis.locations import secondary_chainsolvers as sc
# Reuse the sibling suite's configure() stub instead of duplicating one: it already
# mirrors synpp's ConfigurationContext.config(name, default) semantics (a key's value
# is resolved once and stays fixed for the re-reads configure() does).
from tests.test_escort_chainsolvers import _ConfigureCtx


def _configure_context(**overrides):
    """A ``_ConfigureCtx`` whose config values are pinned BEFORE ``configure`` runs.

    Pre-seeding ``registered`` is how a caller-supplied value is expressed with that
    stub: ``config(key, default)`` keeps the first value seen, so a key seeded here
    wins over the default ``configure`` declares for it -- exactly what a YAML config
    setting that key does in production.
    """
    ctx = _ConfigureCtx()
    ctx.registered.update(overrides)
    return ctx


def _base_overrides(**overrides):
    """Both prerequisites of the joint-location flag satisfied, unless overridden."""
    values = {"escort_purpose": True, "escort_passive_from_adult": True}
    values.update(overrides)
    return values


def test_configure_declares_the_joint_location_flag_default_off():
    ctx = _configure_context()
    sc.configure(ctx)
    assert ctx.registered["escort_passive_joint_location"] is False


def test_configure_rejects_joint_location_without_passive_from_adult():
    ctx = _configure_context(**_base_overrides(escort_passive_joint_location=True,
                                               escort_passive_from_adult=False))
    # "requires escort_passive_from_adult", not the bare key name: the sibling
    # escort_purpose message also mentions escort_passive_from_adult, so the plain
    # substring would pass on the WRONG error.
    with pytest.raises(ValueError, match="requires escort_passive_from_adult"):
        sc.configure(ctx)


def test_configure_rejects_joint_location_without_escort_purpose():
    ctx = _configure_context(**_base_overrides(escort_passive_joint_location=True,
                                               escort_purpose=False))
    with pytest.raises(ValueError, match="escort_purpose"):
        sc.configure(ctx)


# --- per-pass report aggregation --------------------------------------------------
# execute() reports the subtype draw rates and the fallback accounting ONCE over ALL
# passes, so the two aggregators below are what makes a two-pass run's transparency
# reporting cover the whole population instead of one pass's fragment.

def _report(subtype_stats=None, desired_by_category=None):
    return {
        "n_problems": 0, "n_unbounded": 0, "n_failed_bounded": 0, "n_plan_rows": 0,
        "subtype_stats": subtype_stats or {},
        "desired_by_category": desired_by_category or {},
    }


def test_sum_subtype_stats_adds_shared_keys_and_keeps_pass_only_keys():
    total = sc._sum_subtype_stats([
        _report({"shop_daily": 3, "shop_non_daily": 1}),
        _report({"shop_daily": 4, "leisure_local": 2}),
    ])
    assert total == {"shop_daily": 7, "shop_non_daily": 1, "leisure_local": 2}


def test_sum_subtype_stats_of_one_report_reproduces_its_counts():
    stats = {"shop_daily": 3, "distance_layer_fallback": 1}
    assert sc._sum_subtype_stats([_report(stats)]) == stats


def test_concat_desired_by_category_keeps_every_passs_distances():
    merged = sc._concat_desired_by_category([
        _report(desired_by_category={"shopping_daily": [1.0, 2.0]}),
        _report(desired_by_category={"shopping_daily": [3.0], "leisure_sport": [4.0]}),
    ])
    assert merged == {"shopping_daily": [1.0, 2.0, 3.0], "leisure_sport": [4.0]}


def test_concat_desired_by_category_does_not_mutate_a_report():
    first = _report(desired_by_category={"shopping_daily": [1.0]})
    sc._concat_desired_by_category([first, _report(desired_by_category={"shopping_daily": [2.0]})])
    assert first["desired_by_category"] == {"shopping_daily": [1.0]}


# --- two-pass composition ---------------------------------------------------------
# _compose_two_pass is exercised with a FAKE solve (same signature and return contract as
# _solve_problem_set) so the pass logic -- who is solved in which pass, which anchors travel
# with which pass, the purpose rewrite, and the appended anchor rows -- is testable without
# the chainsolvers dependency, a candidate set or any RNG.

def _trips_two_households():
    # adult 1: home -> shop -> home; child 2 (linked to adult 1, W_ID 1): home -> shop -> home
    # adult 3 (no children): home -> leisure -> home
    return pd.DataFrame({
        "person_id":         [1, 1, 2, 2, 3, 3],
        "trip_index":        [0, 1, 0, 1, 0, 1],
        "preceding_purpose": ["home", "shop", "home", "shop", "home", "leisure"],
        "following_purpose": ["shop", "home", "shop", "home", "leisure", "home"],
    })


def _links_child_2():
    return pd.DataFrame({"child_person_id": [2], "child_activity_index": [1],
                         "adult_person_id": [1], "adult_activity_index": [1],
                         "adult_purpose": ["shop"]})


def _fake_solve_factory(calls):
    """Places every secondary activity of the given persons at (person_id, person_id)."""
    def fake_solve(df_trips_pass, df_primary, activity_anchors, shared, *, pass_label=""):
        calls.append({"persons": sorted(df_trips_pass["person_id"].unique().tolist()),
                      "anchors": dict(activity_anchors or {}),
                      "purposes": df_trips_pass[["person_id", "following_purpose"]].values.tolist(),
                      "label": pass_label})
        rows = []
        for person_id, group in df_trips_pass.groupby("person_id"):
            for _, trip in group.iterrows():
                if trip["following_purpose"] in ("shop", "leisure", "other"):
                    rows.append((person_id, trip["trip_index"] + 1, f"loc_{person_id}",
                                 Point(person_id, person_id)))
        df_loc = gpd.GeoDataFrame(pd.DataFrame.from_records(
            rows, columns=["person_id", "activity_index", "location_id", "geometry"]),
            geometry="geometry", crs="EPSG:25832")
        df_conv = pd.DataFrame({"valid": [True] * len(rows), "size": [1] * len(rows)})
        report = {"n_problems": len(rows), "n_unbounded": 0, "n_failed_bounded": 0,
                  "subtype_stats": {"shop_daily": len(rows)}, "desired_by_category": {"shop": [1.0]},
                  "n_plan_rows": len(rows)}
        return df_loc, df_conv, report
    return fake_solve


def test_two_pass_places_the_child_at_the_adults_location():
    calls = []
    shared = {"crs": "EPSG:25832"}
    df_locations, df_convergence, reports, anchor_stats = sc._compose_two_pass(
        _trips_two_households(), df_primary=None, escort_activity_anchors=None,
        links=_links_child_2(), shared=shared, solve=_fake_solve_factory(calls))
    # Pass 1 solved adults 1 and 3 only; pass 2 solved child 2 with the adult's anchor.
    assert calls[0]["persons"] == [1, 3] and "pass 1/2" in calls[0]["label"]
    assert calls[1]["persons"] == [2] and "pass 2/2" in calls[1]["label"]
    assert calls[1]["anchors"] == {(2, 1): Point(1, 1)}
    # The child's joint activity was rewritten to the fixed purpose for pass 2.
    assert [2, "passive_linked"] in calls[1]["purposes"]
    child_row = df_locations[(df_locations["person_id"] == 2) & (df_locations["activity_index"] == 1)]
    assert len(child_row) == 1
    assert child_row["location_id"].iloc[0] == "loc_1" and child_row["geometry"].iloc[0] == Point(1, 1)
    assert anchor_stats == {"n_links": 1, "n_resolved": 1, "n_unresolved": 0}
    assert len(reports) == 2 and sum(r["n_problems"] for r in reports) == 2
    assert len(df_convergence) == 2


def test_two_pass_keeps_escort_anchors_with_their_pass():
    calls = []
    escort_anchors = {(1, 1): Point(9, 9), (2, 1): Point(8, 8)}  # (2,1) is overridden by the joint anchor
    sc._compose_two_pass(_trips_two_households(), df_primary=None,
                         escort_activity_anchors=escort_anchors, links=_links_child_2(),
                         shared={"crs": "EPSG:25832"}, solve=_fake_solve_factory(calls))
    assert calls[0]["anchors"] == {(1, 1): Point(9, 9)}
    assert calls[1]["anchors"] == {(2, 1): Point(1, 1)}


def test_two_pass_with_no_links_runs_pass_1_only():
    calls = []
    df_locations, _conv, reports, anchor_stats = sc._compose_two_pass(
        _trips_two_households(), df_primary=None, escort_activity_anchors=None,
        links=_links_child_2().iloc[0:0], shared={"crs": "EPSG:25832"},
        solve=_fake_solve_factory(calls))
    assert len(calls) == 1 and calls[0]["persons"] == [1, 2, 3]
    assert anchor_stats == {"n_links": 0, "n_resolved": 0, "n_unresolved": 0}
