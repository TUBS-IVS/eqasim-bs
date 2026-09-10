"""Tests for the popsim_mid trips_stage (Phase 5g.6 / Task 6).

Verifies that trips_stage.run() returns the canonical synthesis.population.trips
11-column contract plus euclidean_distance, and that the per-person departure-time
jitter preserves within-person trip ordering.
"""

from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import trips_stage


def test_trips_stage_run_returns_contract_columns_and_euclidean():
    persons = pd.DataFrame({"person_id": ["A_1_0_1"], "H_ID": [1], "P_ID": [1]})
    wege = pd.DataFrame({"H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
                         "W_ZWECK": [1, 8], "hvm_imp": [4, 4], "W_SZS": [8, 17], "W_SZM": [0, 0],
                         "W_AZS": [8, 17], "W_AZM": [30, 20], "wegkm_imp": [12.0, 12.0]})
    out = trips_stage.run(persons, wege, random_seed=0)
    for col in ["person_id", "trip_index", "departure_time", "arrival_time",
                "preceding_purpose", "following_purpose", "is_first_trip",
                "is_last_trip", "trip_duration", "activity_duration", "mode",
                "euclidean_distance"]:
        assert col in out.columns
    assert np.allclose(out["euclidean_distance"], 12.0 * 1000 / 1.3)


def test_trips_stage_jitter_is_per_person_keeps_chain_ordered():
    # Two trips for one person; after jitter the within-person ordering must hold.
    persons = pd.DataFrame({"person_id": ["A_1_0_1"], "H_ID": [1], "P_ID": [1]})
    wege = pd.DataFrame({"H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
                         "W_ZWECK": [1, 8], "hvm_imp": [4, 4], "W_SZS": [8, 17], "W_SZM": [0, 0],
                         "W_AZS": [8, 17], "W_AZM": [30, 20], "wegkm_imp": [12.0, 12.0]})
    out = trips_stage.run(persons, wege, random_seed=0).sort_values("trip_index")
    deps = out["departure_time"].tolist()
    assert deps[0] <= deps[1]   # chain ordering preserved (per-person jitter, same offset)


def test_jitter_records_the_applied_offset_per_person():
    """Issue #123 (Phase 0 Task 1): apply_per_person_jitter must record the offset it applied.

    ``departure_time - departure_time_offset_seconds`` must reproduce the pre-jitter (rounded)
    departure -- i.e. later analysis can decompose the model time as raw + offset without
    re-deriving the offset from the RNG stream -- and the recorded offset must be the SAME for
    every trip of one person's chain (one draw per person, per the jitter formula).
    """
    table = pd.DataFrame({
        "person_id":      ["p1", "p1", "p2"],
        "departure_time": [8 * 3600.0, 17 * 3600.0, 9 * 3600.0],
        "arrival_time":   [8 * 3600.0 + 900.0, 17 * 3600.0 + 900.0, 9 * 3600.0 + 900.0],
    })
    pre_jitter_departure = table["departure_time"].copy()
    pre_jitter_arrival = table["arrival_time"].copy()

    out = trips_stage.apply_per_person_jitter(table.copy(), random_seed=7)

    assert trips_stage.OFFSET_COLUMN in out.columns
    recovered_departure = out["departure_time"] - out[trips_stage.OFFSET_COLUMN]
    recovered_arrival = out["arrival_time"] - out[trips_stage.OFFSET_COLUMN]
    assert np.allclose(recovered_departure, np.round(pre_jitter_departure))
    assert np.allclose(recovered_arrival, np.round(pre_jitter_arrival))

    n_distinct_offsets_per_person = out.groupby("person_id")[trips_stage.OFFSET_COLUMN].nunique()
    assert (n_distinct_offsets_per_person == 1).all()   # one draw per person, shared by every trip


def test_jitter_output_matches_the_pre_task_1_golden_values():
    """Golden-master regression (issue #123 review fix round 1, item 3).

    Pins ``departure_time``/``arrival_time`` against values produced by the PRE-#123
    ``apply_per_person_jitter`` -- i.e. the code as it existed at commit ``3acd382d``, BEFORE this
    feature added the ``OFFSET_COLUMN`` write -- so that a future reordering of the offset write
    relative to the existing round/shift lines (or any other accidental change to the RNG
    consumption or rounding of the pre-existing columns) fails LOUDLY here, rather than passing
    silently because ``test_jitter_records_the_applied_offset_per_person`` above only checks
    internal self-consistency (offset vs. departure_time), not an independent reference.

    Provenance of the pinned values: ``braunschweig/popsim/trips_stage.py`` at commit
    ``3acd382d`` (the branch's base commit, immediately before Task 1) was extracted with
    ``git show 3acd382d:braunschweig/popsim/trips_stage.py`` into a scratch file under
    ``.../scratchpad/trips_stage_golden_3acd382d.py``, imported under the module name
    ``trips_stage_golden_3acd382d`` (distinct from ``braunschweig.popsim.trips_stage``) via
    ``importlib.util.spec_from_file_location``, and its ``apply_per_person_jitter`` was run ONCE
    on the fixture and seed below; the printed ``departure_time``/``arrival_time`` lists were
    copied verbatim into this test. The scratch file is not part of the repository -- only the
    resulting pinned literals are committed.
    """
    fixture = pd.DataFrame({
        "person_id":      ["p1", "p1", "p2", "p2", "p2", "p3"],
        "departure_time": [8 * 3600.0, 17 * 3600.0, 7 * 3600.0, 12 * 3600.0, 18 * 3600.0, 600.0],
        "arrival_time":   [8 * 3600.0 + 900.0, 17 * 3600.0 + 900.0,
                           7 * 3600.0 + 600.0, 12 * 3600.0 + 600.0, 18 * 3600.0 + 600.0,
                           900.0],
    })
    seed = 20260910

    out = trips_stage.apply_per_person_jitter(fixture.copy(), random_seed=seed)

    # Pinned from the 3acd382d (pre-#123) apply_per_person_jitter -- see the docstring above.
    expected_departure_time = [27337.0, 59737.0, 26689.0, 44689.0, 66289.0, 622.0]
    expected_arrival_time = [28237.0, 60637.0, 27289.0, 45289.0, 66889.0, 922.0]
    assert out["departure_time"].tolist() == expected_departure_time
    assert out["arrival_time"].tolist() == expected_arrival_time


