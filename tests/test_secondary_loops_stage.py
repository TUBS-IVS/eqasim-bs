"""Strolls and dog walks in the secondary location stage and the facility check (issue #679, C4).

With ``secondary_loop_zero_displacement`` on, the stage flags the loop legs on the original trips,
takes every loop activity that is neither anchored or linked itself nor the first loop after an
outside stay out of the chains, moves the anchors and links of the collapsed persons to the
collapsed numbering, solves the rest, and puts each loop back at the place of the activity it
starts from (``secondary_chainsolvers.loops``). The facility check then accepts a copied home, work
or education place only under its own kind of facility id.

The stage runs through its real ``execute`` with the all-flags-off stub context of
``tests/test_passive_joint_two_pass.py``; only the solve is replaced, by a stand-in that builds
the real assignment problems (``find_assignment_problems``) and draws one number per variable
activity from the stage's shared random stream. Two runs therefore give the same rows exactly
when they hand the solver the same problems in the same order.
"""
from __future__ import annotations

import json
import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.matsim.scenario import facilities as bs_facilities
from braunschweig.popsim.stage.config_keys import (
    KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS, KEY_SECONDARY_MID_WEEKDAY_LEGS_ONLY,
)
from braunschweig.synthesis.locations import escort_links
from braunschweig.synthesis.locations import secondary_chainsolvers as sc
from braunschweig.synthesis.locations.secondary_chainsolvers import loops
from synthesis.population.spatial.secondary.problems import find_assignment_problems
from tests.test_facilities_portal_gates import _FacilitiesContext, _realised
from tests.test_passive_joint_two_pass import (  # noqa: F401  (fake_chainsolvers_module: fixture)
    _ExecuteCtx, fake_chainsolvers_module,
)
from tests.test_secondary_loops import _mid_persons

CRS = "EPSG:25832"
REPORT_FILENAME = "secondary_locations_report.json"
LOCATION_COLUMNS = ["person_id", "activity_index", "location_id", "geometry"]

#: Every trip lasts 30 minutes: travel-time band 3 of ``purpose_subtype.tt_band``, the band of the
#: MiD cells below.
TRAVEL_TIME_S = 1800.0


def _mid_wege():
    """MiD legs on foot: from home with a way home after it every leisure leg is a stroll
    (P = 1); from home without a way home none is (P = 0). Both cells reach min_obs = 30."""
    return _mid_persons([(30, [(7, 710), (8, 2202)]), (30, [(7, 701)])])


def _trips(rows):
    """Trips from ``(person_id, preceding, following, mode, W_ZWECK, W_ZWD, departure_hour)``.

    ``trip_index``, ``is_first_trip`` and ``is_last_trip`` follow the row order of each person;
    every trip lasts ``TRAVEL_TIME_S``.
    """
    frame = pd.DataFrame(rows, columns=["person_id", "preceding_purpose", "following_purpose",
                                        "mode", "W_ZWECK", "W_ZWD", "departure_hour"])
    frame["trip_index"] = frame.groupby("person_id").cumcount()
    count = frame.groupby("person_id")["trip_index"].transform("size")
    frame["is_first_trip"] = frame["trip_index"] == 0
    frame["is_last_trip"] = frame["trip_index"] == count - 1
    frame["departure_time"] = 3600.0 * frame.pop("departure_hour")
    frame["arrival_time"] = frame["departure_time"] + TRAVEL_TIME_S
    return frame


def _stage_values(person_ids, trips, *, persons_extra=None):
    """The stage outputs execute() reads: household 10 * p for person p, every person with a home,
    a work place ``work_<p>`` and an education place ``edu_<p>``."""
    persons = pd.DataFrame({"person_id": person_ids,
                            "household_id": [10 * p for p in person_ids]})
    for column, values in (persons_extra or {}).items():
        persons[column] = values
    home = gpd.GeoDataFrame({"household_id": [10 * p for p in person_ids],
                             "geometry": [Point(100.0 * p, 0.0) for p in person_ids]}, crs=CRS)
    work = gpd.GeoDataFrame({"person_id": person_ids,
                             "location_id": [f"work_{p}" for p in person_ids],
                             "geometry": [Point(100.0 * p, 50.0) for p in person_ids]}, crs=CRS)
    education = gpd.GeoDataFrame({"person_id": person_ids,
                                  "location_id": [f"edu_{p}" for p in person_ids],
                                  "geometry": [Point(100.0 * p, 70.0) for p in person_ids]},
                                 crs=CRS)
    return persons, {
        "synthesis.population.trips.final": trips,
        "synthesis.population.sampled": persons,
        "synthesis.population.spatial.home.locations": home,
        "synthesis.population.spatial.primary.locations": (work, education),
    }


