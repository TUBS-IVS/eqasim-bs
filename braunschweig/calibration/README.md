# braunschweig/calibration — the calibration corner

Offline calibration tooling: shared metrics, MiD/SrV distribution targets, the
per-model calibration loops, their CLIs and reports. Runtime model components
(e.g. the per-band friction builder `braunschweig/gravity/friction.py`, the
secondary chainsolvers scorer) stay with the model. Some modules here are also
imported by runtime stages (for example calibrated-parameter readers); check
the importers of a module before treating it as offline-only.

The unifying objective: the realised home->activity STRAIGHT-LINE distance
distribution matches a committed MiD distribution target (EMD-minimised), with no
mode choice (synthesis-realised, upstream of MATSim). MiD routed Weglaengen are put
on the same axis as the euclidean model output via the documented detour factor
(`metrics.apply_detour`); the committed reference shares are never reshaped.

Modules (the directory is the authority; this list is by role):
- Shared: `metrics.py`, `targets.py`, `decision.py`, `circuity.py`, `detour_fit.py`,
  `reported_time_precision.py`, `anchor_holdout.py`
- Calibration loops: `commute.py`, `secondary.py`, `secondary_measurement.py`,
  `building_fit.py`, `run_building_fit.py`, `run_building_fit_secondary.py`,
  `commute_day_state_reference.py`
- SrV-derived targets: `srv_*.py` (absence, departure times, distance targets,
  fine purpose, mode by distance band, parking, participation universe, plan
  structure, work participation)
- Legacy calibrators kept for the shims: `_legacy_education_slopes.py`,
  `_legacy_gravity_decay.py`, `_legacy_gravity_per_rs7.py`

The `scripts/calibrate_*.py` entries are thin shims over these modules. There is
no `education.py`; education gravities are calibrated by `_legacy_education_slopes.py`.