# ---------------------------------------------------------------------------
# Task 2.3 C: absolute plan-time bound + NaN-free guarantee in trips_stage.run.
# ---------------------------------------------------------------------------

def test_run_asserts_times_within_bound():
    import pandas as pd, pytest
    from braunschweig.popsim import trips_stage
    df = pd.DataFrame({"person_id": [1, 1], "departure_time": [3600.0, 130000.0],
                       "arrival_time": [4000.0, 131000.0]})
    with pytest.raises(AssertionError, match="exceed"):
        trips_stage._assert_time_bound(df, max_seconds=36 * 3600)


def test_run_resamples_coded_time_persons_no_nan_in_output():
    """End-to-end: a coded-time (701) person must come out of trips_stage.run
    with a valid resampled chain (same ZENSUS100m cell donor), never NaN times."""
    persons = pd.DataFrame({
        "person_id": ["pA", "pB"], "H_ID": [1, 2], "P_ID": [1, 1],
        "ZENSUS100m": ["c1", "c1"],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 2], "P_ID": [1, 1], "W_ID": [1, 1],
        "W_ZWECK": [1, 1], "hvm_imp": [4, 4],
        "W_SZS": [701, 8], "W_SZM": [701, 0],
        "W_AZS": [701, 9], "W_AZM": [701, 0],
        "wegkm_imp": [5.0, 5.0],
    })
    out = trips_stage.run(persons, wege, random_seed=0)
    assert set(out["person_id"].unique()) == {"pA", "pB"}
    assert out["departure_time"].notna().all()
    assert out["arrival_time"].notna().all()
    assert (out["departure_time"] <= trips_stage.MAX_PLAN_TIME_SECONDS).all()


# ---------------------------------------------------------------------------
# Task 3: thread escort_purpose through trips_stage.run and the donor sources.
# ---------------------------------------------------------------------------

def test_run_escort_purpose_flag_produces_escort_trips():
    persons = pd.DataFrame({"person_id": [1], "H_ID": [10], "P_ID": [1]})
    wege = pd.DataFrame({
        "H_ID": [10, 10], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [6, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 12], "W_SZM": [0, 0], "W_AZS": [8, 12], "W_AZM": [30, 30],
        "wegkm_imp": [5.0, 5.0], "wegmin_imp1": [30.0, 30.0], "W_GEW": [1.0, 1.0],
    })
    table_on = trips_stage.run(persons, wege, random_seed=1, escort_purpose=True)
    assert "escort" in set(table_on["following_purpose"])
    table_off = trips_stage.run(persons, wege, random_seed=1, escort_purpose=False)
    assert "escort" not in set(table_off["following_purpose"])


def test_entd_source_rejects_escort_purpose():
    from braunschweig.popsim.sources.entd import EntdSource
    with pytest.raises(NotImplementedError, match="escort_purpose"):
        EntdSource().build_trips(
            pd.DataFrame({"person_id": []}), pd.DataFrame(), random_seed=1,
            escort_purpose=True,
        )


# ---------------------------------------------------------------------------
# Issue #256: thread escort_passive_education through trips_stage.run and the
# ENTD source guard (mirrors the #201 escort_purpose tests directly above).
# ---------------------------------------------------------------------------

def test_trips_stage_threads_escort_passive_education():
    """A W_ZWECK-13 (passive escort) leg must become 'education', not 'escort',
    when escort_passive_education is threaded through trips_stage.run alongside
    escort_purpose."""
    persons = pd.DataFrame({"person_id": [1], "H_ID": [10], "P_ID": [1]})
    wege = pd.DataFrame({
        "H_ID": [10, 10], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [13, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 12], "W_SZM": [0, 0], "W_AZS": [8, 12], "W_AZM": [30, 30],
        "wegkm_imp": [5.0, 5.0], "wegmin_imp1": [30.0, 30.0], "W_GEW": [1.0, 1.0],
    })
    table = trips_stage.run(
        persons, wege, random_seed=1,
        escort_purpose=True, escort_passive_education=True,
    )
    mask_13 = table["W_ZWECK"] == 13
    assert mask_13.any(), "the W_ZWECK-13 donor leg must survive into the output table"
    # following_purpose is the canonical contract column (trips_stage.CONTRACT),
    # not the leftover "purpose" extra column; mirrors
    # test_run_escort_purpose_flag_produces_escort_trips above.
    assert "education" in set(table.loc[mask_13, "following_purpose"])
    assert not (table.loc[mask_13, "following_purpose"] == "escort").any()


def test_entd_source_rejects_escort_passive_education():
    from braunschweig.popsim.sources.entd import EntdSource
    # escort_purpose is left at its default False so this isolates the
    # escort_passive_education guard specifically (both guards are unconditional
    # and independent; escort_purpose=True would raise on that check first, as
    # test_entd_source_rejects_escort_purpose above already covers).
    with pytest.raises(NotImplementedError, match="escort_passive_education"):
        EntdSource().build_trips(
            pd.DataFrame({"person_id": []}), pd.DataFrame(), random_seed=1,
            escort_passive_education=True,
        )