class _LoopExecuteCtx(_ExecuteCtx):
    """``_ExecuteCtx`` with this file's stage values and the stage directory ``path()``."""

    def __init__(self, tmp_path, person_ids, trips, *, persons_extra=None, **config):
        persons, stages = _stage_values(person_ids, trips, persons_extra=persons_extra)
        config.setdefault("escort_passive_joint_location", False)
        super().__init__(df_persons=persons, **config)
        self._stages.update(stages)
        self._path = tmp_path

    def path(self):
        return str(self._path)


def _loop_config(enabled):
    if not enabled:
        return {loops.KEY_ENABLED: False}
    return {loops.KEY_ENABLED: True,
            "braunschweig.population.popsim.mid_dir": "mid-dir-of-the-test",
            "secondary_distance_min_obs": 30,
            KEY_SECONDARY_MID_WEEKDAY_LEGS_ONLY: True,
            KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS: False}


def _problem_solve(calls):
    """Stand-in for ``_solve_problem_set``: one row per variable activity of the real assignment
    problems, at a place drawn from the shared stream; the id names the purpose and the modes of
    the problem's legs, so a changed chain shows in the id. Every call appends ``(trips, anchors)``
    to ``calls``."""
    def solve(df_trips_pass, df_primary, activity_anchors, shared, **kwargs):
        calls.append((df_trips_pass.copy(), dict(activity_anchors or {})))
        random = shared["random"]
        records = []
        for problem in find_assignment_problems(df_trips_pass, df_primary,
                                                activity_anchors=activity_anchors):
            for offset, purpose in enumerate(problem["purposes"]):
                draw = float(random.random_sample())
                records.append((problem["person_id"], problem["activity_index"] + offset,
                                f"{purpose}|{'/'.join(problem['modes'])}|{draw:.12f}",
                                Point(1000.0 * draw, float(offset))))
        df_loc = gpd.GeoDataFrame(pd.DataFrame.from_records(records, columns=LOCATION_COLUMNS),
                                  geometry="geometry", crs=shared["crs"])
        df_conv = pd.DataFrame({"valid": [True] * len(records), "size": [1] * len(records)})
        report = {"n_problems": len(records), "n_unbounded": 0, "n_failed_bounded": 0,
                  "subtype_stats": {}, "desired_by_category": {}, "n_plan_rows": len(records)}
        return df_loc, df_conv, report
    return solve


@pytest.fixture
def stage_doubles(monkeypatch, fake_chainsolvers_module):
    """The solve stand-in and the MiD table; returns the list of trips frames the solve saw."""
    calls = []
    monkeypatch.setattr(sc, "_solve_problem_set", _problem_solve(calls))
    from braunschweig.popsim.mid import donor
    monkeypatch.setattr(donor, "load_mid_wege", lambda mid_dir: _mid_wege())
    return calls


def _row(df_locations, person_id, activity_index):
    rows = df_locations[(df_locations["person_id"] == person_id)
                        & (df_locations["activity_index"] == activity_index)]
    assert len(rows) == 1, f"expected one row for ({person_id}, {activity_index}), got {len(rows)}"
    return rows.iloc[0]


# ---------------------------------------------------------------------------
# Placement: run A has the loops, run B the same diary with the loop trips removed by hand
# ---------------------------------------------------------------------------

#: S = a labelled stroll (710) or dog walk (711), D = a leisure walk without a detail code that the
#: MiD cell (from home, way home follows) makes a loop by draw.
#:   1: home shop S leisure home      2: home work S home       3: home D home
#:   4: home S S shop home            5: home education S home   (S by bicycle)
TRIPS_WITH_LOOPS = [
    (1, "home", "shop", "car", 4, 2202, 8),
    (1, "shop", "leisure", "walk", 7, 710, 9),            # S
    (1, "leisure", "leisure", "bicycle", 7, 701, 10),
    (1, "leisure", "home", "pt", 8, 2202, 11),
    (2, "home", "work", "car", 1, 2202, 7),
    (2, "work", "leisure", "walk", 7, 710, 12),           # S
    (2, "leisure", "home", "bicycle", 8, 2202, 13),
    (3, "home", "leisure", "walk", 7, 2202, 9),           # D
    (3, "leisure", "home", "walk", 8, 2202, 10),
    (4, "home", "leisure", "walk", 7, 711, 8),            # S
    (4, "leisure", "leisure", "walk", 7, 710, 9),         # S
    (4, "leisure", "shop", "car", 4, 2202, 10),
    (4, "shop", "home", "car", 8, 2202, 11),
    (5, "home", "education", "pt", 3, 2202, 7),
    (5, "education", "leisure", "bicycle", 7, 710, 15),   # S
    (5, "leisure", "home", "pt", 8, 2202, 16),
]

