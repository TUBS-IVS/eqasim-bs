# braunschweig/calibration — the calibration corner

Single home for the project's offline calibration tooling. Runtime model
components (e.g. the per-band friction builder `braunschweig/gravity/friction.py`,
the secondary chainsolvers scorer) stay with the model; this corner holds only the
shared metrics, MiD distribution targets, the per-model calibration loops, their
CLIs and reports. It consumes the runtime components and emits pinned YAML; it is
never imported by the runtime pipeline.

The unifying objective: the realised home->activity STRAIGHT-LINE distance
distribution matches a committed MiD distribution target (EMD-minimised), with no
mode choice (synthesis-realised, upstream of MATSim). MiD routed Weglaengen are put
on the same axis as the euclidean model output via the documented detour factor
(metrics.apply_detour); the committed reference shares are never reshaped.

Modules:
- metrics.py   -- band_shares, emd_on_bands, apply_detour (SHARED)
- targets.py   -- P13 / W12 / T43 band-share loaders (SHARED)
- commute.py   -- commute/work gravity friction calibration (-> P13)
- secondary.py -- secondary chainsolvers scorer calibration (-> W12) [Plan B]
- education.py -- school / kita / university gravities (-> T43) [Phase 2]

Migration roadmap: the legacy calibrators (gravity per-RS7 slope, gravity decay,
education slopes) move here; the scripts/calibrate_*.py entries are thin shims.