# ---------------------------------------------------------------------------
# plan-structure-fix Task 6 (issues #366 / #367): thread the rbW exclusion, the
# leading arrive-home drop and the closure dwell model through trips_stage.
# ---------------------------------------------------------------------------

def _persons_and_wege():
    """Minimal one-person / two-leg fixture whose chain already ends at home.

    Same shape as the fixtures used by the escort tests above; no W_RBW / W_SO1
    columns, so it exercises the flags-OFF path only.
    """
    persons = pd.DataFrame({"person_id": [1], "H_ID": [10], "P_ID": [1]})
    wege = pd.DataFrame({
        "H_ID": [10, 10], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [1, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 17], "W_SZM": [0, 0], "W_AZS": [8, 17], "W_AZM": [30, 20],
        "wegkm_imp": [12.0, 12.0], "wegmin_imp1": [30.0, 20.0], "W_GEW": [1.0, 1.0],
    })
    return persons, wege


def _persons_and_wege_not_ending_home():
    """One-person / two-leg fixture whose chain ends AWAY from home (leisure).

    Unlike :func:`_persons_and_wege`, this fixture forces
    ``PlanValidator.repair_trips`` to append a synthesised home-return leg, so
    a byte-identity test comparing two ``run()`` calls actually exercises the
    ``closure_dwell_model="fixed_1h"`` draw instead of vacuously agreeing on a
    plan that never needed a closure.
    """
    persons = pd.DataFrame({"person_id": [1], "H_ID": [10], "P_ID": [1]})
    wege = pd.DataFrame({
        "H_ID": [10, 10], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [1, 7], "hvm_imp": [4, 4],
        "W_SZS": [8, 17], "W_SZM": [0, 0], "W_AZS": [8, 17], "W_AZM": [30, 20],
        "wegkm_imp": [12.0, 12.0], "wegmin_imp1": [30.0, 20.0], "W_GEW": [1.0, 1.0],
    })
    return persons, wege


def _persons_and_wege_with_rbw():
    """Two donors, each with one rbW leg (W_RBW=1, coded times 701) and a chain
    that does NOT end at home (so a synthetic closure is required).

    The observable activity duration of BOTH donors (work arrival -> next
    departure) is exactly 30600 s (8.5 h), so an EMPIRICAL closure dwell is
    distinguishable from the fixed one-hour constant by value alone.
    """
    persons = pd.DataFrame({
        "person_id": [1, 2], "H_ID": [10, 20], "P_ID": [1, 1],
        "ZENSUS100m": ["c1", "c1"],
    })
    wege = pd.DataFrame({
        "H_ID": [10, 10, 10, 20, 20, 20],
        "P_ID": [1, 1, 1, 1, 1, 1],
        "W_ID": [1, 2, 3, 1, 2, 3],
        # work, rbW summary leg, leisure / work, rbW summary leg, shop
        "W_ZWECK": [1, 1, 7, 1, 1, 4],
        "W_RBW": [0, 1, 0, 0, 1, 0],
        "W_SO1": [1, 1, 1, 1, 1, 1],
        "hvm_imp": [4, 4, 4, 4, 4, 4],
        "W_SZS": [8, 701, 17, 7, 701, 16],
        "W_SZM": [0, 701, 0, 0, 701, 0],
        "W_AZS": [8, 701, 17, 7, 701, 16],
        "W_AZM": [30, 701, 20, 30, 701, 15],
        "wegkm_imp": [12.0, 30.0, 5.0, 12.0, 30.0, 5.0],
        "wegmin_imp1": [30.0, 60.0, 20.0, 30.0, 60.0, 15.0],
        "W_GEW": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    })
    return persons, wege


def test_run_excludes_rbw_and_marks_closure_with_empirical_dwell():
    persons, wege = _persons_and_wege_with_rbw()
    out = trips_stage.run(
        persons, wege, random_seed=1,
        exclude_rbw_legs=True, drop_leading_arrive_home_leg=True,
        closure_dwell_model="empirical",
    )
    # No rbW leg survives the exclusion. The brief sketched a "_rbw" trip_key
    # suffix, which does not exist (trip_key is "<person_id>_<W_ID>"), so the
    # W_RBW extra column -- which rides along into the output -- is the direct
    # evidence instead.
    assert "W_RBW" in out.columns
    assert not (out["W_RBW"] == 1).any()
    assert "is_synthetic_closure" in out.columns
    closure = out["is_synthetic_closure"].astype(bool)
    assert closure.any(), "both donor chains end away from home; a closure must be appended"
    assert (out.loc[closure, "following_purpose"] == "home").all()