#: The same diaries with every loop trip removed by hand: the trip after a loop (run) leaves the
#: activity before it, so person 3 keeps one trip from home to home.
TRIPS_WITHOUT_LOOPS = [
    (1, "home", "shop", "car", 4, 2202, 8),
    (1, "shop", "leisure", "bicycle", 7, 701, 10),
    (1, "leisure", "home", "pt", 8, 2202, 11),
    (2, "home", "work", "car", 1, 2202, 7),
    (2, "work", "home", "bicycle", 8, 2202, 13),
    (3, "home", "home", "walk", 8, 2202, 10),
    (4, "home", "shop", "car", 4, 2202, 10),
    (4, "shop", "home", "car", 8, 2202, 11),
    (5, "home", "education", "pt", 3, 2202, 7),
    (5, "education", "home", "pt", 8, 2202, 16),
]

#: (person_id, activity index without loops) -> activity index in the diary with loops, for the
#: secondary activities that are no loops.
WITHOUT_TO_WITH_LOOPS = {(1, 1): 1, (1, 2): 3, (4, 1): 3}

PERSONS = [1, 2, 3, 4, 5]

#: Persons with an anchored or linked activity, each with loops before and after it, so the
#: collapse moves the anchored activity to another index:
#:   6: home S escort S shop home              the escort activity is anchored at a child's
#:                                             school (#201 household link)
#:   7: home S outside S S shop home           a portal stay (#442); the first loop after it is
#:                                             kept, the second copies the kept one
#:   8: home S shop S home                     adult side of a passive joint link (#385)
#:   9: home S shop S leisure home             child side: the shop is taken along with adult 8
ANCHORED_TRIPS_WITH_LOOPS = [
    (6, "home", "leisure", "walk", 7, 710, 7),            # S
    (6, "leisure", "escort", "car", 6, 2202, 8),
    (6, "escort", "leisure", "walk", 7, 710, 9),          # S after the drop-off
    (6, "leisure", "shop", "car", 4, 2202, 10),
    (6, "shop", "home", "car", 8, 2202, 11),
    (7, "home", "leisure", "walk", 7, 710, 7),            # S
    (7, "leisure", "outside", "car", 4, 2202, 8),
    (7, "outside", "leisure", "walk", 7, 710, 12),        # S kept: after the outside stay
    (7, "leisure", "leisure", "walk", 7, 711, 13),        # S
    (7, "leisure", "shop", "car", 4, 2202, 14),
    (7, "shop", "home", "car", 8, 2202, 15),
    (8, "home", "leisure", "walk", 7, 710, 7),            # S
    (8, "leisure", "shop", "car", 4, 2202, 8),
    (8, "shop", "leisure", "walk", 7, 711, 9),            # S
    (8, "leisure", "home", "car", 8, 2202, 10),
    (9, "home", "leisure", "walk", 7, 710, 7),            # S
    (9, "leisure", "shop", "car", 4, 2202, 8),
    (9, "shop", "leisure", "walk", 7, 711, 9),            # S after the joint activity
    (9, "leisure", "leisure", "bicycle", 7, 701, 10),
    (9, "leisure", "home", "car", 8, 2202, 11),
]

#: The same diaries with the loops removed by hand that the stage collapses (person 7 keeps the
#: loop after its outside stay, which the solver places like any leisure activity).
ANCHORED_TRIPS_WITHOUT_LOOPS = [
    (6, "home", "escort", "car", 6, 2202, 8),
    (6, "escort", "shop", "car", 4, 2202, 10),
    (6, "shop", "home", "car", 8, 2202, 11),
    (7, "home", "outside", "car", 4, 2202, 8),
    (7, "outside", "leisure", "walk", 7, 710, 12),
    (7, "leisure", "shop", "car", 4, 2202, 14),
    (7, "shop", "home", "car", 8, 2202, 15),
    (8, "home", "shop", "car", 4, 2202, 8),
    (8, "shop", "home", "car", 8, 2202, 10),
    (9, "home", "shop", "car", 4, 2202, 8),
    (9, "shop", "leisure", "bicycle", 7, 701, 10),
    (9, "leisure", "home", "car", 8, 2202, 11),
]

