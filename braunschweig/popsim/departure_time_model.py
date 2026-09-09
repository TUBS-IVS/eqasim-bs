"""The departure-time model (issue #123, ADR-0114): constants for now, logic in Task 3.

This module will eventually hold the departure-time model that replaces eqasim's uniform
per-person jitter (:func:`braunschweig.popsim.trips_stage.apply_per_person_jitter`) with a
precision-aware alternative: de-rounding a reported clock time inside the half-width of the MiD/
SrV reporting grid (:mod:`braunschweig.calibration.reported_time_precision`), and -- for the
``"srv_mapped"`` model -- quantile-mapping the de-rounded first departure into the committed SrV
departure-time reference (:mod:`braunschweig.calibration.srv_departure_times`, Task 2) so the
model's realised start-time distribution matches the survey's rather than eqasim's uninformative
uniform draw. See ``MODEL_EQASIM_UNIFORM`` / ``MODEL_DEROUNDED`` / ``MODEL_SRV_MAPPED`` and
``apply_departure_time_model`` (Task 3) for the dispatch between the three.

Only :data:`OFFSET_COLUMN` is defined here for now (Phase 0, Task 1). It is declared in THIS
module -- rather than in ``trips_stage``, which is where the brief for this task originally
placed it -- so that Task 3 can have ``trips_stage`` import it here at module level (no cycle)
while THIS module later imports ``trips_stage.apply_per_person_jitter`` lazily, inside
``apply_departure_time_model``, for the ``"eqasim_uniform"`` branch (which must delegate to the
existing jitter byte-identically). ``trips_stage`` re-exports the name as
``trips_stage.OFFSET_COLUMN`` so existing call sites and tests keep working unchanged.
"""
from __future__ import annotations

#: Column recording the per-person departure-time offset actually applied by
#: :func:`braunschweig.popsim.trips_stage.apply_per_person_jitter` (and, from Task 3 onward, by
#: :func:`apply_departure_time_model`'s other models): the SAME offset, already rounded to whole
#: seconds, that was added to both ``departure_time`` and ``arrival_time``. Recorded so later
#: analysis can decompose a model time as ``departure_time = raw_departure_time + offset`` without
#: re-deriving the offset from the RNG stream.
OFFSET_COLUMN = "departure_time_offset_seconds"