def test_run_empirical_dwell_uses_observed_duration_not_the_fixed_hour(caplog):
    """Primary-path test (CLAUDE.md fallback transparency): the empirical model
    must actually supply the dwell, not silently degrade to HOME_CLOSURE_DWELL_S.

    Review finding (fix round 1, minor #2): with only one observed "work"
    activity duration per donor and ``min_obs=30``, this fixture's model never
    has enough observations to use its own (purpose, arrival-band) CELL for
    either donor -- and since the donors' terminal purposes ("leisure" /
    "shop") never appear as an OBSERVED activity at all (they are each
    donor's open-ended last activity, excluded from the duration pool by
    construction; see ``ClosureDwellModel.from_trips``), there is no
    per-purpose marginal for those purposes either. Both closure draws must
    therefore fall straight through to the GLOBAL marginal
    (``n_fallback_global``), never the purpose marginal
    (``n_fallback_purpose_marginal``). This is asserted directly on the
    model's ``report`` (captured from the ``[trips_stage] closure dwell
    model (...) report: ...`` log line trips_stage.run emits) so the test
    states explicitly which pooling level it exercises, rather than passing
    for any reason the two donors' durations happen to agree.
    """
    persons, wege = _persons_and_wege_with_rbw()
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips_stage"):
        out = trips_stage.run(
            persons, wege, random_seed=1,
            exclude_rbw_legs=True, drop_leading_arrive_home_leg=True,
            closure_dwell_model="empirical",
        ).sort_values(["person_id", "trip_index"])
    closure = out["is_synthetic_closure"].astype(bool)
    assert closure.any()
    for person_id in out.loc[closure, "person_id"].unique():
        chain = out[out["person_id"] == person_id]
        is_closure = chain["is_synthetic_closure"].astype(bool)
        last_real = chain[~is_closure].iloc[-1]
        closure_row = chain[is_closure].iloc[0]
        dwell = float(closure_row["departure_time"]) - float(last_real["arrival_time"])
        # 30600 s is the ONLY observed activity duration in this fixture; the
        # per-person jitter shifts both rows by the SAME offset, so the
        # difference only moves by the rounding of the two shifted times (<= 1 s).
        assert abs(dwell - 30600.0) <= 1.0, (
            f"person {person_id}: closure dwell {dwell}s is not the observed 30600s "
            "(3600s would mean the fixed constant was used, i.e. the empirical "
            "model never took effect)"
        )

    report_messages = [
        record.getMessage() for record in caplog.records
        if "closure dwell model" in record.getMessage()
    ]
    assert report_messages, (
        "expected the '[trips_stage] closure dwell model (...) report: ...' log "
        "line so the pooling level exercised can be checked"
    )
    report_line = report_messages[-1]
    assert "'n_fallback_global': 2" in report_line, (
        f"expected both closure draws to fall through to the global marginal "
        f"(no observed 'leisure'/'shop' duration exists in this fixture): {report_line!r}"
    )
    assert "'n_fallback_purpose_marginal': 0" in report_line, (
        f"this fixture's donors never contribute a purpose-marginal hit for their "
        f"own terminal purpose, so the purpose-marginal fallback must stay at 0: "
        f"{report_line!r}"
    )


def test_the_dwell_report_log_is_a_frozen_snapshot(caplog):
    """The report log must carry a COPY of the counters, not the live mapping (#374, R31).

    ``ClosureDwellModel.report`` is one dict that ``draw()`` mutates in place, and
    ``logging`` keeps the argument in ``LogRecord.args`` and renders it lazily, so passing
    the live mapping makes the record report whatever the counters happen to be when
    something formats it -- a queue handler, a deferred formatter, or a later assertion --
    rather than what the run logged. The identical defect was ACTIVE in the Phase B
    home-office donor pool (``tests/test_commute_day_donor_pool.py::
    test_the_dwell_report_log_is_a_frozen_snapshot``); here it is latent, because
    ``dwell_model`` is not touched again after the log statement. This test pins that the
    line stays a snapshot regardless.
    """
    persons, wege = _persons_and_wege_with_rbw()
    captured = {}
    real_builder = trips_stage.build_closure_dwell_model

    def capturing_builder(*args, **kwargs):
        captured["model"] = real_builder(*args, **kwargs)
        return captured["model"]

    trips_stage.build_closure_dwell_model = capturing_builder
    try:
        with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips_stage"):
            trips_stage.run(
                persons, wege, random_seed=1,
                exclude_rbw_legs=True, drop_leading_arrive_home_leg=True,
                closure_dwell_model="empirical",
            )
    finally:
        trips_stage.build_closure_dwell_model = real_builder

    # Select on the LOGGER and on "report:", not merely on "closure dwell model": caplog
    # collects every logger, and plan_validation's global-fallback WARNING for this fixture
    # also contains the words "closure dwell model" -- with float args, so a test that
    # matched it would pass no matter what this line passes.
    record = next(record for record in caplog.records
                  if record.name == "braunschweig.popsim.trips_stage"
                  and "closure dwell model" in record.getMessage()
                  and "report:" in record.getMessage())
    before = record.getMessage()
    captured["model"].report["n_draws"] = 987654
    assert record.getMessage() == before
    assert "987654" not in record.getMessage()


def test_run_rejects_unknown_dwell_model():
    persons, wege = _persons_and_wege_with_rbw()
    with pytest.raises(ValueError, match="closure_dwell_model"):
        trips_stage.run(persons, wege, random_seed=1, closure_dwell_model="median")