#: As ``WITHOUT_TO_WITH_LOOPS``, for every row the run without loops writes: solver rows, the
#: linked escort row (6, 1), the portal row (7, 1) and the joint anchor row of the child (9, 1).
ANCHORED_WITHOUT_TO_WITH_LOOPS = {(6, 1): 2, (6, 2): 4, (7, 1): 2, (7, 2): 3, (7, 3): 5,
                                  (8, 1): 2, (9, 1): 2, (9, 2): 4}

ANCHORED_PERSONS = [6, 7, 8, 9]

#: The school of the child escorter 6 drops off, and the gate of person 7's outside stay.
SCHOOL_ID, SCHOOL_POINT = "edu_99", Point(9900.0, 70.0)
GATE_POINT = Point(40000.0, 0.0)

#: What ``_passive_joint_link_summary`` reads from the link statistics.
LINK_STATS = {"n_passive_paired": 1, "n_linked": 1, "link_rate": 1.0,
              "n_adult_not_in_household": 0, "n_adult_leg_missing": 0,
              "n_purpose_not_secondary": 0}


def _run(tmp_path, trips_rows, enabled):
    ctx = _LoopExecuteCtx(tmp_path, PERSONS, _trips(trips_rows), **_loop_config(enabled))
    df_locations, _df_convergence = sc.execute(ctx)
    return df_locations


def _patch_escort_link(monkeypatch, person_id, school_id, school_point):
    """Anchor ``person_id``'s escort activities at a child's school (#201 household link).

    The stand-in for ``_apply_escort_household_link`` skips only the household search
    (``build_escort_links``): the real anchor assignment and trip rewrite run on the trips the
    stage hands over, so the anchors are in the numbering of that run."""
    df_links = pd.DataFrame({"person_id": [person_id], "child_rank": [0],
                             "location_id": [school_id], "geometry": [school_point]})

    def link(context, df_trips):
        rows, _stats = escort_links.assign_escort_anchors(df_trips, df_links)
        anchors = {(row.person_id, row.activity_index): row.geometry
                   for row in rows.itertuples(index=False)}
        return sc.rewrite_linked_escort_trips(df_trips, rows), rows, anchors

    monkeypatch.setattr(sc, "_apply_escort_household_link", link)


def _portal_anchors(person_id, activity_index):
    """The anchors frame of the portal stage for one outside stay (#442)."""
    return gpd.GeoDataFrame({"person_id": [person_id], "activity_index": [activity_index],
                             "gate_id": ["gate_e"], "kind": ["road"], "mode": ["car"]},
                            geometry=[GATE_POINT], crs=CRS)


def _patch_links(monkeypatch, links):
    """The passive joint link table (#385) from ``(child, child activity, adult, adult activity)``."""
    table = pd.DataFrame(links, columns=["child_person_id", "child_activity_index",
                                         "adult_person_id", "adult_activity_index"]).assign(
        adult_purpose="shop")
    monkeypatch.setattr(sc, "_passive_joint_link_table",
                        lambda *args, **kwargs: (table, LINK_STATS, None))


def _anchored_run(tmp_path, monkeypatch, trips_rows, enabled, *, outside_index, links):
    """Persons 1 to 9 through the two-pass path, with person 6's escort link, person 7's portal
    stay at ``outside_index`` and the passive joint ``links``, all in the numbering of the run."""
    person_ids = PERSONS + ANCHORED_PERSONS
    _patch_escort_link(monkeypatch, 6, SCHOOL_ID, SCHOOL_POINT)
    _patch_links(monkeypatch, links)
    ctx = _LoopExecuteCtx(tmp_path, person_ids, _trips(trips_rows),
                          persons_extra={"source_H_ID": person_ids, "source_P_ID": [1] * 9},
                          **_loop_config(enabled), **{"braunschweig.portal.enabled": True,
                                                       "escort_passive_joint_location": True})
    ctx._stages[sc.PORTAL_ANCHORS_STAGE] = _portal_anchors(7, outside_index)
    df_locations, _df_convergence = sc.execute(ctx)
    return df_locations


def _home(person_id):
    return ("home", f"home_{10 * person_id}", Point(100.0 * person_id, 0.0))


