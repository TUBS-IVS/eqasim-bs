import csv
import logging
import shutil
import os.path

import matsim.runtime.eqasim as eqasim
import matsim.runtime.java as java
from braunschweig.matsim import config_modules
from braunschweig.matsim.scenario import day_end_margin
from braunschweig.popsim.plan_validation import MAX_PLAN_TIME_SECONDS

logger = logging.getLogger(__name__)

_PASSENGER_AVAILABILITY_ADAPTER = (
    "org.eqasim.braunschweig.scenario.RunAdaptPassengerAvailabilityConfig")
_LEGACY_ADAPTER = "org.eqasim.braunschweig.scenario.RunAdaptConfig"

# Large-site access rule (issue #465, ADR-0169), Java RunAdaptLargeFacilityAccess between RunPreparation and the
# routing. eqasim's FacilityPlacement attaches every facility to its nearest non-motorway link whatever its capacity.
# The rule works on SITES (all facilities at one coordinate, incommuter workplaces included) of at least
# ``minimum_activities`` activities at 100 %: work sites and universities spread their car access over the parking
# lots around them (nearest first until ``parking_demand_share`` x size spaces, within ``parking_maximum_distance_m``;
# each lot on the closest link of ``minimum_capacity_veh_per_h`` within ``lot_link_maximum_distance_m``, else the
# closest link within that distance); every other large site gets the closest link of ``minimum_capacity_veh_per_h``
# within ``maximum_distance_m``. Owner decisions of 2026-10-23 (ADR-0169); the bounds fail the step when the lots
# file or the network is broken.
_LARGE_FACILITY_ACCESS_RUNNER = "org.eqasim.braunschweig.scenario.RunAdaptLargeFacilityAccess"
_LARGE_FACILITY_ACCESS_SUMMARY = "large_facility_access_summary.csv"
_LARGE_FACILITY_ACCESS_GROUPS = "large_facility_access_groups.csv"
#: Public: the production wrapper braunschweig.matsim.simulation.prepare hashes the release in its validate().
LARGE_FACILITY_ACCESS_LOTS_KEY = "large_facility_access_lots_path"
#: The parking-lot release relative to data_path (data record parking_lots_osm_2026, preflight D20).
_LARGE_FACILITY_ACCESS_LOTS_DEFAULT = "braunschweig/parking/parking_lots_osm_2026.csv"
LARGE_FACILITY_ACCESS_LOTS_DATASET = "parking_lots_osm_2026"
#: Numeric keys with their defaults, the Java option they feed, and their valid range.
_LARGE_FACILITY_ACCESS_NUMBERS = (
    ("large_facility_access_minimum_activities", 1000.0, "minimum-activities", lambda v: v >= 0.0, ">= 0"),
    ("large_facility_access_minimum_capacity_veh_per_h", 1000.0, "minimum-capacity-veh-per-h", lambda v: v >= 0.0,
     ">= 0"),
    ("large_facility_access_maximum_distance_m", 1000.0, "maximum-distance-m", lambda v: v > 0.0, "> 0"),
    ("large_facility_access_maximum_fallback_share", 0.15, "maximum-fallback-share", lambda v: 0.0 <= v <= 1.0,
     "in [0, 1]"),
    # ASSUMPTION of the owner: half of a site's activities need a parking space at the same time.
    ("large_facility_access_lot_demand_share", 0.5, "parking-demand-share", lambda v: 0.0 < v <= 10.0,
     "in (0, 10]"),
    ("large_facility_access_lot_maximum_distance_m", 1500.0, "parking-maximum-distance-m", lambda v: v > 0.0,
     "> 0"),
    ("large_facility_access_lot_link_maximum_distance_m", 300.0, "lot-link-maximum-distance-m", lambda v: v > 0.0,
     "> 0"),
    ("large_facility_access_maximum_no_lot_share", 0.10, "maximum-no-lot-share", lambda v: 0.0 <= v <= 1.0,
     "in [0, 1]"),
    ("large_facility_access_scope_work_share", 0.5, "scope-work-share", lambda v: 0.0 <= v <= 1.0,
     "in [0, 1]"),
)
#: Text keys (comma-separated prefix lists) with their defaults and Java options. Never sites: the cordon portals
#: (placed by the portal stages on purpose), the external Gemeinde centroids and homes. In scope: universities.
_LARGE_FACILITY_ACCESS_PREFIXES = (
    ("large_facility_access_excluded_prefixes", "outside_,EXT,home_", "excluded-facility-prefixes"),
    ("large_facility_access_scope_prefixes", "uni_loc_", "scope-facility-prefixes"),
)
_LARGE_FACILITY_ACCESS_OUTCOMES = ("distributed", "distributed_partial", "moved", "fallback", "adequate")

