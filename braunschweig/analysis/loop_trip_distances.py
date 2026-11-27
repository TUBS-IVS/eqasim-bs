"""Survey diary length of "loop" trips for every analysis that reports trip distances (issue #679).

Background
----------
The synthesis places some leisure trips on foot or by bicycle (strolls, dog walks, "loops") at the place of the activity
BEFORE them, because the survey (MiD 2023) records such a walk as ONE trip whose length is the whole round walk. In the
model such a trip has origin == destination, so its crow-fly AND routed distance is 0 m. The trip AFTER a loop can also
become zero length when it ends at the loop's place (work, a stroll placed at work, back to work).

When the switch ``secondary_loop_zero_displacement`` is on, the synthesis output ``<prefix>trips.csv`` carries two
appended columns (produced by ``synthesis/output.py``; this module only reads them):

* ``is_loop_stroll`` (bool): the trip leads INTO a loop.
* ``loop_trip_diary_distance_km`` (float, km): the survey diary length (MiD ``wegkm_imp``, a PATH length) of every trip
  the loop placement turns into a zero-length trip, i.e. the trip into a loop (``is_loop_stroll`` True) and the trip out
  of a loop that ends at the loop's place (``is_loop_stroll`` False). Empty on every other trip and on a
  zero-length trip without a diary length (the plan repair's invented home trip).

The rule (ONE owner, used by the dashboard and by the 10 % validation package)
------------------------------------------------------------------------------
Every matched trip with a finite diary length gets that length INSTEAD of its 0 m:

* metric kind ``"path"`` (routed or network distance): the diary kilometres as they are;
* metric kind ``"crow_fly"`` (straight line): the diary kilometres divided by
  :data:`braunschweig.constants.ROUTED_DETOUR_FACTOR`, the same conversion ``braunschweig/popsim/trips_stage.py``
  applies to every diary length when it builds the crow-fly targets.

The mode of the analysed frame is never changed. A trip that the table marks must have a model crow-fly distance of at
most :data:`ZERO_LENGTH_TOLERANCE_METERS`; anything else means the trip indices of the two files do not line up and the
correction would overwrite a real distance, so it raises instead of correcting.

``traveldistancestats.csv`` and ``pkm_modestats.csv`` of MATSim (per-iteration averages and person kilometres) count
loops with 0 m; eqasim-java-bs writes both with the diary lengths next to them (``*_loops_counted.csv``), and the
dashboard reads that companion of the first when the run has it.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from braunschweig.constants import ROUTED_DETOUR_FACTOR

logger = logging.getLogger(__name__)

LOOP_FLAG_COLUMN = "is_loop_stroll"
LOOP_DIARY_DISTANCE_COLUMN = "loop_trip_diary_distance_km"

#: A trip the loop placement turned into a zero-length trip has a model crow-fly distance of exactly 0 m; a metre of
#: tolerance absorbs coordinate rounding in the CSV and GPKG outputs.
ZERO_LENGTH_TOLERANCE_METERS = 1.0

#: ASSUMPTION: an unmatched share (loop-table trips absent from the analysed frame, e.g. stuck agents or filtered
#: rows) above 5 % is unusual enough to warn about; the value is a judgement, not derived from data.
UNMATCHED_SHARE_WARNING_THRESHOLD = 0.05

#: Modes of the analysed frames that can carry a loop (strolls and dog walks on foot or by bicycle). ``bike`` is the
#: MATSim/eqasim spelling, ``bicycle`` the one of the synthesis output.
LOOP_MODES = ("walk", "bike", "bicycle")
LOOP_PURPOSE = "leisure"

METRIC_KIND_PATH = "path"
METRIC_KIND_CROW_FLY = "crow_fly"

_KEY_COLUMNS = ["person_id", "trip_index"]
_UNIT_TO_KILOMETERS = {"km": 1.0, "m": 0.001}
_MAX_KEYS_IN_ERROR = 5


def read_loop_trips(trips_csv_path: str | Path) -> pd.DataFrame | None:
    """Read the loop table of a synthesis ``<prefix>trips.csv``.

    Returns a frame ``person_id``, ``trip_index``, ``is_loop_stroll``, ``loop_trip_diary_distance_km`` (km) with the
    rows that have a finite diary length OR ``is_loop_stroll`` True, or ``None`` when the file lacks both loop
    columns (switch off, or an output written before the change; logged at INFO: no correction is possible).
    A file with only one of the two columns raises ``ValueError``; so does a duplicated trip key.
    """
    path = Path(trips_csv_path)
    if not path.exists():
        raise FileNotFoundError(f"synthesis trips file not found: {path}")
    header = pd.read_csv(path, sep=";", nrows=0).columns
    present = [c for c in (LOOP_FLAG_COLUMN, LOOP_DIARY_DISTANCE_COLUMN) if c in header]
    if not present:
        logger.info("[loop-trips] %s has no %s / %s columns (switch secondary_loop_zero_displacement off or an "
                    "output from before the change): no loop correction is possible, loop trips stay at 0 m",
                    path.name, LOOP_FLAG_COLUMN, LOOP_DIARY_DISTANCE_COLUMN)
        return None
    if len(present) == 1:
        raise ValueError(
            f"{path} has only the column {present[0]!r} of the pair {LOOP_FLAG_COLUMN!r} / "
            f"{LOOP_DIARY_DISTANCE_COLUMN!r}; the synthesis writes both or neither -- the file is damaged or "
            f"was written by an incompatible version")
    missing_keys = [c for c in _KEY_COLUMNS if c not in header]
    if missing_keys:
        raise ValueError(f"{path} lacks the key columns {missing_keys}")

    table = pd.read_csv(path, sep=";", usecols=[*_KEY_COLUMNS, LOOP_FLAG_COLUMN, LOOP_DIARY_DISTANCE_COLUMN])
    # An empty flag column is read as float NaN; only an explicit True marks a trip into a loop.
    table[LOOP_FLAG_COLUMN] = table[LOOP_FLAG_COLUMN].map(_as_bool).astype(bool)
    table[LOOP_DIARY_DISTANCE_COLUMN] = pd.to_numeric(table[LOOP_DIARY_DISTANCE_COLUMN], errors="coerce")
    keep = np.isfinite(table[LOOP_DIARY_DISTANCE_COLUMN]) | table[LOOP_FLAG_COLUMN]
    table = table.loc[keep, [*_KEY_COLUMNS, LOOP_FLAG_COLUMN, LOOP_DIARY_DISTANCE_COLUMN]].reset_index(drop=True)
    duplicated = table.duplicated(_KEY_COLUMNS, keep=False)
    if duplicated.any():
        raise ValueError(f"{path} has {int(duplicated.sum())} loop rows with a repeated (person_id, trip_index) key")
    logger.info("[loop-trips] %s: %d loop-table trips (%d into a loop, %d with a diary length)", path.name, len(table),
                int(table[LOOP_FLAG_COLUMN].sum()), int(np.isfinite(table[LOOP_DIARY_DISTANCE_COLUMN]).sum()))
    return table


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    if pd.isna(value):
        return False
    return bool(value)


def _normalised_keys(frame: pd.DataFrame, key_columns: Sequence[str]) -> pd.Series:
    """One string key per row; integer-valued numbers compare equal whether stored as int, float or text."""
    parts = []
    for column in key_columns:
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.notna().all():
            parts.append(numeric.astype("int64").astype(str))
        else:
            parts.append(frame[column].astype(str))
    keys = parts[0]
    for part in parts[1:]:
        keys = keys + "|" + part
    return keys


def apply_loop_trip_distances(
    trips: pd.DataFrame,
    loop_trips: pd.DataFrame | None,
    *,
    key_columns: Sequence[str],
    distance_column: str,
    distance_unit: str,
    metric_kind: str,
    crow_fly_column: str,
    crow_fly_unit: str = "m",
    mode_column: str = "mode",
    purpose_column: str = "following_purpose",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Replace the 0 m of loop trips in ``trips[distance_column]`` by their survey diary length.

    ``key_columns`` are the two columns of ``trips`` that match ``person_id`` and ``trip_index`` of the loop table, in
    that order. ``distance_unit`` (``"m"`` or ``"km"``) is the unit of ``distance_column``; ``metric_kind`` is
    ``"path"`` (diary kilometres as they are) or ``"crow_fly"`` (diary kilometres divided by
    ``ROUTED_DETOUR_FACTOR``). ``crow_fly_column`` (unit ``crow_fly_unit``) holds the model's straight-line distance
    and drives the consistency check: every matched trip must be at most ``ZERO_LENGTH_TOLERANCE_METERS`` long,
    otherwise ``ValueError`` (misaligned trip indices). The input frame is not modified.

    Returns ``(corrected_frame, report)``. Without a loop table the frame is returned unchanged and a WARNING counts
    the zero-length leisure walk/bicycle trips whose distance therefore stays 0 m.
    """
    if distance_unit not in _UNIT_TO_KILOMETERS or crow_fly_unit not in _UNIT_TO_KILOMETERS:
        raise ValueError(f"distance_unit and crow_fly_unit must be one of {sorted(_UNIT_TO_KILOMETERS)}, "
                         f"got {distance_unit!r} / {crow_fly_unit!r}")
    if metric_kind not in (METRIC_KIND_PATH, METRIC_KIND_CROW_FLY):
        raise ValueError(f"metric_kind must be {METRIC_KIND_PATH!r} or {METRIC_KIND_CROW_FLY!r}, got {metric_kind!r}")
    if len(key_columns) != 2:
        raise ValueError(f"key_columns must name the person and the trip column, got {list(key_columns)}")

    if loop_trips is None:
        return trips, _report_without_loop_table(trips, crow_fly_column, crow_fly_unit, mode_column, purpose_column)

    needed = [*key_columns, distance_column, crow_fly_column]
    missing = [c for c in needed if c not in trips.columns]
    if missing:
        raise ValueError(f"the analysed trips lack the columns {missing}: the loop correction and its consistency "
                         f"check cannot run")

    out = trips.copy()
    out_keys = _normalised_keys(out, key_columns)
    loop_keys = _normalised_keys(loop_trips, _KEY_COLUMNS)
    if out_keys.duplicated().any():
        raise ValueError(f"the analysed trips repeat a ({key_columns[0]}, {key_columns[1]}) key; "
                         f"the loop correction needs one row per trip")
    position = pd.Series(np.arange(len(out)), index=out_keys.to_numpy())
    matched_mask = loop_keys.isin(position.index).to_numpy()
    matched = loop_trips.loc[matched_mask].reset_index(drop=True)
    row_of_match = position.loc[loop_keys[matched_mask].to_numpy()].to_numpy()

    # Primary-path guard: a marked trip is zero length in the model; a longer one means misaligned indices.
    crow_fly_meters = (out[crow_fly_column].to_numpy(dtype=float)[row_of_match]
                       * _UNIT_TO_KILOMETERS[crow_fly_unit] * 1000.0)
    not_zero_length = ~(crow_fly_meters <= ZERO_LENGTH_TOLERANCE_METERS)
    if not_zero_length.any():
        examples = [(matched.iloc[i][_KEY_COLUMNS[0]], matched.iloc[i][_KEY_COLUMNS[1]])
                    for i in np.flatnonzero(not_zero_length)[:_MAX_KEYS_IN_ERROR]]
        raise ValueError(
            f"{int(not_zero_length.sum())} of {len(matched)} loop-table trips have a model crow-fly distance above "
            f"{ZERO_LENGTH_TOLERANCE_METERS} m (or none) in column {crow_fly_column!r}; first keys {examples}. "
            f"The trip indices of the synthesis trips file and the analysed trips do not line up; the loop "
            f"correction would overwrite real distances.")

    diary_km = matched[LOOP_DIARY_DISTANCE_COLUMN].to_numpy(dtype=float)
    has_diary = np.isfinite(diary_km)
    divisor = ROUTED_DETOUR_FACTOR if metric_kind == METRIC_KIND_CROW_FLY else 1.0
    new_value = diary_km / divisor / _UNIT_TO_KILOMETERS[distance_unit]

    distance_position = out.columns.get_loc(distance_column)
    rows_to_correct = row_of_match[has_diary]
    modes = out[mode_column].to_numpy() if mode_column in out.columns else np.full(len(out), "unknown", dtype=object)
    before_km = out[distance_column].to_numpy(dtype=float)[rows_to_correct] * _UNIT_TO_KILOMETERS[distance_unit]
    corrected_modes = modes[rows_to_correct]
    out[distance_column] = out[distance_column].astype(float)
    out.iloc[rows_to_correct, distance_position] = new_value[has_diary]
    after_km = new_value[has_diary] * _UNIT_TO_KILOMETERS[distance_unit]

    into_loop = matched[LOOP_FLAG_COLUMN].to_numpy(dtype=bool)
    n_unmatched = int((~matched_mask).sum())
    report: dict[str, Any] = {
        "available": True,
        "metric_kind": metric_kind,
        "into_loop": _group_counts(into_loop, has_diary),
        "back_to_loop_start": _group_counts(~into_loop, has_diary),
        "unmatched": n_unmatched,
        "unmatched_share": float(n_unmatched / len(loop_trips)) if len(loop_trips) else 0.0,
        "km_before_by_mode": _sum_by_mode(corrected_modes, before_km),
        "km_after_by_mode": _sum_by_mode(corrected_modes, after_km),
    }

    logger.info(
        "[loop-trips] %s distances: into a loop matched %d / corrected %d / without diary length %d; back to the "
        "loop start matched %d / corrected %d / without diary length %d; unmatched %d of %d loop-table trips "
        "(%.1f%%); corrected trips %.1f km before, %.1f km after",
        metric_kind,
        report["into_loop"]["matched"], report["into_loop"]["corrected"], report["into_loop"]["without_diary_length"],
        report["back_to_loop_start"]["matched"], report["back_to_loop_start"]["corrected"],
        report["back_to_loop_start"]["without_diary_length"],
        n_unmatched, len(loop_trips), 100.0 * report["unmatched_share"], float(before_km.sum()), float(after_km.sum()))
    if report["unmatched_share"] > UNMATCHED_SHARE_WARNING_THRESHOLD:
        logger.warning(
            "[loop-trips] %.1f%% of the loop-table trips (%d of %d) are absent from the analysed trips (above "
            "%.0f%%): stuck agents, filtered rows or another run's trips file?",
            100.0 * report["unmatched_share"], n_unmatched, len(loop_trips),
            100.0 * UNMATCHED_SHARE_WARNING_THRESHOLD)
    return out, report