def _copy_of(df_locations, person_id, activity_index):
    origin = _row(df_locations, person_id, activity_index)
    return ("secondary", origin["location_id"], origin["geometry"])


@pytest.mark.parametrize("composition", ["one_pass", "anchored_two_pass"])
def test_the_stage_places_other_activities_as_if_the_loop_had_been_removed_by_hand(
        tmp_path, stage_doubles, monkeypatch, composition):
    (tmp_path / "with").mkdir()
    (tmp_path / "without").mkdir()
    if composition == "one_pass":
        with_loops = _run(tmp_path / "with", TRIPS_WITH_LOOPS, enabled=True)
        without_loops = _run(tmp_path / "without", TRIPS_WITHOUT_LOOPS, enabled=False)
        without_to_with = WITHOUT_TO_WITH_LOOPS
    else:
        # Escort link, portal stay and joint link are built on each run's own diary: with the
        # loops the escort activity is 2, the outside stay 2 and both joint activities 2; in the
        # diary without them all are 1.
        with_loops = _anchored_run(tmp_path / "with", monkeypatch,
                                   TRIPS_WITH_LOOPS + ANCHORED_TRIPS_WITH_LOOPS, True,
                                   outside_index=2, links=[(9, 2, 8, 2)])
        without_loops = _anchored_run(tmp_path / "without", monkeypatch,
                                      TRIPS_WITHOUT_LOOPS + ANCHORED_TRIPS_WITHOUT_LOOPS, False,
                                      outside_index=1, links=[(9, 1, 8, 1)])
        without_to_with = {**WITHOUT_TO_WITH_LOOPS, **ANCHORED_WITHOUT_TO_WITH_LOOPS}

    # The solver saw the hand-made diary in every pass: every column of every trip (modes, times,
    # travel times, purposes, donor codes) in the same order, and the same anchors (escort, gate,
    # joint) under the same indices; only the row labels differ.
    n_passes = len(stage_doubles) // 2
    assert n_passes == (1 if composition == "one_pass" else 2)
    for (seen_with, anchors_with), (seen_without, anchors_without) in zip(
            stage_doubles[:n_passes], stage_doubles[n_passes:]):
        pd.testing.assert_frame_equal(seen_with.reset_index(drop=True),
                                      seen_without.reset_index(drop=True))
        assert anchors_with == anchors_without

    # Every row of the run without loops (solver, linked escort, portal and joint anchor rows)
    # sits where that run put it.
    assert sorted(zip(without_loops["person_id"], without_loops["activity_index"])) == sorted(
        without_to_with)
    for row in without_loops.itertuples(index=False):
        placed = _row(with_loops, row.person_id,
                      without_to_with[(row.person_id, row.activity_index)])
        assert placed["location_id"] == row.location_id
        assert placed["geometry"].equals(row.geometry)
        assert placed["loop_origin"] is None

    # Every loop activity copies the place of the activity it starts from.
    expected_loops = {
        (1, 2): _copy_of(with_loops, 1, 1),
        (2, 2): ("work", "work_2", Point(200.0, 50.0)),
        (3, 1): _home(3), (4, 1): _home(4), (4, 2): _home(4),
        (5, 2): ("education", "edu_5", Point(500.0, 70.0)),
    }
    if composition == "anchored_two_pass":
        expected_loops.update({
            # The stroll after the drop-off copies the child's school.
            (6, 1): _home(6), (6, 3): ("secondary", SCHOOL_ID, SCHOOL_POINT),
            # The second loop after the outside stay copies the kept loop, never the gate.
            (7, 1): _home(7), (7, 4): _copy_of(with_loops, 7, 3),
            (8, 1): _home(8), (8, 3): _copy_of(with_loops, 8, 2),
            # The child's stroll after the joint shop copies the adult's shop.
            (9, 1): _home(9), (9, 3): _copy_of(with_loops, 8, 2),
        })
    loop_rows = with_loops[with_loops["loop_origin"].notna()]
    assert sorted(zip(loop_rows["person_id"], loop_rows["activity_index"])) == sorted(expected_loops)
    for (person_id, activity_index), (origin, location_id, geometry) in expected_loops.items():
        placed = _row(with_loops, person_id, activity_index)
        assert (placed["loop_origin"], placed["location_id"]) == (origin, location_id)
        assert placed["geometry"].equals(geometry)
    assert len(with_loops) == len(without_loops) + len(expected_loops)
    assert with_loops.crs.to_string() == CRS

    if composition == "anchored_two_pass":
        report = json.loads((tmp_path / "with" / REPORT_FILENAME).read_text(
            encoding="utf-8"))["loops"]
        # 15 loops: 6 of persons 1 to 5, 2 + 3 + 2 + 2 of persons 6 to 9; person 7's loop after
        # the outside stay is the one kept.
        assert (report["loops"], report["collapsed"], report["kept_total"]) == (15, 14, 1)
        assert (report["kept_after_outside"], report["kept_loop_is_linked_activity"]) == (1, 0)
        # The remap is exercised: collapsed loops of persons with an escort link, a portal stay
        # and a passive joint link (both sides).
        assert (report["collapsed_person_has_escort_link"],
                report["collapsed_person_has_portal_stay"],
                report["collapsed_person_has_passive_joint_link"]) == (2, 2, 4)