# MATSim's QSim moves departing vehicles onto a link BEFORE the vehicles already driving on it by default
# (insertingWaitingVehiclesBeforeDrivingVehicles = true), so a stream of departures can take every flow slot
# and hold through traffic for hours (#465). False lets traffic on the road go first, as at a site exit.
_QSIM_INSERTION_KEY = "matsim_qsim_inserting_waiting_vehicles_first"
_QSIM_INSERTION_PARAM = "insertingWaitingVehiclesBeforeDrivingVehicles"


#: Columns the Java step reads from the parking-lot release (eqasim-java-bs ParkingLotReader).
_LARGE_FACILITY_ACCESS_LOTS_COLUMNS = ("lot_id", "x", "y", "spaces")


def _parking_lots_path(context):
    """Absolute path of the parking-lot release, checked before the preparation runs: FileNotFoundError naming the
    config key when the file is missing, ValueError when its header lacks a column the Java step reads (the rows are
    validated by the Java step)."""
    path = os.path.join(context.config("data_path"), context.config(LARGE_FACILITY_ACCESS_LOTS_KEY))
    if not os.path.isfile(path):
        raise FileNotFoundError("%s: the parking-lot release %s is missing (data record %s, preflight D20; "
                                "regenerate it with scripts/build_parking_lots_from_osm.py)"
                                % (LARGE_FACILITY_ACCESS_LOTS_KEY, path, LARGE_FACILITY_ACCESS_LOTS_DATASET))
    with open(path, encoding="utf-8") as handle:
        header = [column.strip().strip('"') for column in handle.readline().strip().split(";")]
    missing = [column for column in _LARGE_FACILITY_ACCESS_LOTS_COLUMNS if column not in header]
    if missing:
        raise ValueError("%s: the parking-lot release %s lacks the columns %s (header: %s)"
                         % (LARGE_FACILITY_ACCESS_LOTS_KEY, path, missing, ";".join(header)))
    return os.path.abspath(path)


def _prefix_text(value, key):
    """A prefix-list config value as the comma-separated text the Java step reads.

    Accepts a comma-separated string or a YAML list / tuple of strings; ValueError naming ``key`` for any other
    type or an empty entry (``str()`` of a list would reach Java as one prefix that matches nothing, which would turn
    portals and homes into sites)."""
    if isinstance(value, str):
        entries = [entry.strip() for entry in value.split(",")] if value.strip() else []
    elif isinstance(value, (list, tuple)) and all(isinstance(entry, str) for entry in value):
        entries = [entry.strip() for entry in value]
    else:
        raise ValueError("%s must be a comma-separated text or a list of texts, got %r" % (key, value))
    if any(not entry or "," in entry for entry in entries):
        raise ValueError("%s has an empty or malformed entry: %r" % (key, value))
    return ",".join(entries)


def _require_bool(value, key):
    """``value`` when it is a boolean; ValueError naming ``key`` for any other type (the text "false" is truthy in
    Python). ``None`` passes: synpp returns the declared default, only configure-time test doubles return None."""
    if value is not None and not isinstance(value, bool):
        raise ValueError("%s must be true or false, got %r" % (key, value))
    return value


