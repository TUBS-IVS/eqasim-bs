"""The shared MiD/SrV reported-time precision rule (issue #123, Phase 0 Task 1).

Both surveys ask respondents for clock times but only RECORD them on a coarse grid: a reported
time ending on the hour or quarter-hour (``:00``, ``:15``, ``:30``, ``:45``) is precise to +/- 7.5
minutes, one ending on any other multiple of five minutes is precise to +/- 2.5 minutes, and any
other reported minute (a genuine to-the-minute report) carries no rounding at all. This module is
the ONE place that states that rule, so the SrV departure-time reference builder (Task 2,
``braunschweig.calibration.srv_departure_times``) and the departure-time model (Task 3,
``braunschweig.popsim.departure_time_model``) cannot disagree on which half-width applies to a
given reported minute.

ASSUMPTION: a reported time is de-rounded by drawing an offset UNIFORMLY inside its half-width
(i.e. the true time is assumed uniformly distributed within the reporting cell the respondent
rounded to). This is the standard rounding-noise assumption for grid-reported survey times; no
finer within-cell distribution is available from either survey's documentation.
"""
from __future__ import annotations

import numpy as np

#: Half-width (minutes) of the reporting cell for a time reported on the quarter-hour grid
#: (minute of hour in {0, 15, 30, 45}), e.g. "7:15" could be anywhere in [7:07:30, 7:22:30).
QUARTER_HOUR_HALF_WIDTH_MINUTES = 7.5

#: Half-width (minutes) of the reporting cell for a time reported on the five-minute grid
#: (minute of hour a multiple of 5, but not of 15), e.g. "7:05" could be anywhere in
#: [7:02:30, 7:07:30).
FIVE_MINUTE_HALF_WIDTH_MINUTES = 2.5


def half_width_minutes(minute_of_hour: np.ndarray) -> np.ndarray:
    """Return the reporting-precision half-width (minutes) for each ``minute_of_hour`` value.

    ``QUARTER_HOUR_HALF_WIDTH_MINUTES`` where ``minute_of_hour % 15 == 0``,
    ``FIVE_MINUTE_HALF_WIDTH_MINUTES`` where ``minute_of_hour % 5 == 0`` (and not already caught
    by the quarter-hour case), ``0.0`` otherwise (a to-the-minute report, taken as exact).

    Parameters
    ----------
    minute_of_hour:
        Integer (or integer-valued) minute-of-hour, e.g. ``departure_seconds // 60 % 60``.

    Returns
    -------
    np.ndarray
        Half-widths in minutes, same shape as ``minute_of_hour``.
    """
    minute_of_hour = np.asarray(minute_of_hour)
    half_width = np.zeros(minute_of_hour.shape, dtype=float)
    half_width[minute_of_hour % 5 == 0] = FIVE_MINUTE_HALF_WIDTH_MINUTES
    half_width[minute_of_hour % 15 == 0] = QUARTER_HOUR_HALF_WIDTH_MINUTES
    return half_width


def deround_minutes_of_day(minutes_of_day: np.ndarray, rng: np.random.RandomState):
    """De-round reported times-of-day by drawing uniformly inside the reporting-precision cell.

    Parameters
    ----------
    minutes_of_day:
        Reported time of day, in MINUTES (e.g. 420.0 for 7:00). Only ``minutes_of_day % 60``
        (the minute-of-hour) decides the half-width; the hour is irrelevant to the rule.
    rng:
        ``np.random.RandomState`` used for the single ``uniform`` draw per element (one RNG call,
        vectorised over all elements, for reproducibility with a given seed).

    Returns
    -------
    (derounded, offset):
        ``derounded`` -- ``minutes_of_day + offset``, in minutes.
        ``offset`` -- the offset actually drawn for each element, in minutes, ``U(-h, +h)`` where
        ``h`` is that element's :func:`half_width_minutes`; ``0.0`` for an exact (to-the-minute)
        report.
    """
    minutes_of_day = np.asarray(minutes_of_day, dtype=float)
    half_width = half_width_minutes(minutes_of_day % 60.0)
    # A single vectorised draw covers every element regardless of its half-width; multiplying by
    # 2*half_width - half_width maps U(0, 1) onto U(-half_width, +half_width), collapsing to
    # exactly 0.0 where half_width is 0.0 (an exact, to-the-minute report).
    offset = rng.uniform(size=minutes_of_day.shape) * 2.0 * half_width - half_width
    derounded = minutes_of_day + offset
    return derounded, offset