def test_the_report_gives_loops_by_label_by_draw_and_by_origin(tmp_path, stage_doubles, caplog):
    with caplog.at_level(logging.INFO):
        _run(tmp_path, TRIPS_WITH_LOOPS, enabled=True)

    report = json.loads((tmp_path / REPORT_FILENAME).read_text(encoding="utf-8"))["loops"]
    assert report["enabled"] is True
    # In scope: the five walks into leisure and the two bicycle legs into leisure (person 1's
    # 701 leg is labelled and no loop); person 3's walk is the one leg without a detail code.
    assert report["flags"] == {
        "in_scope": {"bicycle": 2, "walk": 5}, "by_label": {"bicycle": 1, "walk": 4},
        "by_draw": {"bicycle": 0, "walk": 1}, "draws": {"bicycle": 0, "walk": 1},
        "draws_from_marginal": {"bicycle": 0, "walk": 0},
        "missing_detail_code": {"bicycle": 0, "walk": 0}}
    # Share of the draws that took the mode marginal; 0 for a mode without draws.
    assert report["draws_from_marginal_share"] == {"bicycle": 0.0, "walk": 0.0}
    assert report["loops"] == 6
    for reason in ("kept_loop_is_linked_activity", "kept_after_outside", "kept_total"):
        assert (report[reason], report[f"{reason}_rate"]) == (0, 0.0)
    for kind in ("escort_link", "portal_stay", "passive_joint_link"):
        assert report[f"collapsed_person_has_{kind}"] == 0
    assert report["collapsed"] == 6
    assert report["by_origin"] == {"home": 3, "work": 1, "education": 1, "secondary": 1}
    # Persons 2, 3 and 5 had only loops between their home, work and education activities.
    assert report["persons_without_secondary_activity"] == 3
    assert not [record for record in caplog.records
                if record.levelno >= logging.WARNING and "kept" in record.getMessage()]


def test_the_report_gives_the_share_of_draws_from_the_mode_marginal(tmp_path, stage_doubles):
    # Both MiD cells hold 30 legs: with min_obs 31 neither has its own estimate, so person 3's
    # walk without a detail code (the one draw) takes the walk marginal; bicycle has no draw.
    ctx = _LoopExecuteCtx(tmp_path, PERSONS, _trips(TRIPS_WITH_LOOPS),
                          **{**_loop_config(True), "secondary_distance_min_obs": 31})
    sc.execute(ctx)

    report = json.loads((tmp_path / REPORT_FILENAME).read_text(encoding="utf-8"))["loops"]
    assert report["draws_from_marginal_share"] == {"bicycle": 0.0, "walk": 1.0}


