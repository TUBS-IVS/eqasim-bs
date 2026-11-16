"""Byte-identity and speed of the vectorised ``plan_validation._append_return_home``.

Audit package H, finding P1-F2: the closure loop visited every person through one
``df.loc`` row Series and scanned the whole frame for each closure's ``trip_id``.
The rewrite only visits the persons that need a closure. It must stay byte-identical,
including the sequence of ``ClosureDwellModel.draw`` calls, because each empirical
draw consumes the RNG that later stages continue to use. The one intended difference:
an integer person_id narrower than int64 keeps its dtype instead of being widened
(``_align_reference_person_id``); int64 ids, as in production on Linux, are unaffected.

``_reference_append_return_home`` below is a verbatim copy of the implementation
before the rewrite (commit a46968a4), kept only as the test oracle.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import plan_validation as pv
from braunschweig.popsim.closure_dwell import ClosureDwellModel
from braunschweig.popsim.fallback_rates import log_fallback_rate

_reference_logger = logging.getLogger(pv.__name__)


def _reference_append_return_home(
    df_trips: pd.DataFrame, dwell_s: float, *, dwell_model=None,
    max_plan_time_seconds: float = pv.MAX_PLAN_TIME_SECONDS,
) -> pd.DataFrame:
    """Verbatim copy of ``_append_return_home`` before the P1-F2 rewrite (test oracle)."""
    df = df_trips.sort_values(["person_id", "departure_time"], ignore_index=True)
    if "is_synthetic_closure" not in df.columns:
        df["is_synthetic_closure"] = False
    else:
        df["is_synthetic_closure"] = df["is_synthetic_closure"].fillna(False).astype(bool)

    last_rows = df.drop_duplicates("person_id", keep="last")
    last_idx = pd.Series(last_rows.index, index=last_rows["person_id"].to_numpy())

    rows_to_append = []
    idx_to_flip = []
    n_capped = 0
    n_cap_impossible = 0
    n_mode_filled = 0
    cap_applies = dwell_model is not None and dwell_model.report.get("kind") == "empirical"

    for person_id, idx in last_idx.items():
        last = df.loc[idx]
        if last["following_purpose"] == "home":
            continue

        if "trip_duration" in df.columns and not pd.isna(last.get("trip_duration", np.nan)):
            travel_time = float(last["trip_duration"])
        else:
            travel_time = max(0.0, float(last["arrival_time"]) - float(last["departure_time"]))

        if dwell_model is not None:
            dwell = dwell_model.draw(last["following_purpose"], float(last["arrival_time"]))
        else:
            dwell = dwell_s

        dep = float(last["arrival_time"]) + dwell
        arr = dep + travel_time

        if cap_applies and arr > max_plan_time_seconds:
            capped_dwell = max_plan_time_seconds - travel_time - float(last["arrival_time"])
            if capped_dwell >= pv.HOME_CLOSURE_DWELL_S:
                dwell = capped_dwell
                dep = float(last["arrival_time"]) + dwell
                arr = dep + travel_time
                n_capped += 1
                dwell_model.report["n_capped"] = dwell_model.report.get("n_capped", 0) + 1
            else:
                n_cap_impossible += 1

        if "mode" in df.columns and not pd.isna(last.get("mode")):
            mode = last["mode"]
        else:
            mode = "car"
            n_mode_filled += 1

        new_row = {col: np.nan for col in df.columns}
        new_row.update({
            "person_id": person_id,
            "departure_time": dep,
            "arrival_time": arr,
            "preceding_purpose": last["following_purpose"],
            "following_purpose": "home",
            "mode": mode,
            "is_first_trip": False,
            "is_last_trip": True,
            "is_synthetic_closure": True,
        })
        if "trip_key" in df.columns:
            new_row["trip_key"] = f"{person_id}_closure"
        if "trip_id" in df.columns:
            max_trip_id = df[df["person_id"] == person_id]["trip_id"].max()
            new_row["trip_id"] = max_trip_id + 1

        rows_to_append.append(new_row)
        idx_to_flip.append(idx)

    if not rows_to_append:
        return df

    log_fallback_rate(
        _reference_logger, "plan_validation.return_home_mode", n_mode_filled, len(rows_to_append),
        what='appended return-home legs whose mode was missing and was filled with "car"',
    )

    if n_capped or n_cap_impossible:
        n_draws = len(rows_to_append)
        cap_rate = n_capped / max(n_draws, 1)
        log = _reference_logger.warning if cap_rate > pv.CLOSURE_CAP_WARN_RATE else _reference_logger.info
        log(
            "[popsim.plan_validation] home-closure dwell capped at the %.0fh plan-time "
            "bound for %d/%d (%.2f%%) appended return-home trips (empirical dwell model "
            "only; the dwell was reduced, never the outbound travel time, and never below "
            "the %.1fh floor); %d/%d (%.2f%%) could not be capped without breaching that "
            "floor and are left to the plan-time bound validation",
            max_plan_time_seconds / 3600.0, n_capped, n_draws, 100.0 * cap_rate,
            pv.HOME_CLOSURE_DWELL_S / 3600.0,
            n_cap_impossible, n_draws, 100.0 * n_cap_impossible / max(n_draws, 1),
        )

    df.loc[idx_to_flip, "is_last_trip"] = False

    new_df = pd.DataFrame(rows_to_append)
    df = pd.concat([df, new_df], ignore_index=True)
    return df.sort_values(["person_id", "departure_time"], ignore_index=True)


_PURPOSES = np.array(["home", "work", "education", "shop", "leisure", "errand", "other"], dtype=object)
_MODES = np.array(["car", "car_passenger", "pt", "bike", "walk"], dtype=object)


def _random_trips(seed: int, n_persons: int, *, late: bool = False, variant: int = 0) -> pd.DataFrame:
    """Random diary table covering the input shapes the repair meets in production.

    ``variant`` toggles the optional columns (``trip_id`` int or float with NaN,
    ``trip_key``, ``trip_duration`` with NaN, ``mode`` with NaN or absent, an existing
    ``is_synthetic_closure`` with NaN) and string vs integer person ids. Departure
    ties (MiD 0-minute legs) are included. ``late`` shifts days towards the plan-time
    bound so the empirical cap and its floor both fire.
    """
    rng = np.random.RandomState(seed)
    n_trips = rng.randint(1, 7, size=n_persons)
    person_numbers = np.repeat(np.arange(n_persons), n_trips)
    # Shuffle the person order so the function's own sort is exercised.
    order = rng.permutation(len(person_numbers))
    person_numbers = person_numbers[order]
    start = rng.uniform(5 * 3600, 22 * 3600 if late else 12 * 3600, size=len(person_numbers))
    step = rng.choice([0.0, 600.0, 1800.0, 7200.0], size=len(person_numbers))
    departure = np.round(start + step, 0)
    travel = rng.choice([0.0, 300.0, 1200.0, 3600.0], size=len(person_numbers))
    frame = pd.DataFrame({
        "person_id": person_numbers if variant not in (1, 3) else np.array([f"p{n:05d}" for n in person_numbers], dtype=object),
        "departure_time": departure,
        "arrival_time": departure + travel,
        "preceding_purpose": rng.choice(_PURPOSES, size=len(person_numbers)),
        "following_purpose": rng.choice(_PURPOSES, size=len(person_numbers)),
        "is_first_trip": False,
        "is_last_trip": False,
        "household_id": person_numbers // 3,
        "weight": rng.uniform(0.5, 2.0, size=len(person_numbers)),
    })
    if variant in (0, 1, 2):
        frame["mode"] = rng.choice(_MODES, size=len(frame))
        frame.loc[rng.rand(len(frame)) < 0.1, "mode"] = np.nan
    if variant in (0, 3):
        frame["trip_id"] = frame.groupby("person_id").cumcount()
    if variant == 1:
        frame["trip_id"] = frame.groupby("person_id").cumcount().astype(float)
        frame.loc[rng.rand(len(frame)) < 0.2, "trip_id"] = np.nan
    if variant in (0, 2):
        frame["trip_key"] = frame["person_id"].astype(str) + "_" + frame.groupby("person_id").cumcount().astype(str)
    if variant in (0, 1, 3):
        frame["trip_duration"] = travel
        frame.loc[rng.rand(len(frame)) < 0.3, "trip_duration"] = np.nan
    if variant == 2:
        flag = pd.Series(rng.rand(len(frame)) < 0.05, dtype=object)
        flag[rng.rand(len(frame)) < 0.05] = np.nan
        frame["is_synthetic_closure"] = flag.to_numpy()
    if variant == 4:
        # int32 ids and none of the optional columns: the rewrite keeps int32, where
        # the former implementation widened it to int64 (see _align_reference_person_id).
        frame["person_id"] = frame["person_id"].astype(np.int32)
    if variant == 5:
        # Float ids with some missing ones (sorted last, grouped as one key).
        person = frame["person_id"].astype(float)
        person[person % 17 == 0] = np.nan
        frame["person_id"] = person
        frame["trip_id"] = np.arange(len(frame))
    return frame


def _donor_trips(seed: int) -> pd.DataFrame:
    """Donor diaries for the empirical dwell model, with several sparse cells (fallbacks fire)."""
    rng = np.random.RandomState(seed)
    rows = []
    for person in range(400):
        t = rng.uniform(6 * 3600, 9 * 3600)
        for _ in range(rng.randint(2, 5)):
            arrival = t + 900
            rows.append((person, t, arrival, rng.choice(_PURPOSES[:5])))
            t = arrival + rng.uniform(600, 14 * 3600)
    return pd.DataFrame(rows, columns=["person_id", "departure_time", "arrival_time", "following_purpose"])


def _models(kind: str, seed: int):
    """Two identical, independently seeded models (one per implementation), or ``None``."""
    if kind == "none":
        return None, None
    if kind == "fixed":
        return ClosureDwellModel.fixed(pv.HOME_CLOSURE_DWELL_S), ClosureDwellModel.fixed(pv.HOME_CLOSURE_DWELL_S)
    donors = _donor_trips(seed)
    return (
        ClosureDwellModel.from_trips(donors, rng=np.random.RandomState(seed)),
        ClosureDwellModel.from_trips(donors, rng=np.random.RandomState(seed)),
    )


def _align_reference_person_id(expected: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
    """Apply the one intended difference to the oracle's output.

    The former implementation widened an integer person_id narrower than int64 (int32
    ids from np.arange on Windows) to int64 as a side effect of a pandas Index; the
    rewrite keeps the input dtype. Values are unchanged. Every other column must match
    the oracle exactly.
    """
    input_dtype = trips["person_id"].dtype
    if pd.api.types.is_integer_dtype(input_dtype) and expected["person_id"].dtype != input_dtype:
        expected = expected.copy()
        expected["person_id"] = expected["person_id"].astype(input_dtype)
    return expected


def _run(function, trips, model, max_plan_time_seconds, caplog):
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=pv.__name__):
        out = function(trips, pv.HOME_CLOSURE_DWELL_S, dwell_model=model, max_plan_time_seconds=max_plan_time_seconds)
    messages = [(record.levelno, record.getMessage()) for record in caplog.records]
    return out, messages


@pytest.mark.parametrize("variant", [0, 1, 2, 3, 4, 5])
@pytest.mark.parametrize("kind,late,bound", [
    ("empirical", False, pv.MAX_PLAN_TIME_SECONDS),  # uncapped empirical draws
    ("empirical", True, 26 * 3600),  # capped and cap-impossible draws
    ("fixed", True, 26 * 3600),  # fixed model: never capped, even beyond the bound
    ("none", True, 26 * 3600),  # constant dwell, no model
])
def test_append_return_home_is_byte_identical_to_the_reference(variant, kind, late, bound, caplog):
    seed = 1000 + variant * 10 + (1 if late else 0)
    trips = _random_trips(seed, 300, late=late, variant=variant)
    trips_before = trips.copy(deep=True)
    reference_model, new_model = _models(kind, seed)

    expected, expected_logs = _run(_reference_append_return_home, trips, reference_model, bound, caplog)
    actual, actual_logs = _run(pv._append_return_home, trips, new_model, bound, caplog)

    # The person_id dtype of the input survives (the oracle widened int32 to int64).
    assert actual["person_id"].dtype == trips["person_id"].dtype
    expected = _align_reference_person_id(expected, trips)
    # Frame: values, dtypes, column order and index exactly equal.
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert list(actual.dtypes) == list(expected.dtypes)
    assert [type(v) for v in actual.iloc[-1]] == [type(v) for v in expected.iloc[-1]]
    # The caller's table is never modified.
    pd.testing.assert_frame_equal(trips, trips_before, check_exact=True)
    # Logs (fallback-rate line and cap line) are identical.
    assert actual_logs == expected_logs
    if kind == "empirical":
        # Same RNG draws in the same order leave the same RNG state; counters agree.
        reference_state = reference_model._rng.get_state()
        new_state = new_model._rng.get_state()
        assert reference_state[0] == new_state[0]
        np.testing.assert_array_equal(reference_state[1], new_state[1])
        assert reference_state[2:] == new_state[2:]
        assert new_model.report == reference_model.report
        assert new_model.report["n_draws"] > 0
        if late:
            # The capped case really caps, and the floor rule really fires.
            assert new_model.report["n_capped"] > 0
            assert any("could not be capped" in message for _, message in actual_logs)
    elif kind == "fixed":
        assert new_model.report == reference_model.report
        assert new_model.report["n_capped"] == 0
    # The synthetic rows were actually appended (the test covers the closure path).
    assert actual["is_synthetic_closure"].sum() > 0


def test_append_return_home_without_closures_is_identical(caplog):
    trips = _random_trips(7, 50)
    trips["following_purpose"] = "home"
    expected, expected_logs = _run(_reference_append_return_home, trips, None, pv.MAX_PLAN_TIME_SECONDS, caplog)
    actual, actual_logs = _run(pv._append_return_home, trips, None, pv.MAX_PLAN_TIME_SECONDS, caplog)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    assert actual_logs == expected_logs == []


def test_append_return_home_is_faster_than_the_reference(caplog):
    """The rewrite removes the per-person row access and the per-closure frame scan.

    The reference's per-closure scan grows quadratically, so 2,000 persons already
    separate the two clearly while the reference stays at a few seconds. The 3x bound
    is deliberately loose so a loaded CI runner does not flake; the observed ratio is
    printed (run with ``-s``).
    """
    trips = _random_trips(42, 2000, variant=0)
    reference_model, new_model = _models("empirical", 42)

    start = time.perf_counter()
    expected, _ = _run(_reference_append_return_home, trips, reference_model, pv.MAX_PLAN_TIME_SECONDS, caplog)
    reference_seconds = time.perf_counter() - start
    start = time.perf_counter()
    actual, _ = _run(pv._append_return_home, trips, new_model, pv.MAX_PLAN_TIME_SECONDS, caplog)
    new_seconds = time.perf_counter() - start

    pd.testing.assert_frame_equal(actual, _align_reference_person_id(expected, trips), check_exact=True)
    print(f"reference {reference_seconds:.3f} s, vectorised {new_seconds:.3f} s, "
          f"speedup {reference_seconds / new_seconds:.1f}x")
    assert new_seconds * 3 < reference_seconds