def _group_counts(in_group: np.ndarray, has_diary: np.ndarray) -> dict[str, int]:
    return {
        "matched": int(in_group.sum()),
        "corrected": int((in_group & has_diary).sum()),
        "without_diary_length": int((in_group & ~has_diary).sum()),
    }


def _sum_by_mode(modes: np.ndarray, kilometers: np.ndarray) -> dict[str, float]:
    sums: dict[str, float] = {}
    for mode, value in zip(modes, kilometers):
        sums[str(mode)] = sums.get(str(mode), 0.0) + float(value)
    return {mode: round(value, 3) for mode, value in sorted(sums.items())}


def _report_without_loop_table(trips: pd.DataFrame, crow_fly_column: str, crow_fly_unit: str, mode_column: str,
                               purpose_column: str) -> dict[str, Any]:
    """No loop table: leave the frame alone and count the zero-length leisure walk/bicycle trips that stay 0 m."""
    report: dict[str, Any] = {"available": False}
    if not {crow_fly_column, mode_column, purpose_column} <= set(trips.columns):
        return report
    crow_fly_meters = trips[crow_fly_column].astype(float) * _UNIT_TO_KILOMETERS[crow_fly_unit] * 1000.0
    suspects = ((crow_fly_meters <= ZERO_LENGTH_TOLERANCE_METERS) & (trips[purpose_column] == LOOP_PURPOSE)
                & trips[mode_column].isin(LOOP_MODES))
    n_suspects = int(suspects.sum())
    report["zero_length_leisure_walk_bicycle_trips"] = n_suspects
    if n_suspects:
        logger.warning(
            "[loop-trips] no loop table: %d zero-length leisure walk/bicycle trips keep a distance of 0 m because "
            "the synthesis output carries no loop diary length (switch secondary_loop_zero_displacement off or an "
            "older output)", n_suspects)
    return report
