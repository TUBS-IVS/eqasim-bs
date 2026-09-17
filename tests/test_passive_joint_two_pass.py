"""Two-pass secondary chainsolver for passive escort joint locations (issue #385, ADR-0119):
flag declaration, prerequisites, the pass composition, execute()'s flag dispatch, and the
RNG call-order guard for the ``_solve_problem_set`` extraction."""
import sys
import types

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

def _link_stats(n_passive_paired, n_linked, n_adult_not_in_household=0,
                n_adult_leg_missing=0, n_purpose_not_secondary=0):
    return {"n_passive_paired": n_passive_paired, "n_linked": n_linked,
            "n_adult_not_in_household": n_adult_not_in_household,
            "n_adult_leg_missing": n_adult_leg_missing,
            "n_purpose_not_secondary": n_purpose_not_secondary,
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
    # The exclusion split divides by the paired count, so the zero-paired run must not
    # print "nan%" for it either.
    assert "nan" not in line


def test_the_link_rate_line_carries_the_per_exclusion_split():
    """The split already reaches the run log through ``logging`` --
    ``passive_joint_links._log_link_rates`` logs it, and ``scripts/run_synpp.py::main``'s
    ``braunschweig.logging_setup.setup_logging`` root-logger setup carries every
    ``braunschweig.*`` logger into it. The stage prints the same split ADDITIONALLY so it
    sits with the stage's other per-run rate lines (the fallback accounting summary and the
    success rate) in the operator's stdout block, where the rates are read together
    (CLAUDE.md fallback transparency rule 1). The printed line carries only the three main
    reasons, so it must say so and point to the log line for the full five-way split."""
    line = sc._passive_joint_link_summary(
        _link_stats(200, 140, n_adult_not_in_household=20, n_adult_leg_missing=30,
                    n_purpose_not_secondary=10))
    assert "140/200 paired passive legs linked to the adult's activity (70.0%)" in line
    # Same wording as the log line, so the two channels are recognisably one statement.
    assert ("excluded (three main reasons; the run log's [passive_joint_links] line carries "
            "all five): adult not in the synthetic household (plan source) 20 (10.0%), "
            "adult leg missing 30 (15.0%), purpose not secondary 10 (5.0%)") in line
    assert line.endswith("unlinked children keep the independent draw.")


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


def test_shard_attempts_rejects_a_non_integral_value():
    """A fractional value must be NAMED, not silently truncated: ``int(3.7)`` is 3, so a
    config asking for 3.7 attempts would have run with a different number than it states
    -- an untraceable divergence between the config and the executed run."""
    with pytest.raises(ValueError, match="shard_attempts must be a positive integer"):
        sc._resolve_shard_attempts(3.7)
    with pytest.raises(ValueError, match="got 0.5"):
        sc._resolve_shard_attempts(0.5)
    # An integral float (a YAML ``3.0``) is a legitimate way to write 3 and stays accepted.
    assert sc._resolve_shard_attempts(3.0) == 3


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


# --- execute()'s flag dispatch ----------------------------------------------------
# The two branches of execute() itself: the ON branch's plan-source guard and the OFF
# branch's single-solve contract. The stub context below is an ALL-FLAGS-OFF minimum, so
# the stage's own setup (the #201 escort link, the primary locations, the distributions,
# every decider, the candidate frame and the worker settings) runs FOR REAL up to the
# dispatch -- only the solve itself, which needs the chainsolvers solver and a real
# candidate set, is substituted.

def _execute_stage_values(df_persons):
    """The six stage outputs execute() reads, at the smallest shape that carries the
    contract: two persons of one household, each with a home -> shop -> home chain."""
    crs = "EPSG:25832"
    df_home = gpd.GeoDataFrame({"household_id": [10], "geometry": [Point(0.0, 0.0)]},
                               geometry="geometry", crs=crs)
    df_work = gpd.GeoDataFrame({"person_id": [1, 2],
                                "geometry": [Point(1000.0, 0.0), Point(1000.0, 0.0)]},
                               geometry="geometry", crs=crs)
    df_education = gpd.GeoDataFrame({"person_id": [1, 2],
                                     "geometry": [Point(0.0, 1000.0), Point(0.0, 1000.0)]},
                                    geometry="geometry", crs=crs)
    df_trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2], "trip_index": [0, 1, 0, 1],
        "preceding_purpose": ["home", "shop", "home", "shop"],
        "following_purpose": ["shop", "home", "shop", "home"],
        "mode": ["car"] * 4,
        "departure_time": [0.0, 3600.0, 0.0, 3600.0],
        "arrival_time": [600.0, 4200.0, 600.0, 4200.0],
    })
    df_candidates = gpd.GeoDataFrame({
        "location_id": ["sec_1"], "offers_shop": [True], "offers_leisure": [True],
        "offers_other": [True], "geometry": [Point(500.0, 500.0)],
    }, geometry="geometry", crs=crs)
    return {
        "synthesis.population.trips.final": df_trips,
        "synthesis.population.sampled": df_persons,
        "synthesis.population.spatial.home.locations": df_home,
        "synthesis.population.spatial.primary.locations": (df_work, df_education),
        # An empty distributions dict is the documented empty-input contract of
        # _resample_distributions; nothing in these tests samples a distance.
        "synthesis.population.spatial.secondary.distance_distributions": {},
        "synthesis.locations.secondary": df_candidates,
    }


