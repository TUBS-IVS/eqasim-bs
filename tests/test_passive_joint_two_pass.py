"""Two-pass secondary chainsolver for passive escort joint locations (issue #385, ADR-0119):
flag declaration, prerequisites, and the pass composition."""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.synthesis.locations import secondary_chainsolvers as sc
from braunschweig.synthesis.locations.passive_joint_links import (
    DEFAULT_UNRESOLVED_ANCHOR_WARNING_SHARE,
)
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


def _raw_anchor_solve_factory(calls):
    """Like :func:`_fake_solve_factory` but records the RAW ``activity_anchors`` object.

    The anchors contract is "None means no escort link at all, a dict means a link table
    that may be empty"; a recorder normalising with ``dict(... or {})`` cannot tell the
    two apart, which is exactly what this fake is for.
    """
    inner = _fake_solve_factory(calls)

    def fake_solve(df_trips_pass, df_primary, activity_anchors, shared, *, pass_label=""):
        result = inner(df_trips_pass, df_primary, activity_anchors, shared,
                       pass_label=pass_label)
        calls[-1]["raw_anchors"] = activity_anchors
        return result
    return fake_solve


def test_pass_1_receives_none_when_there_are_no_escort_anchors():
    calls = []
    sc._compose_two_pass(_trips_two_households(), df_primary=None,
                         escort_activity_anchors=None, links=_links_child_2(),
                         shared={"crs": "EPSG:25832"},
                         solve=_raw_anchor_solve_factory(calls))
    # None (no escort household link at all), never an empty dict: the problem splitter
    # distinguishes "no anchor table" from "an anchor table without an entry for me".
    assert calls[0]["raw_anchors"] is None


def test_pass_1_receives_a_dict_even_when_no_escort_anchor_belongs_to_it():
    calls = []
    sc._compose_two_pass(_trips_two_households(), df_primary=None,
                         escort_activity_anchors={(2, 1): Point(8, 8)},
                         links=_links_child_2(), shared={"crs": "EPSG:25832"},
                         solve=_raw_anchor_solve_factory(calls))
    # The only escort anchor belongs to the pass-2 child, so pass 1 gets an EMPTY dict.
    assert calls[0]["raw_anchors"] == {}
    assert isinstance(calls[0]["raw_anchors"], dict)


def _unresolved_solve_factory(calls):
    """A solve that places NOTHING, so every joint link stays unresolved."""
    def fake_solve(df_trips_pass, df_primary, activity_anchors, shared, *, pass_label=""):
        calls.append({"persons": sorted(df_trips_pass["person_id"].unique().tolist()),
                      "purposes": df_trips_pass[["person_id", "following_purpose"]].values.tolist(),
                      "label": pass_label})
        df_loc = gpd.GeoDataFrame(pd.DataFrame(
            {"person_id": pd.Series(dtype="int64"), "activity_index": pd.Series(dtype="int64"),
             "location_id": pd.Series(dtype=object), "geometry": pd.Series(dtype=object)}),
            geometry="geometry", crs="EPSG:25832")
        df_conv = pd.DataFrame({"valid": pd.Series(dtype=bool), "size": pd.Series(dtype="int64")})
        report = {"n_problems": 0, "n_unbounded": 0, "n_failed_bounded": 0,
                  "subtype_stats": {}, "desired_by_category": {}, "n_plan_rows": 0}
        return df_loc, df_conv, report
    return fake_solve


def test_an_unresolved_link_leaves_the_child_on_the_ordinary_pass_2_path():
    """The adult's activity has no pass-1 row (it was never placed), so the child's joint
    activity is NOT rewritten, no anchor row is appended, and the child is still solved in
    pass 2 -- with its plan-level purpose, i.e. the independent draw."""
    calls = []
    df_locations, _conv, reports, anchor_stats = sc._compose_two_pass(
        _trips_two_households(), df_primary=None, escort_activity_anchors=None,
        links=_links_child_2(), shared={"crs": "EPSG:25832"},
        solve=_unresolved_solve_factory(calls))
    assert anchor_stats == {"n_links": 1, "n_resolved": 0, "n_unresolved": 1}
    assert calls[1]["persons"] == [2]
    assert [2, "passive_linked"] not in calls[1]["purposes"]
    assert [2, "shop"] in calls[1]["purposes"]
    assert len(df_locations) == 0  # nothing placed, and no anchor row appended
    assert len(reports) == 2


def test_the_anchor_summary_escalates_above_the_unresolved_share(capsys):
    calls = []
    sc._compose_two_pass(_trips_two_households(), df_primary=None,
                         escort_activity_anchors=None, links=_links_child_2(),
                         shared={"crs": "EPSG:25832"}, solve=_unresolved_solve_factory(calls))
    line = capsys.readouterr().out
    # The stage prefix stays first, as in every other escalated line of this stage.
    assert "[braunschweig.secondary_chainsolvers] WARNING: passive joint location:" in line
    assert "1 unresolved" in line


def test_the_anchor_summary_does_not_escalate_when_the_anchors_resolve(capsys):
    calls = []
    sc._compose_two_pass(_trips_two_households(), df_primary=None,
                         escort_activity_anchors=None, links=_links_child_2(),
                         shared={"crs": "EPSG:25832"}, solve=_fake_solve_factory(calls))
    assert "WARNING" not in capsys.readouterr().out