def test_run_default_flags_off_is_byte_identical_to_previous_signature():
    """Pin the OFF path (no new keywords) against the PRE-#366/#367 behaviour.

    Review finding (fix round 1): the previous version of this test used
    :func:`_persons_and_wege`, whose chain already ends at home, so no
    closure was ever appended and ``closure_dwell_model="fixed_1h"`` was
    never actually drawn -- the two calls agreed vacuously. This fixture's
    chain ends away from home (leisure), forcing a synthesised return-home
    leg in both calls, and the closure row's departure is additionally
    checked against the OLD hard-coded constant (``arrival + HOME_CLOSURE_DWELL_S``,
    imported from ``plan_validation``, unchanged since before this feature),
    so the OFF path is pinned against the pre-change behaviour, not just
    against itself.
    """
    from braunschweig.popsim.plan_validation import HOME_CLOSURE_DWELL_S

    persons, wege = _persons_and_wege_not_ending_home()
    a = trips_stage.run(persons, wege, random_seed=1)
    b = trips_stage.run(
        persons, wege, random_seed=1,
        exclude_rbw_legs=False, drop_leading_arrive_home_leg=False,
        closure_dwell_model="fixed_1h",
    )
    assert "is_synthetic_closure" in a.columns and "is_synthetic_closure" in b.columns
    pd.testing.assert_frame_equal(a, b)

    closure = a["is_synthetic_closure"].astype(bool)
    assert closure.any(), "fixture must require a synthesised home-return closure"
    last_real = a.loc[~closure].iloc[-1]
    closure_row = a.loc[closure].iloc[0]
    dwell = float(closure_row["departure_time"]) - float(last_real["arrival_time"])
    assert abs(dwell - HOME_CLOSURE_DWELL_S) <= 1.0, (
        f"closure dwell {dwell}s must equal the pre-change constant "
        f"HOME_CLOSURE_DWELL_S={HOME_CLOSURE_DWELL_S}s (within 1s rounding); "
        "closure_dwell_model='fixed_1h' must reproduce the old hard-coded dwell."
    )


class _RecordingConfigureContext:
    """Minimal synpp ConfigurationContext stand-in that records config() lookups.

    Mirrors the CONFIGURE phase: every ``context.config(key, default)`` call is
    recorded with the default the stage declared, so the test can assert what the
    stage registers without depending on config VALUES (there are none during
    configure). ``stage()`` records the declared dependencies because
    trips_stage.configure() declares them. Same shape as the stand-in in
    tests/test_completed_donor_stage.py.
    """

    def __init__(self, values=None):
        self.calls = {}
        self.stages = []
        self._values = values or {}

    def config(self, key, default=None):
        self.calls[key] = default
        return self._values.get(key, default)

    def stage(self, name, alias=None, **kwargs):
        self.stages.append((name, alias))


def test_trips_stage_configure_registers_the_plan_structure_keys():
    from braunschweig.popsim.stage import (
        KEY_CLOSURE_DWELL_MODEL, KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_EXCLUDE_RBW_LEGS,
    )
    ctx = _RecordingConfigureContext()
    trips_stage.configure(ctx)
    assert ctx.calls[KEY_EXCLUDE_RBW_LEGS] is True
    assert ctx.calls[KEY_DROP_LEADING_ARRIVE_HOME_LEG] is True
    assert ctx.calls[KEY_CLOSURE_DWELL_MODEL] == "empirical"


# ---------------------------------------------------------------------------
# Ruling C-R22 (issue #373 cleanup wave, item 6): map_purpose already raises
# "escort_passive_from_adult requires escort_purpose" -- but only at TRIP-BUILD
# time, i.e. after synpp has already run every upstream stage including the full
# PopulationSim balancing. configure() must raise the SAME contradiction before
# any stage executes, so a misconfigured run fails the DAG immediately.
# ---------------------------------------------------------------------------

def test_trips_stage_configure_raises_when_escort_passive_from_adult_without_escort_purpose():
    from braunschweig.popsim.stage import KEY_ESCORT_PASSIVE_FROM_ADULT
    ctx = _RecordingConfigureContext(
        values={"escort_purpose": False, KEY_ESCORT_PASSIVE_FROM_ADULT: True})
    with pytest.raises(ValueError, match="escort_purpose"):
        trips_stage.configure(ctx)


def test_trips_stage_configure_names_both_keys_in_the_error():
    from braunschweig.popsim.stage import KEY_ESCORT_PASSIVE_FROM_ADULT
    ctx = _RecordingConfigureContext(
        values={"escort_purpose": False, KEY_ESCORT_PASSIVE_FROM_ADULT: True})
    with pytest.raises(ValueError) as error:
        trips_stage.configure(ctx)
    assert "escort_purpose" in str(error.value)
    assert KEY_ESCORT_PASSIVE_FROM_ADULT in str(error.value)


def test_trips_stage_configure_allows_escort_passive_from_adult_with_escort_purpose():
    from braunschweig.popsim.stage import KEY_ESCORT_PASSIVE_FROM_ADULT
    ctx = _RecordingConfigureContext(
        values={"escort_purpose": True, KEY_ESCORT_PASSIVE_FROM_ADULT: True})
    trips_stage.configure(ctx)  # must not raise


def test_trips_stage_configure_default_flags_do_not_raise():
    """Both flags default False, so a config that sets neither must configure cleanly."""
    ctx = _RecordingConfigureContext()
    trips_stage.configure(ctx)  # must not raise


def test_entd_source_rejects_exclude_rbw_legs():
    from braunschweig.popsim.sources.entd import EntdSource
    with pytest.raises(NotImplementedError, match="exclude_rbw_legs"):
        EntdSource().build_trips(
            pd.DataFrame({"person_id": []}), pd.DataFrame(), random_seed=1,
            exclude_rbw_legs=True,
        )


def test_entd_source_rejects_drop_leading_arrive_home_leg():
    from braunschweig.popsim.sources.entd import EntdSource
    with pytest.raises(NotImplementedError, match="drop_leading_arrive_home_leg"):
        EntdSource().build_trips(
            pd.DataFrame({"person_id": []}), pd.DataFrame(), random_seed=1,
            drop_leading_arrive_home_leg=True,
        )


def test_entd_source_rejects_empirical_closure_dwell_model():
    from braunschweig.popsim.sources.entd import EntdSource
    with pytest.raises(NotImplementedError, match="closure_dwell_model"):
        EntdSource().build_trips(
            pd.DataFrame({"person_id": []}), pd.DataFrame(), random_seed=1,
            closure_dwell_model="empirical",
        )