#: Every flag OFF -- the cheapest configuration that still reaches the dispatch, and the
#: one whose printed output the OFF path must keep byte-identical.
_EXECUTE_CONFIG = {
    "escort_household_link": False,
    "escort_distance_by_type": False,
    "escort_purpose": False,
    "random_seed": 1234,
    "leisure_correction_factor": 2.0,
    "secondary_shop_daily_split": False,
    "secondary_leisure_subtype_split": False,
    "secondary_other_subtype_split": False,
    "leisure_visit_building_potential": False,
    "secondary_srv_location_types": False,
    "secondary_building_potentials": False,
    # The surrogate rescue keys (#409): read unconditionally once execute() enters the
    # escort_passive_joint_location branch (_passive_joint_link_table's call reads all
    # four regardless of surrogate_enabled), mirroring the defaults configure() declares.
    "escort_passive_joint_surrogate": False,
    "escort_passive_joint_surrogate_max_gap_minutes": 15.0,
    "escort_passive_joint_surrogate_min_age_years": 14,
    "escort_passive_joint_surrogate_require_same_purpose": True,
    "braunschweig.chainsolvers.fallback": "rda",
    "braunschweig.chainsolvers.solver": sc.DEFAULT_CHAIN_SOLVER,
    "braunschweig.chainsolvers.parallel": False,
    "braunschweig.chainsolvers.processes": 1,
    "braunschweig.chainsolvers.shard_attempts": sc.DEFAULT_SHARD_ATTEMPTS,
}


class _ExecuteCtx:
    """Minimal synpp ExecuteContext stand-in for ``execute``.

    ``config(key)`` takes the key ALONE, mirroring synpp's
    ``ExecuteContext.config`` (declared options only, no default parameter) exactly like
    ``tests.test_escort_chainsolvers._Ctx``: a two-argument read from the stage would fail
    here just as it would crash in production. Every config and stage read is recorded, so
    a test can assert HOW OFTEN a stage was read.
    """

    def __init__(self, *, df_persons, **config_overrides):
        self._config = dict(_EXECUTE_CONFIG)
        self._config.update(config_overrides)
        self._stages = _execute_stage_values(df_persons)
        self.config_reads = []
        self.stage_reads = []

    def config(self, key):
        self.config_reads.append(key)
        if key not in self._config:
            raise KeyError(
                f"_ExecuteCtx: no value for config key {key!r} -- declared-config "
                "semantics require the test to supply it explicitly.")
        return self._config[key]

    def stage(self, name):
        self.stage_reads.append(name)
        if name not in self._stages:
            raise KeyError(f"_ExecuteCtx: no value for stage {name!r}.")
        return self._stages[name]


def _persons_without_plan_source():
    """A persons frame from a producer that carries no plan-source ids (the case the ON
    branch's guard names)."""
    return pd.DataFrame({"person_id": [1, 2], "household_id": [10, 10]})


