# Long-haul freight injection (german-wide-freight v3)


Long-haul road freight (heavy goods vehicles) is injected into the MATSim
scenario from the VSP **german-wide-freight v3** model (Lu, Martins-Turner,
Nagel 2022, *A simple, calibrated, agent-based, German-wide freight transport
model*, doi:10.1016/j.procs.2022.03.080). The freight plans and the
Germany-wide road network are **local-only** (large, not committed; the
`eqasim-data` tree is gitignored) and are fetched by
`scripts/download_german_wide_freight.py`, which prints the exact download
command when an input is missing. All freight geometry is processed in
**EPSG:25832** (the project-wide metric CRS).

Relative to the ZGB study area each freight trip is one of four categories:
**INTERNAL** (origin and destination both inside ZGB), **INCOMING**
(destination inside, origin outside), **OUTGOING** (origin inside,
destination outside) and **TRANSIT** (through-traffic: both endpoints outside
ZGB but the routed path crosses the study area). Determining the TRANSIT share
correctly requires **routing each freight trip on the German-wide road
network** and testing the route against the study-area polygon -- a
straight-line OD test would miss exactly the through-traffic that uses the ZGB
motorways (A2, A7, A39). This is why the published, peer-reviewed Java
extraction tool is used rather than re-implementing the classification.

The injection is a **three-stage hybrid** (flag-gated; see below):

1. **Java extraction (cached, 100%, one run per category).**
   `braunschweig.freight.extraction` runs the published matsim
   application-contrib tool `RunExtractFreightTrips`
   (`ExtractRelevantFreightTrips`) against the dissolved ZGB study-area polygon
   (the union of the in-scope municipalities, plus the cordon buffer when
   `cordon_enabled`) on the German-wide network. The tool routes every freight
   trip, classifies it, trims each plan at the study-area boundary and shifts
   the departure time by the access travel time. The stage runs the
   **unmodified tool once per category**
   (`--geographicalTripType INTERNAL/INCOMING/OUTGOING/TRANSIT`, ~45 min each)
   and returns `{category: plans_file}` -- the exact published classification,
   no geometric heuristic (trimmed endpoints lie on network nodes *inside* the
   polygon, so a point-in-polygon test cannot recover the category). The
   matsim **2026** contrib build (in lockstep with the parent pom's
   `matsim.version` since the dependency consolidation) also tags each output
   person with the `geographical_Trip_Type` attribute; the per-category runs
   remain the canonical partition. Verified CLI contract of this build (via
   `--help` against the built jar): the options are `--legMode` (was
   `--LegMode` in 2025.0-PR3568) and `--geographicalTripType` (was
   `--tripType`); `--subpopulation freight` must be passed explicitly (the
   tool now defaults to `longDistanceFreight`, the old build hard-coded
   `freight`); plans/network/output paths must be absolute (the tool NPEs on
   a bare `--output` filename). This
   stage is **sampling-rate independent** (cached by synpp), so the expensive
   routing runs once and is reused across sampling rates. The local-only inputs
   are validated up front by `braunschweig.data.freight.german_wide`, which
   fails early with the download command when a file is absent.

2. **Python trips stage.** `braunschweig.freight.trips` parses the four
   per-category plans files with a streaming `xml.etree` reader (deliberately
   **not** matsim-tools: the repo-local `matsim` package shadows the PyPI
   `matsim-tools` import, so the tooling reader is unavailable here), labels
   each trip with its extraction category, rewrites the per-file person ids to
   the collision-free, self-documenting `freight_<category>_<n>` (each
   per-category tool run renumbers from `freight_0`), writes an inspectable
   `freight_trips.gpkg`, and returns one tidy trips DataFrame.

3. **Injection hook.** `braunschweig/matsim/simulation/prepare.py`
   (`_inject_freight`) runs **after** the cordon cut. It Bernoulli-samples the
   trips DataFrame at `freight_sampling_rate` (`None` => `sampling_rate`; seeded
   RNG, offset `+81247`) and writes `freight_trips_sampled.csv`. Sampling the
   freight to the run's sampling rate is **required** because the global qsim
   `flowCapacityFactor` is scaled to the sampling rate -- injecting 100 % freight
   into a 25 % scenario would overload the links. It then runs the Java tool
   `RunInjectFreight`, which builds one freight agent per sampled row
   (subpopulation `freight`, single `truck` leg, vehicle type `heavy_truck`),
   adds `truck` to the car links' allowed modes, adapts the config, and writes
   the population / vehicles / network / config in place.

**Discrete-mode-choice isolation.** Freight agents must not participate in the
person mode choice: `BraunschweigModeAvailability` returns only `{truck}` for
subpopulation `freight`, and a constant-zero `FreightTruckUtilityEstimator` is
bound so the truck leg carries no behavioural utility (freight routes are fixed,
not re-chosen).

**Analysis exclusion.** Injected freight agents are person-trip artefacts, not
synthetic residents, so they are excluded from every person-travel analysis:
`braunschweig.analysis.freight_filter.drop_freight_agents` removes the
`freight_`-prefixed agents at every `eqasim_trips.csv` read (dashboard,
mid_validation, spatial-demand and behaviour tabs). With freight off the filter
is a no-op.

**Assumptions (both configurable, neither calibrated).** Two parameters are
explicit ASSUMPTIONS, not validated references: `freight_truck_pce` (passenger-
car equivalent **3.5**) and `freight_truck_max_velocity_kmh` (**80 km/h**, the
German StVO speed limit for HGVs > 7.5 t). They are exposed as config keys so a
later calibration can override them.

Config keys (registered in `prepare.configure`): `freight_enabled` (default
**true**), `freight_sampling_rate` (default `None` => `sampling_rate`),
`freight_truck_pce` (3.5), `freight_truck_max_velocity_kmh` (80.0); the
extraction stage additionally reads `freight_crs` (default EPSG:25832),
`freight_plans_path` and `freight_network_path` (defaults under
`braunschweig/freight/german-wide-freight-v3/`). The **OFF path**
(`freight_enabled: false`) is byte-identical to the pre-feature pipeline: no
freight stages are requested, no injection runs, and the analysis filter is a
no-op. The committed run configs reflect this -- the two real-data run configs
(`config_local_braunschweig.yml`, `config_server_braunschweig_100pct.yml`) set
`freight_enabled: true`; every other `config_*.yml` (dryrun, smoke, popsim,
intermediate sampling rates) sets `freight_enabled: false` so they never require
the local-only freight inputs.

A possible follow-up (NOT done) is to **calibrate the injected freight against
BASt automatic HGV counts** at the ZGB counting stations (Dauerzaehlstellen), so
the truck volumes on the ZGB motorways are validated against observed counts
rather than taken as-is from the german-wide-freight model.

Tests: `tests/test_download_german_wide_freight.py`,
`tests/test_freight_data.py`, `tests/test_freight_extraction.py`,
`tests/test_freight_trips.py`, `tests/test_freight_injection_wiring.py`,
`tests/test_freight_filter.py`.