def _log_large_facility_access_summary(summary_path):
    """Log the site outcome rates of the large-site access step from its summary CSV; return the counts.

    The Java step logs the same rates; this line puts them into the pipeline log under the stage's own prefix and
    warns when a site kept its low-capacity link or was only partly covered by lots (ADR-0169).
    """
    counts = {outcome: 0 for outcome in _LARGE_FACILITY_ACCESS_OUTCOMES}
    counts["in_scope"] = 0
    counts["in_scope_without_lot"] = 0
    with open(summary_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if row["outcome"] not in _LARGE_FACILITY_ACCESS_OUTCOMES:
                raise ValueError("%s: unknown outcome %r for site %s" % (summary_path, row["outcome"], row["site_key"]))
            counts[row["outcome"]] += 1
            in_scope = row["in_scope"] == "true"
            counts["in_scope"] += in_scope
            counts["in_scope_without_lot"] += in_scope and int(row["lots_used"]) == 0
    sites = sum(counts[outcome] for outcome in _LARGE_FACILITY_ACCESS_OUTCOMES)
    distributed = counts["distributed"] + counts["distributed_partial"]
    needing = counts["moved"] + counts["fallback"]

    def share(count, total):
        return 100.0 * count / total if total else 0.0

    logger.info("[matsim.simulation.prepare] large facility access: %d large sites, %d in scope; distributed over parking "
                "lots %d/%d (%.1f %%), of them partly covered %d; in scope without a usable lot %d/%d (%.1f %%); "
                "single-link rule: adequate %d, moved %d, fallback %d/%d (%.1f %%) (ADR-0169, %s)", sites,
                counts["in_scope"], distributed, counts["in_scope"], share(distributed, counts["in_scope"]),
                counts["distributed_partial"], counts["in_scope_without_lot"], counts["in_scope"],
                share(counts["in_scope_without_lot"], counts["in_scope"]), counts["adequate"], counts["moved"],
                counts["fallback"], needing, share(counts["fallback"], needing), summary_path)
    if counts["fallback"] or counts["distributed_partial"] or counts["in_scope_without_lot"]:
        logger.warning("[matsim.simulation.prepare] large facility access: %d sites kept their low-capacity link, %d "
                       "in-scope sites found fewer parking spaces than wanted and %d none, see %s", counts["fallback"],
                       counts["distributed_partial"], counts["in_scope_without_lot"], summary_path)
    return counts


def configure(context):
    # synpp scopes config per stage (issue #229): execute() calls eqasim.run() ->
    # java.run(), which reads the java binary/memory options AND the hang-watchdog
    # options (#330) from THIS stage's context. Delegate to java's own configure()
    # so the declares cannot drift from what java.run actually reads. Required
    # rather than optional for the watchdog keys: they are volatile, and synpp does
    # not propagate volatile options to downstream stages.
    java.configure(context)

    context.config("mode_choice", False)
    # The passenger-specific Java entrypoint is a capability negotiation: a MiD
    # population carrying carPassengerAvailability needs a JAR that understands
    # it.  Old/open configurations deliberately retain the legacy entrypoint.
    context.config("mid_passenger_availability", True)
    context.config("braunschweig.population.method", None)
    # Routed day-end check (eqasim-bs#465, ADR-0160): the population writer moves late days earlier by their
    # SURVEY travel times (ADR-0154); the routing repeats the rule with the ROUTED travel times.
    context.config(day_end_margin.KEY_ENABLED, day_end_margin.DEFAULT_ENABLED)
    context.config(day_end_margin.KEY_ROUTED_CHECK, day_end_margin.DEFAULT_ROUTED_CHECK)
    if context.config(day_end_margin.KEY_ENABLED) and context.config(day_end_margin.KEY_ROUTED_CHECK):
        day_end_margin.check_margin(
            context.config(day_end_margin.KEY_LAST_ARRIVAL_MARGIN_S, day_end_margin.DEFAULT_LAST_ARRIVAL_MARGIN_S),
            MAX_PLAN_TIME_SECONDS)
    
    context.stage("matsim.scenario.population")
    context.stage("matsim.scenario.households")
    context.stage("matsim.scenario.vehicles")

    context.stage("matsim.scenario.facilities")
    context.stage("matsim.scenario.supply.processed")
    context.stage("matsim.scenario.supply.gtfs")

    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.eqasim")

    context.stage("data.spatial.departments")
    context.stage("data.spatial.codes")

    context.config("sampling_rate")
    # Execution detail, not scientific config: changing it must not devalidate cached stages (upstream eqasim-france #438)
    context.config("processes", volatile = True)
    # Thread count for the MATSim preparation/routing/config-generation steps and
    # the simulation (qsim + global numberOfThreads, baked into the generated
    # config). Defaults to None -> use "processes", so existing configs are
    # unchanged. Decoupled from "processes" because the synthesis worker count is
    # memory-bound (large GeoDataFrames) while the MATSim phase is CPU-bound and
    # can use more of the machine's cores.
    context.config("matsim_threads", None)
    context.config("random_seed")

    context.config("output_prefix", "ile_de_france_")

    enabled = _require_bool(context.config("large_facility_access_enabled", True), "large_facility_access_enabled")
    context.config("data_path")
    context.config(LARGE_FACILITY_ACCESS_LOTS_KEY, _LARGE_FACILITY_ACCESS_LOTS_DEFAULT)
    if enabled:
        # Fail at configure time on a value the Java step would reject only after the preparation ran.
        for key, default, _option, valid, expected in _LARGE_FACILITY_ACCESS_NUMBERS:
            value = float(context.config(key, default))
            if not valid(value) or value == float("inf"):
                raise ValueError("%s must be a finite number %s, got %r" % (key, expected, value))
        for key, default, _option in _LARGE_FACILITY_ACCESS_PREFIXES:
            _prefix_text(context.config(key, default), key)
    _require_bool(context.config(_QSIM_INSERTION_KEY, False), _QSIM_INSERTION_KEY)

def population_routing_arguments(context, matsim_threads):
    """Arguments of eqasim's ``RunPopulationRouting``. With the routed day-end check on (ADR-0160), plans whose
    ROUTED last arrival is later than the end of the simulated day minus the margin move earlier as a whole day
    and are routed again (``--latest-last-arrival``, logged by the router as a rate)."""
    arguments = [
        "--config-path", "%sconfig.xml" % context.config("output_prefix"),
        "--output-path", "%spopulation.xml.gz" % context.config("output_prefix"),
        "--threads", matsim_threads,
        "--config:plans.inputPlansFile", "prepared_population.xml.gz"
    ]
    if context.config(day_end_margin.KEY_ENABLED) and context.config(day_end_margin.KEY_ROUTED_CHECK):
        arguments += ["--latest-last-arrival", day_end_margin.latest_last_arrival_s(
            context.config(day_end_margin.KEY_LAST_ARRIVAL_MARGIN_S), MAX_PLAN_TIME_SECONDS)]
    return arguments


def execute(context):
    # MATSim-phase thread count: explicit override or the global processes count.
    matsim_threads = context.config("matsim_threads") or context.config("processes")
    # Fail before the long preparation when the large-site access cannot run.
    parking_lots_path = _parking_lots_path(context) if context.config("large_facility_access_enabled") else None

    # Prepare input files
    facilities_path = "%s/%s" % (
        context.path("matsim.scenario.facilities"),
        context.stage("matsim.scenario.facilities")
    )

    population_path = "%s/%s" % (
        context.path("matsim.scenario.population"),
        context.stage("matsim.scenario.population")
    )

    network_path = "%s/%s" % (
        context.path("matsim.scenario.supply.processed"),
        context.stage("matsim.scenario.supply.processed")["network_path"]
    )

    eqasim.run(context, "org.eqasim.core.scenario.preparation.RunPreparation", [
        "--input-facilities-path", facilities_path,
        "--output-facilities-path", "%sfacilities.xml.gz" % context.config("output_prefix"),
        "--input-population-path", population_path,
        "--output-population-path", "prepared_population.xml.gz",
        "--input-network-path", network_path,
        "--output-network-path", "%snetwork.xml.gz" % context.config("output_prefix"),
        "--threads", matsim_threads
    ])

    assert os.path.exists("%s/%sfacilities.xml.gz" % (context.path(), context.config("output_prefix")))
    assert os.path.exists("%s/prepared_population.xml.gz" % context.path())
    assert os.path.exists("%s/%snetwork.xml.gz" % (context.path(), context.config("output_prefix")))

    if context.config("large_facility_access_enabled"):
        # Rewrites the prepared facilities and population in place, before the population is routed.
        arguments = [
            "--network-path", "%snetwork.xml.gz" % context.config("output_prefix"),
            "--input-facilities-path", "%sfacilities.xml.gz" % context.config("output_prefix"),
            "--output-facilities-path", "%sfacilities.xml.gz" % context.config("output_prefix"),
            "--input-population-path", "prepared_population.xml.gz",
            "--output-population-path", "prepared_population.xml.gz",
            "--parking-lots-path", parking_lots_path,
            "--summary-path", _LARGE_FACILITY_ACCESS_SUMMARY,
            "--groups-path", _LARGE_FACILITY_ACCESS_GROUPS,
            "--sample-size", context.config("sampling_rate"),
        ]
        for key, _default, option, _valid, _expected in _LARGE_FACILITY_ACCESS_NUMBERS:
            arguments += ["--" + option, context.config(key)]
        for key, _default, option in _LARGE_FACILITY_ACCESS_PREFIXES:
            arguments += ["--" + option, _prefix_text(context.config(key), key)]
        eqasim.run(context, _LARGE_FACILITY_ACCESS_RUNNER, arguments)
        summary_path = "%s/%s" % (context.path(), _LARGE_FACILITY_ACCESS_SUMMARY)
        assert os.path.exists(summary_path)
        assert os.path.exists("%s/%s" % (context.path(), _LARGE_FACILITY_ACCESS_GROUPS))
        _log_large_facility_access_summary(summary_path)

    # Copy remaining input files
    households_path = "%s/%s" % (
        context.path("matsim.scenario.households"),
        context.stage("matsim.scenario.households")
    )
    shutil.copy(households_path, "%s/%shouseholds.xml.gz" % (context.cache_path, context.config("output_prefix")))

    transit_schedule_path = "%s/%s" % (
        context.path("matsim.scenario.supply.processed"),
        context.stage("matsim.scenario.supply.processed")["schedule_path"]
    )
    shutil.copy(transit_schedule_path, "%s/%stransit_schedule.xml.gz" % (context.cache_path, context.config("output_prefix")))

    transit_vehicles_path = "%s/%s" % (
        context.path("matsim.scenario.supply.gtfs"),
        context.stage("matsim.scenario.supply.gtfs")["vehicles_path"]
    )
    shutil.copy(transit_vehicles_path, "%s/%stransit_vehicles.xml.gz" % (context.cache_path, context.config("output_prefix")))

    vehicles_path = "%s/%s" % (
        context.path("matsim.scenario.vehicles"),
        context.stage("matsim.scenario.vehicles")
    )
    shutil.copy(vehicles_path, "%s/%svehicles.xml.gz" % (context.cache_path, context.config("output_prefix")))

    # Generate base configuration
    eqasim.run(context, "org.eqasim.core.scenario.config.RunGenerateConfig", [
        "--sample-size", context.config("sampling_rate"),
        "--threads", matsim_threads,
        "--prefix", context.config("output_prefix"),
        "--random-seed", context.config("random_seed"),
        "--output-path", "generic_config.xml"
    ])
    assert os.path.exists("%s/generic_config.xml" % context.path())

    # Adapt the config through the capability-specific entrypoint only for an
    # enabled MiD population.  Do not fall back if the requested class is absent:
    # that failure means the configured JAR lacks the required capability.
    adapter = _PASSENGER_AVAILABILITY_ADAPTER if (
        context.config("mid_passenger_availability")
        and context.config("braunschweig.population.method") == "popsim_mid"
    ) else _LEGACY_ADAPTER
    eqasim.run(context, adapter, [
        "--input-path", "generic_config.xml",
        "--output-path", "%sconfig.xml" % context.config("output_prefix"),
        "--prefix", context.config("output_prefix")
    ])
    assert os.path.exists("%s/%sconfig.xml" % (context.path(), context.config("output_prefix")))

    insertion = "true" if context.config(_QSIM_INSERTION_KEY) else "false"
    previous = config_modules.set_param("%s/%sconfig.xml" % (context.path(), context.config("output_prefix")),
                                        "qsim", _QSIM_INSERTION_PARAM, insertion)
    logger.info("[matsim.simulation.prepare] qsim.%s = %s (generated value %s; ADR-0169)",
                _QSIM_INSERTION_PARAM, insertion, previous)
    
    # Optionally, perform mode choice
    if context.config("mode_choice"):
        if context.config(day_end_margin.KEY_ENABLED) and context.config(day_end_margin.KEY_ROUTED_CHECK):
            # ADR-0160: the standalone mode choice routes the population itself, without the routed day-end check
            logging.getLogger(__name__).warning(
                "[day end margin] %s is on, but mode_choice routes the population with RunStandaloneModeChoice, "
                "which does not apply it: no plan is checked by its routed last arrival", day_end_margin.KEY_ROUTED_CHECK)
        eqasim.run(context, "org.eqasim.core.standalone_mode_choice.RunStandaloneModeChoice", [
            "--config-path", "%sconfig.xml" % context.config("output_prefix"),
            "--config:standaloneModeChoice.outputDirectory", "mode_choice",
            "--config:global.numberOfThreads", matsim_threads,
            "--write-output-csv-trips", "true",
            "--skip-scenario-check", "true",
            "--config:plans.inputPlansFile", "prepared_population.xml.gz",
            "--eqasim-configurator-class", "org.eqasim.braunschweig.BraunschweigConfigurator"
        ])

        assert os.path.exists("%s/mode_choice/output_plans.xml.gz" % context.path())
        assert os.path.exists("%s/mode_choice/output_trips.csv" % context.path())
        assert os.path.exists("%s/mode_choice/output_pt_legs.csv" % context.path())

        shutil.copy("%s/mode_choice/output_plans.xml.gz" % context.path(),
                    "%s/%spopulation.xml.gz" % (context.path(), context.config("output_prefix")))
    else:
        # Route population
        eqasim.run(context, "org.eqasim.core.scenario.routing.RunPopulationRouting",
                   population_routing_arguments(context, matsim_threads))

    assert os.path.exists("%s/%spopulation.xml.gz" % (context.path(), context.config("output_prefix")))

    # Validate scenario
    eqasim.run(context, "org.eqasim.core.scenario.validation.RunScenarioValidator", [
        "--config-path", "%sconfig.xml" % context.config("output_prefix")
    ])

    # Cleanup
    os.remove("%s/prepared_population.xml.gz" % context.path())

    return "%sconfig.xml" % context.config("output_prefix")