def _persons_without_hp_alter():
    """A persons frame that DOES carry the plan-source ids the identity link needs, but not
    the age column the surrogate rescue additionally requires (M10)."""
    return pd.DataFrame({"person_id": [1, 2], "household_id": [10, 10],
                        "source_H_ID": [100, 100], "source_P_ID": [1, 2]})


@pytest.fixture
def fake_chainsolvers_module(monkeypatch):
    """Stub out the optional ``chainsolvers`` package for the duration of one test.

    ``execute`` imports it eagerly purely as a fail-fast dependency check, and the tests
    using this fixture never solve. chainsolvers is an optional dependency that is not
    installed everywhere (see tests/test_chainsolvers_parallel.py), so injecting the stub
    unconditionally keeps these tests deterministic with AND without the real package.
    """
    monkeypatch.setitem(sys.modules, "chainsolvers", types.ModuleType("chainsolvers"))


def _recording_execute_solve(calls):
    """Stands in for ``_solve_problem_set``: records the call and returns an empty result
    in the shape execute()'s consolidated reporting consumes."""
    def solve(df_trips_pass, df_primary, activity_anchors, shared, **kwargs):
        calls.append({"persons": sorted(df_trips_pass["person_id"].unique().tolist()),
                      "anchors": activity_anchors, "kwargs": dict(kwargs)})
        df_loc = gpd.GeoDataFrame(pd.DataFrame(
            {"person_id": pd.Series(dtype="int64"), "activity_index": pd.Series(dtype="int64"),
             "location_id": pd.Series(dtype=object), "geometry": pd.Series(dtype=object)}),
            geometry="geometry", crs=shared["crs"])
        df_conv = pd.DataFrame({"valid": pd.Series(dtype=bool), "size": pd.Series(dtype="int64")})
        report = {"n_problems": 0, "n_unbounded": 0, "n_failed_bounded": 0,
                  "subtype_stats": {}, "desired_by_category": {}, "n_plan_rows": 0}
        return df_loc, df_conv, report
    return solve


def test_execute_with_the_flag_on_requires_the_plan_source_columns(fake_chainsolvers_module):
    """The ON branch links on ``(source_H_ID, source_P_ID)``; a producer without them must
    fail with a named error rather than a bare pandas KeyError deep inside the link build."""
    ctx = _ExecuteCtx(df_persons=_persons_without_plan_source(),
                      escort_passive_joint_location=True)
    with pytest.raises(RuntimeError) as excinfo:
        sc.execute(ctx)
    message = str(excinfo.value)
    assert message.startswith("[braunschweig.secondary_chainsolvers]")
    assert "escort_passive_joint_location" in message
    assert "source_H_ID" in message and "source_P_ID" in message
    # The stage whose frame is missing them, so the operator knows WHERE to look.
    assert "synthesis.population.sampled" in message
    # The ON branch is the one that reads the persons frame a second time (the first read
    # is _prepare_primary's); the guard fires on that second read, before any link build.
    assert ctx.stage_reads.count("synthesis.population.sampled") == 2


def test_execute_with_the_flag_on_and_the_surrogate_on_requires_hp_alter(fake_chainsolvers_module):
    """Parametrised twin of the test above (M10): with escort_passive_joint_surrogate ALSO
    on, the persons_columns guard additionally requires HP_ALTER (the surrogate age floor
    reads it) and names both the missing column and the surrogate key in the error, not
    just escort_passive_joint_location. Previously only the server smoke exercised this
    branch of the guard; test_execute_with_the_flag_on_requires_the_plan_source_columns
    above pins escort_passive_joint_surrogate=False (correctly) so HP_ALTER was never
    required there."""
    ctx = _ExecuteCtx(df_persons=_persons_without_hp_alter(),
                      escort_passive_joint_location=True,
                      escort_passive_joint_surrogate=True)
    with pytest.raises(RuntimeError) as excinfo:
        sc.execute(ctx)
    message = str(excinfo.value)
    assert message.startswith("[braunschweig.secondary_chainsolvers]")
    assert "escort_passive_joint_location" in message
    assert "escort_passive_joint_surrogate" in message
    assert "HP_ALTER" in message
    assert ctx.stage_reads.count("synthesis.population.sampled") == 2


