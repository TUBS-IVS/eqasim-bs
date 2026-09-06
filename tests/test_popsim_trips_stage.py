"""Tests for the popsim_mid trips_stage (Phase 5g.6 / Task 6).

Verifies that trips_stage.run() returns the canonical synthesis.population.trips
11-column contract plus euclidean_distance, and that the per-person departure-time
jitter preserves within-person trip ordering.
"""

from __future__ import annotations

import logging

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