# ---------------------------------------------------------------------------
# closure_dwell_min_obs is a config key, not a hard-coded 30 (final-review
# minor M1): the value must reach ClosureDwellModel.from_trips, since it decides
# whether a (purpose x arrival band) cell is used directly or falls back to the
# purpose marginal -- the rate the fallback instrumentation reports.
# ---------------------------------------------------------------------------

def test_closure_dwell_min_obs_reaches_the_empirical_model():
    persons, wege = _persons_and_wege_with_rbw()
    lenient = trips_stage.build_closure_dwell_model(
        persons, wege, closure_dwell_model="empirical", random_seed=1,
        closure_dwell_min_obs=1, exclude_rbw_legs=True,
    )
    strict = trips_stage.build_closure_dwell_model(
        persons, wege, closure_dwell_model="empirical", random_seed=1,
        closure_dwell_min_obs=1000, exclude_rbw_legs=True,
    )
    # Same donor diaries, same draw: with min_obs=1 the (purpose, band) CELL is used;
    # with min_obs=1000 no cell qualifies and every draw falls back to the marginal.
    # 8:30 is the arrival of the work activity whose duration the donors observe
    # (their next departure is at 17:00), so the (work, <14h) cell is populated.
    lenient.draw("work", 8 * 3600.0 + 1800.0)
    strict.draw("work", 8 * 3600.0 + 1800.0)
    assert lenient.report["n_fallback_purpose_marginal"] == 0
    assert strict.report["n_fallback_purpose_marginal"] == 1


def test_closure_dwell_min_obs_must_be_positive():
    persons, wege = _persons_and_wege_with_rbw()
    with pytest.raises(ValueError, match="closure_dwell_min_obs"):
        trips_stage.build_closure_dwell_model(
            persons, wege, closure_dwell_model="empirical", random_seed=1,
            closure_dwell_min_obs=0,
        )


def test_run_threads_closure_dwell_min_obs(monkeypatch):
    seen = {}
    original = trips_stage.build_closure_dwell_model

    def spy(*args, **kwargs):
        seen["min_obs"] = kwargs.get("closure_dwell_min_obs")
        return original(*args, **kwargs)

    monkeypatch.setattr(trips_stage, "build_closure_dwell_model", spy)
    persons, wege = _persons_and_wege_with_rbw()
    trips_stage.run(persons, wege, random_seed=1, closure_dwell_model="empirical",
                    closure_dwell_min_obs=7, exclude_rbw_legs=True)
    assert seen["min_obs"] == 7


def test_run_forwards_w_zweck_10_as_leisure_to_both_builders(monkeypatch):
    """w_zweck_10_as_leisure must reach BOTH internal builders run() calls: the
    empirical closure-dwell donor table (build_closure_dwell_model) and the main
    validated trip table (popsim_trips.build_validated_trip_table) -- issue #373
    fix round 1, Important finding 3b. Forwarding it to only one would let the
    dwell pools disagree with the purpose vocabulary of the table they feed (see
    build_closure_dwell_model's own docstring)."""
    from braunschweig.popsim import trips as popsim_trips

    seen = {}
    original_dwell = trips_stage.build_closure_dwell_model

    def dwell_spy(*args, **kwargs):
        seen["dwell_model"] = kwargs.get("w_zweck_10_as_leisure")
        return original_dwell(*args, **kwargs)

    original_build = popsim_trips.build_validated_trip_table

    def build_spy(*args, **kwargs):
        seen["build_validated_trip_table"] = kwargs.get("w_zweck_10_as_leisure")
        return original_build(*args, **kwargs)

    monkeypatch.setattr(trips_stage, "build_closure_dwell_model", dwell_spy)
    monkeypatch.setattr(popsim_trips, "build_validated_trip_table", build_spy)
    persons, wege = _persons_and_wege_with_rbw()
    trips_stage.run(persons, wege, random_seed=1, w_zweck_10_as_leisure=True)
    assert seen["dwell_model"] is True
    assert seen["build_validated_trip_table"] is True


def test_run_forwards_the_passive_escort_pairing_keywords_to_both_builders(monkeypatch):
    """Same requirement as w_zweck_10_as_leisure above for the two passive-escort keywords
    (issue #372 task 4): the empirical dwell pools are stratified by following_purpose, so a
    donor table built WITHOUT the pairing would send every relabelled passive leg's draw into
    the education pool while the main table puts it in shop/home/leisure."""
    from braunschweig.popsim import trips as popsim_trips

    seen = {}
    original_dwell = trips_stage.build_closure_dwell_model

    def dwell_spy(*args, **kwargs):
        seen["dwell_flag"] = kwargs.get("escort_passive_from_adult")
        seen["dwell_gap"] = kwargs.get("passive_pair_max_gap_minutes")
        return original_dwell(*args, **kwargs)

    original_build = popsim_trips.build_validated_trip_table

    def build_spy(*args, **kwargs):
        seen["build_flag"] = kwargs.get("escort_passive_from_adult")
        seen["build_gap"] = kwargs.get("passive_pair_max_gap_minutes")
        return original_build(*args, **kwargs)

    monkeypatch.setattr(trips_stage, "build_closure_dwell_model", dwell_spy)
    monkeypatch.setattr(popsim_trips, "build_validated_trip_table", build_spy)
    persons, wege = _persons_and_wege_with_rbw()
    wege = wege.assign(HP_ALTER=40)
    trips_stage.run(persons, wege, random_seed=1, escort_purpose=True,
                    escort_passive_education=True, escort_passive_from_adult=True,
                    passive_pair_max_gap_minutes=20.0)
    assert seen["dwell_flag"] is True and seen["build_flag"] is True
    assert seen["dwell_gap"] == 20.0 and seen["build_gap"] == 20.0