def test_execute_with_the_flag_off_solves_once_and_reads_the_persons_frame_once(
        monkeypatch, fake_chainsolvers_module):
    """The OFF path's contract: ONE pass over the whole population, the two-pass
    composition never entered, and no second read of ``synthesis.population.sampled``."""
    solve_calls, compose_calls = [], []
    monkeypatch.setattr(sc, "_solve_problem_set", _recording_execute_solve(solve_calls))

    def _must_not_compose(*args, **kwargs):
        compose_calls.append((args, kwargs))
        raise AssertionError("_compose_two_pass ran on the OFF path")

    monkeypatch.setattr(sc, "_compose_two_pass", _must_not_compose)
    ctx = _ExecuteCtx(df_persons=_persons_without_plan_source(),
                      escort_passive_joint_location=False)

    df_locations, df_convergence = sc.execute(ctx)

    assert compose_calls == []
    assert len(solve_calls) == 1
    assert solve_calls[0]["persons"] == [1, 2]  # one pass over everybody
    # None, not an empty dict: the #201 escort link is OFF, so there is no anchor table.
    assert solve_calls[0]["anchors"] is None
    # No pass_label keyword at all -- NOT passing it is what keeps the one-pass printed
    # lines byte-identical and keeps the default stated in exactly one place.
    assert solve_calls[0]["kwargs"] == {}
    assert ctx.stage_reads.count("synthesis.population.sampled") == 1
    assert len(df_locations) == 0 and len(df_convergence) == 0


# --- RNG call-order guard for the _solve_problem_set extraction --------------------
# _solve_problem_set is execute()'s former solve section moved verbatim, and the evidence
# that the move preserved the RNG stream is a ONE-OFF manual comparison of two cache
# pickles -- a check nothing in tests/ repeats. test_the_rng_consuming_calls_of_one_pass_
# keep_their_order below is the standing guard that manual A/B cannot be: it pins the
# ORDER of the RNG-consuming calls within one pass, and that all of them draw from the ONE
# shared RandomState. A golden output frame would need the real chainsolvers solver and a
# real candidate set, so it does not belong in a unit test; the call order is the part an
# edit can break silently.
#
# This guard drives the SERIAL path only (the stub context below sets "parallel_enabled":
# False), so it does not cover _solve_chains_parallel, where per-shard seeding happens.

class _RecordingRandom:
    """Records every method ``_solve_problem_set`` calls on the shared RNG.

    Deliberately NOT a ``numpy.random.RandomState`` subclass: an unrecorded draw method
    would then pass through silently, and an unrecorded draw is exactly what this guard
    exists to catch. Draws are delegated to a real seeded ``RandomState`` so the values
    are the ones the production stream would yield.
    """

    def __init__(self, calls, seed=0):
        self._calls = calls
        self._random = np.random.RandomState(seed)

    def randint(self, *args, **kwargs):
        self._calls.append("random.randint")
        return self._random.randint(*args, **kwargs)

    def __getattr__(self, name):
        raise AssertionError(
            f"_solve_problem_set used the shared RNG via {name!r}, which this order guard "
            "does not record. Add it to _RecordingRandom AND to the expected sequence, "
            "after confirming the new draw is intended -- an added, removed or reordered "
            "draw changes every placed location downstream of it.")


def _order_guard_trips():
    """Two persons, each home -> shop -> home: the smallest frame yielding TWO assignment
    problems, so the two fallback calls can be told apart by their problem indices."""
    return pd.DataFrame({
        "person_id": [1, 1, 2, 2], "trip_index": [0, 1, 0, 1],
        "preceding_purpose": ["home", "shop", "home", "shop"],
        "following_purpose": ["shop", "home", "shop", "home"],
        "mode": ["car"] * 4, "travel_time": [600.0, 600.0, 600.0, 600.0],
    })


def _order_guard_primary():
    return pd.DataFrame({
        "person_id": [1, 2],
        "home": [Point(0.0, 0.0), Point(0.0, 0.0)],
        "work": [Point(1000.0, 0.0), Point(1000.0, 0.0)],
        "education": [Point(0.0, 1000.0), Point(0.0, 1000.0)],
    })