def test_with_pass_label_returns_the_one_pass_line_by_identity():
    """The one-pass path must stay byte-identical, so an empty label returns the very
    same string object instead of a rebuilt equal one."""
    line = "[braunschweig.secondary_chainsolvers] excursion boundary clip: 0/0"
    assert sc._with_pass_label(line, "") is line
    assert sc._with_pass_label("no stage prefix here", " [pass 2/2]") == "no stage prefix here"


def test_with_pass_label_inserts_the_label_after_the_stage_prefix_once():
    line = ("[braunschweig.secondary_chainsolvers] excursion boundary clip: "
            "[braunschweig.secondary_chainsolvers] is quoted inside the line")
    labelled = sc._with_pass_label(line, " [pass 2/2: linked children]")
    assert labelled.startswith(
        "[braunschweig.secondary_chainsolvers] [pass 2/2: linked children] excursion")
    assert labelled.count(" [pass 2/2: linked children]") == 1


# --- report lines and config fail-fasts -------------------------------------------

def _link_stats(n_passive_paired, n_linked):
    return {"n_passive_paired": n_passive_paired, "n_linked": n_linked,
            "link_rate": (n_linked / n_passive_paired) if n_passive_paired else float("nan")}


def test_the_link_rate_line_escalates_when_nothing_could_be_linked():
    line = sc._passive_joint_link_summary(_link_stats(120, 0))
    assert line.startswith(
        "[braunschweig.secondary_chainsolvers] WARNING: passive joint link:")
    assert "0/120 paired passive legs linked" in line


def test_the_link_rate_line_stays_plain_when_links_were_built():
    line = sc._passive_joint_link_summary(_link_stats(120, 90))
    assert line.startswith("[braunschweig.secondary_chainsolvers] passive joint link:")
    assert "90/120 paired passive legs linked to the adult's activity (75.0%)" in line


def test_the_link_rate_line_survives_a_run_without_any_paired_leg():
    # link_rate is NaN then; the line must not escalate and must not print "nan%".
    line = sc._passive_joint_link_summary(_link_stats(0, 0))
    assert "WARNING" not in line
    assert "0/0 paired passive legs linked to the adult's activity (0.0%)" in line


def test_the_anchor_summary_uses_the_shared_unresolved_share_constant():
    """The escalation is ``>=``, the convention every sibling rate instrument of this stage
    uses (reporting._fallback_accounting_summary, reporting._excursion_boundary_clip_summary,
    the SrV marginal-fallback line), so a share landing EXACTLY on the threshold warns."""
    n_links = 10
    n_unresolved = int(DEFAULT_UNRESOLVED_ANCHOR_WARNING_SHARE * n_links) + 1
    stats = {"n_links": n_links, "n_resolved": n_links - n_unresolved,
             "n_unresolved": n_unresolved}
    assert "WARNING: " in sc._passive_joint_anchor_summary(3, stats)
    at_threshold = {"n_links": n_links, "n_resolved": n_links - n_unresolved + 1,
                    "n_unresolved": n_unresolved - 1}
    assert (at_threshold["n_unresolved"] / n_links
            == DEFAULT_UNRESOLVED_ANCHOR_WARNING_SHARE)
    assert "WARNING: " in sc._passive_joint_anchor_summary(3, at_threshold)
    below_threshold = {"n_links": n_links, "n_resolved": n_links - n_unresolved + 2,
                       "n_unresolved": n_unresolved - 2}
    assert "WARNING" not in sc._passive_joint_anchor_summary(3, below_threshold)


def test_shard_attempts_must_be_a_positive_integer():
    """The two-pass extraction reads the key unconditionally (it is part of the shared
    solve state), so a config leaving it null must fail with a named error instead of a
    bare TypeError from int(None)."""
    with pytest.raises(ValueError, match="shard_attempts must be a positive integer"):
        sc._resolve_shard_attempts(None)
    with pytest.raises(ValueError, match="got 0"):
        sc._resolve_shard_attempts(0)
    assert sc._resolve_shard_attempts(3) == 3


def _one_candidate_frame():
    # No offers_* column at all -> every secondary purpose takes the any-type pool, which
    # is the branch that prints the fallback catalog line.
    return pd.DataFrame({"location_id": ["sec_1"], "geometry": [Point(0.0, 0.0)]})


def _one_problem():
    return [{"person_id": 1, "activity_index": 1, "purposes": ["shop"], "size": 1}]


def test_the_fallback_catalog_line_carries_the_pass_label(capsys):
    sc._fallback_place(_one_problem(), [0], _one_candidate_frame(),
                       np.random.RandomState(0), "EPSG:25832",
                       pass_label=" [pass 2/2: linked children]")
    assert ("[braunschweig.secondary_chainsolvers] [pass 2/2: linked children] "
            "fallback catalog:") in capsys.readouterr().out


def test_the_fallback_catalog_line_is_unchanged_without_a_pass_label(capsys):
    sc._fallback_place(_one_problem(), [0], _one_candidate_frame(),
                       np.random.RandomState(0), "EPSG:25832")
    assert capsys.readouterr().out.startswith(
        "[braunschweig.secondary_chainsolvers] fallback catalog:")