# ---------------------------------------------------------------------------
# Departure-time model (issue #123 Task 4, ADR-0114): the trip build applies one of the three
# start-time models of braunschweig.popsim.departure_time_model instead of calling the eqasim
# per-person jitter directly. The CODE default stays "eqasim_uniform", i.e. byte-identical.
# ---------------------------------------------------------------------------

SRV_REFERENCE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "eqasim-data", "data", "braunschweig", "srv")


def _persons_and_wege_with_person_attributes():
    """Six employed persons, each with a home -> work -> home chain on a quarter-hour clock time.

    Unlike the fixtures above, the PERSONS frame carries the MiD person attributes
    ``HP_ALTER`` / ``P_TAET`` that
    :func:`braunschweig.popsim.departure_time_model.persons_from_mid_schema` needs to pick a
    mapping cell (ruling A-R7): all six are employed adults, so their harmonised group is
    ``employed`` and -- their first leg being a work leg -- their reference cell is
    ``(employed, work)``, which the committed SrV table fills with 4,649 unweighted observations.
    """
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5, 6],
        "H_ID": [10, 20, 30, 40, 50, 60],
        "P_ID": [1, 1, 1, 1, 1, 1],
        "HP_ALTER": [40, 41, 42, 43, 44, 45],
        "P_TAET": [1, 1, 1, 1, 1, 1],
        "ZENSUS100m": ["c1"] * 6,
    })
    rows = []
    for index, household_id in enumerate([10, 20, 30, 40, 50, 60]):
        hour, minute = 6 + index % 3, (index % 4) * 15
        rows += [
            {"H_ID": household_id, "P_ID": 1, "W_ID": 1, "W_ZWECK": 1, "hvm_imp": 4,
             "W_SZS": hour, "W_SZM": minute, "W_AZS": hour, "W_AZM": minute + 10,
             "wegkm_imp": 12.0, "wegmin_imp1": 10.0, "W_GEW": 1.0},
            {"H_ID": household_id, "P_ID": 1, "W_ID": 2, "W_ZWECK": 8, "hvm_imp": 4,
             "W_SZS": 17, "W_SZM": 0, "W_AZS": 17, "W_AZM": 20,
             "wegkm_imp": 12.0, "wegmin_imp1": 20.0, "W_GEW": 1.0},
        ]
    return persons, pd.DataFrame(rows)


def test_run_default_is_byte_identical_on_contract_columns():
    """Golden-master regression for the OFF path (issue #123 Task 4).

    ``trips_stage.run`` no longer calls ``apply_per_person_jitter`` directly -- it dispatches
    through ``departure_time_model.apply_departure_time_model``, whose ``eqasim_uniform`` branch
    delegates back to that very function. This test pins the CONTRACT columns of a default
    ``run()`` call against values produced by the code as it existed BEFORE that dispatch was
    introduced, so a change in the RNG consumption, in the rounding, or in the order of the
    shift relative to the rest of ``run()`` fails loudly here instead of silently moving every
    departure time in the pipeline.

    Provenance of the pinned values: ``braunschweig/popsim/trips_stage.py`` at commit
    ``eff5720c`` (this branch's Task 3 tip, immediately before Task 4 rewired the call) was run
    ONCE on the fixture and seed below with a scratch script; the printed ``departure_time`` /
    ``arrival_time`` lists were copied verbatim. The scratch script is not part of the
    repository -- only the pinned literals are committed. Only CONTRACT columns are compared:
    the extras a run carries beyond the contract (raw MiD columns, ``trip_key``, the recorded
    offset) are not part of the downstream contract this pin protects.
    """
    persons, wege = _persons_and_wege_with_person_attributes()
    out = trips_stage.run(persons, wege, random_seed=20260910)

    assert list(out.columns)[:len(trips_stage.CONTRACT)] == trips_stage.CONTRACT
    assert out["person_id"].tolist() == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
    assert out["trip_index"].tolist() == [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1]
    # Pinned from trips_stage.run at eff5720c (pre-Task-4) -- see the docstring above.
    assert out["departure_time"].tolist() == [
        20137.0, 59737.0, 27589.0, 62689.0, 30667.0, 61267.0,
        23800.0, 60700.0, 24770.0, 60770.0, 30732.0, 62232.0]
    assert out["arrival_time"].tolist() == [
        20737.0, 60937.0, 28189.0, 63889.0, 31267.0, 62467.0,
        24400.0, 61900.0, 25370.0, 61970.0, 31332.0, 63432.0]
    # The explicit default must reproduce the implicit one, contract columns included.
    explicit = trips_stage.run(persons, wege, random_seed=20260910,
                               departure_time_model="eqasim_uniform")
    pd.testing.assert_frame_equal(out[trips_stage.CONTRACT], explicit[trips_stage.CONTRACT])