def _order_guard_shared(random_wrapper, df_secondary):
    """The shared solve state ``_solve_problem_set`` reads, with every decider OFF."""
    return {
        "distance_distributions": {}, "leisure_corr": 2.0, "random": random_wrapper,
        "shop_subtype_decider": None, "leisure_subtype_decider": None,
        "other_subtype_decider": None, "escort_location_decider": None,
        "escort_distance_factor_map": None, "srv_location_decider": None,
        "fallback_strategy": "rda", "rda_index_cache": {},
        "df_secondary": df_secondary, "df_secondary_legacy": df_secondary,
        "scorer_spec": None, "locations_df": pd.DataFrame(),
        "solver_name": sc.DEFAULT_CHAIN_SOLVER, "parallel_enabled": False,
        "configured_procs": 1, "shard_attempts": 1, "crs": "EPSG:25832",
    }


def test_the_rng_consuming_calls_of_one_pass_keep_their_order(monkeypatch):
    calls, received_random, solve_args, index_builds = [], [], [], []
    shared_random = _RecordingRandom(calls)
    df_secondary = _one_candidate_frame()

    def fake_build_plans_df(problems, distance_distributions, leisure_corr, random, **kwargs):
        calls.append("_build_plans_df")
        received_random.append(random)
        plans_df = pd.DataFrame({"unique_person_id": ["1", "2"], "to_act_type": ["shop", "shop"]})
        # Problem 0 is bounded and will fail in the solve; problem 1 is unbounded.
        problem_meta = [{"problem_idx": 0, "person_id": 1, "activity_index": 1,
                         "n_secondary": 1}]
        return plans_df, problem_meta, [1], {}, {}

    def fake_rda_fallback_place(problems, problem_indices, rda_index, distance_distributions,
                                leisure_correction_factor, random, crs, *, pass_label=""):
        calls.append("_rda_fallback_place%s" % (tuple(problem_indices),))
        received_random.append(random)
        return [], []

    def fake_solve_person_shard(args):
        calls.append("solve")
        solve_args.append(args)
        return 0, sc._empty_chain_result_df(), [0]

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError(
            "the 'random' fallback strategy ran while 'rda' was configured")

    monkeypatch.setattr(sc, "_build_plans_df", fake_build_plans_df)
    monkeypatch.setattr(sc, "_rda_fallback_place", fake_rda_fallback_place)
    monkeypatch.setattr(sc, "_fallback_place", _must_not_be_called)
    monkeypatch.setattr(sc, "_build_rda_candidate_index",
                        lambda frame: index_builds.append(frame) or "rda-index")
    monkeypatch.setattr(sc, "_init_chain_worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(sc, "_solve_person_shard", fake_solve_person_shard)

    df_locations, df_convergence, report = sc._solve_problem_set(
        _order_guard_trips(), _order_guard_primary(), None,
        _order_guard_shared(shared_random, df_secondary))

    # The verbatim order of the pass: distance draws, the unbounded fallback, the base-seed
    # draw, the solve, then the failed-bounded fallback. The problem indices in the two
    # fallback entries also pin WHICH set each call received.
    assert calls == ["_build_plans_df", "_rda_fallback_place(1,)", "random.randint",
                     "solve", "_rda_fallback_place(0,)"]
    # ONE stream: the plans build, the base seed and both fallbacks share the same object,
    # so a future edit handing any of them a fresh RandomState fails here.
    assert received_random and all(r is shared_random for r in received_random)
    # base_seed is the FIRST draw of that stream (nothing above it consumes one), which a
    # freshly seeded RandomState could not reproduce by construction.
    assert solve_args[0][3] == np.random.RandomState(0).randint(0, 2**31 - 1)
    assert isinstance(solve_args[0][3], int)
    # The RDA candidate index is built at most once per pass, and on the LEGACY frame.
    assert len(index_builds) == 1 and index_builds[0] is df_secondary
    assert report["n_unbounded"] == 1 and report["n_failed_bounded"] == 1
    assert report["n_problems"] == 2
    assert len(df_locations) == 0 and len(df_convergence) == 1


def test_configure_declares_the_surrogate_keys_with_their_defaults():
    ctx = _configure_context()
    sc.configure(ctx)
    assert ctx.registered["escort_passive_joint_surrogate"] is False
    assert ctx.registered["escort_passive_joint_surrogate_max_gap_minutes"] == 15.0
    assert ctx.registered["escort_passive_joint_surrogate_min_age_years"] == 14
    assert ctx.registered["escort_passive_joint_surrogate_require_same_purpose"] is True


def test_configure_rejects_the_surrogate_flag_without_joint_location():
    ctx = _configure_context(**_base_overrides(escort_passive_joint_surrogate=True,
                                               escort_passive_joint_location=False))
    with pytest.raises(ValueError, match="requires escort_passive_joint_location"):
        sc.configure(ctx)


def test_configure_accepts_the_surrogate_flag_on_top_of_joint_location():
    ctx = _configure_context(**_base_overrides(escort_passive_joint_surrogate=True,
                                               escort_passive_joint_location=True))
    sc.configure(ctx)
    assert ctx.registered["escort_passive_joint_surrogate"] is True


@pytest.mark.parametrize("key,value", [
    ("escort_passive_joint_surrogate_max_gap_minutes", 0),
    ("escort_passive_joint_surrogate_max_gap_minutes", -5.0),
    ("escort_passive_joint_surrogate_min_age_years", 0),
    ("escort_passive_joint_surrogate_min_age_years", -1),
])
def test_configure_rejects_non_positive_surrogate_parameters(key, value):
    ctx = _configure_context(**_base_overrides(**{key: value}))
    with pytest.raises(ValueError, match=f"{key} must be > 0"):
        sc.configure(ctx)


# --- surrogate rescue wiring (#409) --------------------------------------------------
from braunschweig.synthesis.locations.passive_joint_links import (  # noqa: E402
    LINK_COLUMNS, LINK_SOURCE_PLAN_SOURCE, LINK_SOURCE_SURROGATE, build_passive_joint_links,
)


def _wiring_persons():
    # hh 10: adult 1 + identity-linked child 2; hh 20: adult 4 + child 5 whose donor adult
    # (source 300/1) is absent -> rescued onto adult 4's shop trip 5 minutes later.
    return pd.DataFrame({
        "person_id": [1, 2, 4, 5], "household_id": [10, 10, 20, 20],
        "source_H_ID": [100, 100, 900, 300], "source_P_ID": [1, 2, 1, 2],
        "HP_ALTER": [40, 8, 40, 8],
    })


def _wiring_trips():
    nan = np.nan
    return pd.DataFrame({
        "person_id":               [1,      1,      2,        2,      4,      4,      5,        5],
        "trip_index":              [0,      1,      0,        1,      0,      1,      0,        1],
        "preceding_purpose":       ["home", "shop", "home",   "shop", "home", "shop", "home",   "shop"],
        "following_purpose":       ["shop", "home", "shop",   "home", "shop", "home", "shop",   "home"],
        "departure_time":          [28800., 36000., 28800.,   36000., 29100., 36000., 28800.,   36000.],
        "W_ID":                    [1,      2,      3,        4,      5,      6,      7,        8],
        "passive_pair_status":     [nan,    nan,    "paired", nan,    nan,    nan,    "paired", nan],
        "passive_pair_adult_p_id": [nan,    nan,    1.0,      nan,    nan,    nan,    1.0,      nan],
        "passive_pair_adult_w_id": [nan,    nan,    1.0,      nan,    nan,    nan,    50.0,     nan],
    })


def test_link_table_without_the_rescue_is_exactly_todays_identity_table():
    links, link_stats, rescue_stats = sc._passive_joint_link_table(
        _wiring_persons(), _wiring_trips(), surrogate_enabled=False,
        max_gap_minutes=15.0, min_age_years=14, require_same_purpose=True)
    expected, expected_stats = build_passive_joint_links(_wiring_persons(), _wiring_trips())
    pd.testing.assert_frame_equal(links, expected)
    assert list(links.columns) == LINK_COLUMNS          # no link_source column on the OFF path
    assert link_stats == expected_stats and rescue_stats is None


def test_link_table_with_the_rescue_appends_provenance_tagged_surrogate_rows():
    links, link_stats, rescue_stats = sc._passive_joint_link_table(
        _wiring_persons(), _wiring_trips(), surrogate_enabled=True,
        max_gap_minutes=15.0, min_age_years=14, require_same_purpose=True)
    assert list(links.columns) == LINK_COLUMNS + ["link_source"]
    by_child = links.set_index("child_person_id")
    assert by_child.loc[2, "link_source"] == LINK_SOURCE_PLAN_SOURCE
    assert by_child.loc[5, "link_source"] == LINK_SOURCE_SURROGATE
    assert by_child.loc[5, "adult_person_id"] == 4 and by_child.loc[5, "adult_activity_index"] == 1
    assert link_stats["n_linked"] == 1 and rescue_stats["n_surrogate_linked"] == 1


def test_two_pass_places_a_surrogate_linked_child_at_the_surrogates_location():
    links, _link_stats, _rescue_stats = sc._passive_joint_link_table(
        _wiring_persons(), _wiring_trips(), surrogate_enabled=True,
        max_gap_minutes=15.0, min_age_years=14, require_same_purpose=True)
    calls = []
    df_locations, _conv, reports, anchor_stats = sc._compose_two_pass(
        _wiring_trips(), df_primary=None, escort_activity_anchors=None,
        links=links, shared={"crs": "EPSG:25832"}, solve=_fake_solve_factory(calls))
    # Both children are pass-2 persons; both adults were placed in pass 1.
    assert calls[0]["persons"] == [1, 4] and calls[1]["persons"] == [2, 5]
    child_5 = df_locations[(df_locations["person_id"] == 5) & (df_locations["activity_index"] == 1)]
    assert len(child_5) == 1
    assert child_5["location_id"].iloc[0] == "loc_4" and child_5["geometry"].iloc[0] == Point(4, 4)
    assert anchor_stats == {"n_links": 2, "n_resolved": 2, "n_unresolved": 0}
    assert len(reports) == 2


def _rescue_stats(n_candidates, n_linked, ineligible=0, no_activity=0, gap_exceeded=0):
    return {"n_rescue_candidates": n_candidates, "n_surrogate_linked": n_linked,
            "n_rescue_ineligible_child_purpose": ineligible,
            "n_rescue_no_candidate_activity": no_activity, "n_rescue_gap_exceeded": gap_exceeded,
            "n_rescue_purpose_mismatch_rows": 0, "n_rescue_cyclic_dropped_rows": 0,
            "rescue_rate": (n_linked / n_candidates) if n_candidates else float("nan")}


def test_the_surrogate_summary_line_carries_the_rate_and_the_combined_total():
    line = sc._passive_joint_surrogate_summary(
        _link_stats(n_passive_paired=58, n_linked=12, n_adult_not_in_household=27),
        _rescue_stats(27, 6, ineligible=10, no_activity=3, gap_exceeded=8))
    assert line.startswith("[braunschweig.secondary_chainsolvers] passive joint surrogate: 6/27")
    assert "(22.2%)" in line
    assert "child purpose not secondary 10" in line and "gap exceeded 8" in line
    assert "total linked 18/58 (plan-source 12 + surrogate 6)" in line


def test_the_surrogate_summary_line_warns_when_material_exists_but_nothing_links():
    line = sc._passive_joint_surrogate_summary(
        _link_stats(n_passive_paired=58, n_linked=12, n_adult_not_in_household=27),
        _rescue_stats(27, 0, ineligible=10, no_activity=3, gap_exceeded=14))
    assert line.startswith("[braunschweig.secondary_chainsolvers] WARNING: passive joint surrogate: 0/27")


def test_the_surrogate_summary_line_stays_plain_when_no_leg_had_any_material():
    line = sc._passive_joint_surrogate_summary(
        _link_stats(n_passive_paired=3, n_linked=1, n_adult_not_in_household=2),
        _rescue_stats(2, 0, ineligible=2))
    assert "WARNING" not in line and "0/2" in line