def test_the_key_off_gives_todays_result_without_the_loop_origin_column(
        tmp_path, stage_doubles, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("a loop function ran with secondary_loop_zero_displacement off")

    for name in ("estimate_loop_probabilities", "flag_loops", "select_collapsible_loops",
                 "collapse_loops", "to_collapsed_activity_index", "to_collapsed_anchor_keys",
                 "to_collapsed_activity_rows", "to_collapsed_links", "restore_loops"):
        monkeypatch.setattr(loops, name, must_not_run)
    from braunschweig.popsim.mid import donor
    monkeypatch.setattr(donor, "load_mid_wege", must_not_run)

    ctx = _LoopExecuteCtx(tmp_path, PERSONS, _trips(TRIPS_WITH_LOOPS), **_loop_config(False))
    df_locations, _df_convergence = sc.execute(ctx)

    # Today's result is the solver's own output on the uncollapsed trips: the strolls are placed
    # by the solver like any other leisure activity.
    df_primary, _crs = sc._prepare_primary(ctx)
    expected, _conv, _report = _problem_solve([])(
        _trips(TRIPS_WITH_LOOPS).assign(travel_time=TRAVEL_TIME_S), df_primary, None,
        {"random": np.random.RandomState(ctx.config("random_seed")), "crs": CRS})
    assert "loop_origin" not in df_locations.columns
    pd.testing.assert_frame_equal(pd.DataFrame(df_locations), pd.DataFrame(expected))
    assert not (tmp_path / REPORT_FILENAME).exists()


# ---------------------------------------------------------------------------
# The two kinds of loop the stage keeps on the solver
# ---------------------------------------------------------------------------

def _kept_after_outside(monkeypatch):
    """Person 1 strolls right after an outside stay (#442): the gate is a road crossing at the
    region boundary, no place for a stroll, so the stroll is kept and solved."""
    rows = [(1, "home", "outside", "car", 4, 2202, 8),
            (1, "outside", "leisure", "walk", 7, 710, 9),      # S, activity 2
            (1, "leisure", "home", "car", 8, 2202, 10)]
    stages = {sc.PORTAL_ANCHORS_STAGE: _portal_anchors(1, 1)}
    return rows, {"braunschweig.portal.enabled": True}, stages, [(1, 2)]


def _kept_linked(monkeypatch):
    """Child 2 joins adult 1's stroll (#385): the stroll is the linked activity on both sides,
    so neither loop can be taken out of its chain."""
    _patch_links(monkeypatch, [(2, 1, 1, 1)])
    rows = [(person_id, *leg) for person_id in (1, 2) for leg in (
        ("home", "leisure", "walk", 7, 710, 8),                 # S, activity 1
        ("leisure", "home", "walk", 8, 2202, 9))]
    return rows, {"escort_passive_joint_location": True}, {}, [(1, 1), (2, 1)]


KEPT_CASES = {"kept_after_outside": _kept_after_outside,
              "kept_loop_is_linked_activity": _kept_linked}


@pytest.mark.parametrize("reason", sorted(KEPT_CASES))
def test_a_kept_loop_is_placed_by_the_solver_and_counted_by_its_reason(
        tmp_path, stage_doubles, monkeypatch, caplog, reason):
    rows, config, stages, kept_activities = KEPT_CASES[reason](monkeypatch)
    rows = rows + [(3, "home", "leisure", "walk", 7, 710, 8),     # S of a person without anchors
                   (3, "leisure", "home", "walk", 8, 2202, 9)]
    ctx = _LoopExecuteCtx(tmp_path, [1, 2, 3], _trips(rows),
                          persons_extra={"source_H_ID": [100, 100, 300],
                                         "source_P_ID": [1, 2, 1]},
                          **_loop_config(True), **config)
    ctx._stages.update(stages)

    with caplog.at_level(logging.INFO):
        df_locations, _df_convergence = sc.execute(ctx)

    # A kept stroll is placed by the solver like any leisure activity (the child's one at the
    # adult's placed stroll), not copied.
    for person_id, activity_index in kept_activities:
        kept = _row(df_locations, person_id, activity_index)
        assert kept["loop_origin"] is None and kept["location_id"].startswith("leisure|")
    # Person 3's stroll is copied from home.
    copied = _row(df_locations, 3, 1)
    assert (copied["loop_origin"], copied["location_id"]) == ("home", "home_30")

    report = json.loads((tmp_path / REPORT_FILENAME).read_text(encoding="utf-8"))["loops"]
    n_kept = len(kept_activities)
    assert (report["loops"], report["collapsed"]) == (n_kept + 1, 1)
    assert (report[reason], report["kept_total"]) == (n_kept, n_kept)
    assert report["kept_total_rate"] == pytest.approx(n_kept / (n_kept + 1))
    other_reason = ({"kept_after_outside", "kept_loop_is_linked_activity"} - {reason}).pop()
    assert report[other_reason] == 0
    # A kept share of 50 % or more is far above FALLBACK_WARN_RATE: a WARNING, not an INFO line.
    assert [record for record in caplog.records
            if record.levelno == logging.WARNING and "kept" in record.getMessage()]


def test_a_stroll_after_an_escort_drop_off_copies_the_school_and_passes_the_facility_check(
        tmp_path, stage_doubles, monkeypatch):
    # Person 1 drops a child at school edu_1, an education facility of the facilities stage's
    # test frames, and strolls from there.
    school = Point(200.0, 0.0)
    _patch_escort_link(monkeypatch, 1, "edu_1", school)
    rows = [(1, "home", "escort", "car", 6, 2202, 8),
            (1, "escort", "leisure", "walk", 7, 710, 9),       # S, activity 2
            (1, "leisure", "home", "car", 8, 2202, 10)]
    for directory in ("stage", "facilities", "facilities_without_link"):
        (tmp_path / directory).mkdir()
    df_locations, _df_convergence = sc.execute(
        _LoopExecuteCtx(tmp_path / "stage", [1], _trips(rows), **_loop_config(True)))

    stroll = _row(df_locations, 1, 2)
    assert (stroll["loop_origin"], stroll["location_id"]) == ("secondary", "edu_1")
    assert stroll["geometry"].equals(school)
    # The facilities stage accepts the copied school id as it accepts the linked escort row: as a
    # written education facility, and only while the household link is on.
    bs_facilities.execute(_LoopFacilitiesContext(tmp_path / "facilities", df_locations,
                                                 escort_household_link=True))
    assert (tmp_path / "facilities" / "facilities.xml.gz").exists()
    with pytest.raises(RuntimeError, match="edu_1"):
        bs_facilities.execute(_LoopFacilitiesContext(tmp_path / "facilities_without_link",
                                                     df_locations))


# ---------------------------------------------------------------------------
# Facility check
# ---------------------------------------------------------------------------

class _LoopFacilitiesContext(_FacilitiesContext):
    """``_FacilitiesContext`` (household 1, work_1, edu_1, sec_b_1) with the loop switch and the
    #201 household link switch."""

    def __init__(self, tmp_path, realised, enabled=True, escort_household_link=False):
        super().__init__(tmp_path, realised)
        self._enabled = enabled
        self._escort_household_link = escort_household_link

    def config(self, key, default=None, volatile=False):
        if key == loops.KEY_ENABLED:
            return self._enabled
        if key == "escort_household_link":
            return self._escort_household_link
        return super().config(key, default, volatile)


def _realised_with_origins(ids_and_origins):
    realised = _realised([location_id for location_id, _ in ids_and_origins])
    realised["loop_origin"] = pd.Series([origin for _, origin in ids_and_origins], dtype=object)
    return realised


def test_the_facility_check_accepts_loop_rows_at_home_work_and_education(tmp_path):
    realised = _realised_with_origins([("sec_b_1", None), ("home_1", "home"), ("work_1", "work"),
                                       ("edu_1", "education"), ("sec_b_1", "secondary")])
    bs_facilities.execute(_LoopFacilitiesContext(tmp_path, realised))
    assert (tmp_path / "facilities.xml.gz").exists()


@pytest.mark.parametrize("ids_and_origins, message", [
    ([("sec_b_9", None)], "sec_b_9"),                 # an ordinary unknown id
    ([("home_2", "home")], "home_2"),                 # a household without a home facility
    ([("edu_1", "work")], "edu_1"),                   # a written facility of the other kind
    ([("sec_b_1", "home")], "sec_b_1"),               # a secondary facility on a home copy
    ([("work_1", "secondary")], "work_1"),            # a secondary copy is checked as secondary
])
def test_the_facility_check_still_rejects_an_unknown_identifier(tmp_path, ids_and_origins,
                                                                  message):
    realised = _realised_with_origins(ids_and_origins)
    with pytest.raises(RuntimeError, match=message):
        bs_facilities.execute(_LoopFacilitiesContext(tmp_path, realised))


@pytest.mark.parametrize("loop_origin_ids, names_loop_copies", [
    (None, False),                                                    # switch off
    ({"home": {"home_1"}, "work": {"work_1"}, "education": {"edu_1"}}, True),
])
def test_the_coverage_error_names_loop_copies_only_with_the_switch_on(loop_origin_ids,
                                                                      names_loop_copies):
    realised = _realised_with_origins([("sec_b_9", None)])
    with pytest.raises(RuntimeError, match="1 realised secondary location id") as excinfo:
        bs_facilities.validate_secondary_coverage(
            realised, pd.DataFrame({"location_id": ["sec_b_1"]}), loop_origin_ids=loop_origin_ids)
    assert ("loop rows" in str(excinfo.value)) is names_loop_copies


def test_the_facility_check_with_the_switch_on_needs_the_loop_origin_column(tmp_path):
    with pytest.raises(RuntimeError, match="loop_origin"):
        bs_facilities.execute(_LoopFacilitiesContext(tmp_path, _realised(["sec_b_1"])))