def test_run_with_srv_mapped_uses_the_model():
    """``departure_time_model="srv_mapped"`` maps the fixture's first departures onto the
    COMMITTED SrV reference cell of their (purpose, group), and shifts the whole chain with it.

    ``departure_time_min_model_n=1`` is passed because the six-person fixture is far below the
    production ``min_model_n`` default of 50; without it every person would legitimately stay
    unmapped and the test would assert nothing about the mapping. The reference side keeps its
    production threshold (the ``(employed, work)`` cell carries 4,649 observations).
    """
    from braunschweig.popsim.departure_time_model import (BIN_MINUTES, OFFSET_COLUMN,
                                                          load_departure_time_reference)

    reference = load_departure_time_reference(SRV_REFERENCE_DIR)
    persons, wege = _persons_and_wege_with_person_attributes()
    out = trips_stage.run(persons, wege, random_seed=20260910,
                          departure_time_model="srv_mapped",
                          departure_time_reference=reference,
                          departure_time_min_model_n=1)

    assert OFFSET_COLUMN in out.columns
    first = out[out["trip_index"] == 0]
    assert (first["following_purpose"] == "work").all()
    # Every first departure must land in a 15-minute bin the (employed, work) reference cell
    # actually carries mass in -- i.e. inside the SrV support, not merely somewhere plausible.
    cell = reference[(reference["segment"] == "employed") & (reference["purpose"] == "work")]
    support = set(cell.loc[cell["share_derounded"] > 0.0, "bin_15min"].astype(int))
    bins = (first["departure_time"] // (BIN_MINUTES * 60)).astype(int)
    assert set(bins) <= support, f"mapped bins {sorted(set(bins))} outside the SrV support"
    # The chain moves rigidly: both trips of a person carry the SAME recorded offset, and the
    # trip duration is untouched (the model's hold-out dimension).
    assert (out.groupby("person_id")[OFFSET_COLUMN].nunique() == 1).all()
    assert np.allclose(out["arrival_time"] - out["departure_time"], out["trip_duration"])
    # The mapping actually happened: the offsets are not merely the de-rounding draw, whose
    # half-width on a quarter-hour report is 450 s.
    assert (out[OFFSET_COLUMN].abs() > 450.0).any()


def test_run_rejects_an_unknown_departure_time_model():
    persons, wege = _persons_and_wege_with_person_attributes()
    with pytest.raises(ValueError, match="unknown model"):
        trips_stage.run(persons, wege, random_seed=1, departure_time_model="quantile")


def test_trips_stage_configure_registers_the_departure_time_keys():
    from braunschweig.popsim.stage import (
        DEFAULT_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS, DEFAULT_DEPARTURE_TIME_MIN_MODEL_N,
        DEFAULT_DEPARTURE_TIME_MIN_REFERENCE_N, DEFAULT_DEPARTURE_TIME_MODEL,
        KEY_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS, KEY_DEPARTURE_TIME_MIN_MODEL_N,
        KEY_DEPARTURE_TIME_MIN_REFERENCE_N, KEY_DEPARTURE_TIME_MODEL,
    )
    ctx = _RecordingConfigureContext()
    trips_stage.configure(ctx)
    assert ctx.calls[KEY_DEPARTURE_TIME_MODEL] == DEFAULT_DEPARTURE_TIME_MODEL == "eqasim_uniform"
    assert ctx.calls[KEY_DEPARTURE_TIME_MIN_REFERENCE_N] == DEFAULT_DEPARTURE_TIME_MIN_REFERENCE_N
    assert ctx.calls[KEY_DEPARTURE_TIME_MIN_MODEL_N] == DEFAULT_DEPARTURE_TIME_MIN_MODEL_N
    assert (ctx.calls[KEY_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS]
            == DEFAULT_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS)
    # The reference lives under data_path; the stage must declare that key to read it.
    assert "data_path" in ctx.calls


def test_departure_time_model_default_agrees_with_the_model_module():
    """``config_keys`` is a leaf by contract, so it repeats the OFF model name as a literal --
    pin it equal to the model module's own constant (same treatment as the passive-escort gap
    default), or the two homes can silently disagree on what "off" means."""
    from braunschweig.popsim.departure_time_model import MODEL_EQASIM_UNIFORM
    from braunschweig.popsim.stage.config_keys import DEFAULT_DEPARTURE_TIME_MODEL

    assert DEFAULT_DEPARTURE_TIME_MODEL == MODEL_EQASIM_UNIFORM


def test_execute_raises_naming_the_key_when_the_srv_reference_is_missing(tmp_path):
    """No silent fallback to ``eqasim_uniform``: a configured ``srv_mapped`` run whose committed
    reference is not under ``data_path`` must ABORT with a message naming both the expected path
    and the config key that requires it (CLAUDE.md fallback transparency)."""
    from braunschweig.popsim.stage.config_keys import KEY_DEPARTURE_TIME_MODEL

    ctx = _RecordingConfigureContext()
    trips_stage.configure(ctx)
    values = dict(ctx.calls)
    values.update({"random_seed": 1, "data_path": str(tmp_path),
                   KEY_DEPARTURE_TIME_MODEL: "srv_mapped",
                   "braunschweig.population.popsim.mid_dir": str(tmp_path)})

    class _ExecuteContext:
        def config(self, key):
            return values[key]

        def stage(self, name):
            raise AssertionError(f"stage {name!r} must not be read before the reference check")

    with pytest.raises(FileNotFoundError) as excinfo:
        trips_stage.execute(_ExecuteContext())
    message = str(excinfo.value)
    assert KEY_DEPARTURE_TIME_MODEL in message
    assert str(tmp_path) in message


def test_entd_source_rejects_the_departure_time_model():
    """The ENTD trip build has its own jitter path and never reaches ``trips_stage.run``, so a
    non-default model must RAISE naming the key rather than sit silently unapplied."""
    from braunschweig.popsim.sources.entd import EntdSource
    with pytest.raises(ValueError, match="departure_time_model"):
        EntdSource().build_trips(
            pd.DataFrame({"person_id": []}), pd.DataFrame(), random_seed=1,
            departure_time_model="srv_mapped",
        )
