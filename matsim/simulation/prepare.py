import csv
import logging
import shutil
import os.path

import matsim.runtime.eqasim as eqasim
import matsim.runtime.java as java
from braunschweig.matsim import config_modules

logger = logging.getLogger(__name__)

_PASSENGER_AVAILABILITY_ADAPTER = (
    "org.eqasim.braunschweig.scenario.RunAdaptPassengerAvailabilityConfig")
_LEGACY_ADAPTER = "org.eqasim.braunschweig.scenario.RunAdaptConfig"

# Large-facility access rule (issue #465, ADR-0165). eqasim's FacilityPlacement attaches every facility to its
# nearest non-motorway link whatever its capacity; facilities of at least ``minimum_activities`` activities
# (scaled to 100 %) on a link below ``minimum_capacity_veh_per_h`` move to the closest eligible link of that
# capacity within ``maximum_distance_m`` (Java RunAdaptLargeFacilityAccess). Defaults from
# scripts/measure_large_facility_access.py on the 1 % rerun and the 100 % run (ADR-0165): fallback 8.6 % / 6.8 %
# of the facilities that need a new link, so a share above 0.15 means the rule is broken.
_LARGE_FACILITY_ACCESS_RUNNER = "org.eqasim.braunschweig.scenario.RunAdaptLargeFacilityAccess"
_LARGE_FACILITY_ACCESS_SUMMARY = "large_facility_access_summary.csv"
_LARGE_FACILITY_ACCESS_DEFAULTS = {
    "large_facility_access_minimum_activities": 1000.0,
    "large_facility_access_minimum_capacity_veh_per_h": 1000.0,
    "large_facility_access_maximum_distance_m": 1000.0,
    "large_facility_access_maximum_fallback_share": 0.15,
}
# Cordon portals (``outside_*``) are placed by the portal stages on purpose, often on motorways.
_LARGE_FACILITY_ACCESS_EXCLUDED_PREFIXES = "outside_"

# MATSim's QSim moves departing vehicles onto a link BEFORE the vehicles already driving on it by default
# (insertingWaitingVehiclesBeforeDrivingVehicles = true), so a stream of departures can take every flow slot
# and hold through traffic for hours (#465). False lets traffic on the road go first, as at a site exit.
_QSIM_INSERTION_KEY = "matsim_qsim_inserting_waiting_vehicles_first"
_QSIM_INSERTION_PARAM = "insertingWaitingVehiclesBeforeDrivingVehicles"


def _check_large_facility_access_parameters(minimum_activities, minimum_capacity_veh_per_h, maximum_distance_m,
                                            maximum_fallback_share):
    """Fail at configure time on a value the Java step would reject only after the preparation ran."""
    checks = (
        ("large_facility_access_minimum_activities", minimum_activities, lambda v: v >= 0.0, ">= 0"),
        ("large_facility_access_minimum_capacity_veh_per_h", minimum_capacity_veh_per_h, lambda v: v >= 0.0, ">= 0"),
        ("large_facility_access_maximum_distance_m", maximum_distance_m, lambda v: v > 0.0, "> 0"),
        ("large_facility_access_maximum_fallback_share", maximum_fallback_share, lambda v: 0.0 <= v <= 1.0,
         "in [0, 1]"),
    )
    for key, value, valid, expected in checks:
        if not valid(float(value)) or float(value) == float("inf"):
            raise ValueError("%s must be a finite number %s, got %r" % (key, expected, value))

def _log_large_facility_access_summary(summary_path):
    """Log the moved / fallback rate of the large-facility access step from its summary CSV; return the counts.

    The Java step logs the same rates; this line puts them into the pipeline log under the stage's own prefix,
    and warns when any large facility kept its low-capacity link (the fallback of ADR-0165).
    """
    counts = {"moved": 0, "fallback": 0, "adequate": 0, "excluded": 0}
    with open(summary_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            if row["outcome"] not in counts:
                raise ValueError("%s: unknown outcome %r for facility %s" % (summary_path, row["outcome"],
                                                                             row["facility_id"]))
            counts[row["outcome"]] += 1
    needing = counts["moved"] + counts["fallback"]

    def share(count):
        return 100.0 * count / needing if needing else 0.0

    logger.info("[matsim.simulation.prepare] large facility access: moved %d/%d (%.1f %%), fallback %d/%d (%.1f %%),"
                " already adequate %d, excluded %d (ADR-0165, %s)", counts["moved"], needing, share(counts["moved"]),
                counts["fallback"], needing, share(counts["fallback"]), counts["adequate"], counts["excluded"],
                summary_path)
    if counts["fallback"]:
        logger.warning("[matsim.simulation.prepare] large facility access: %d large facilities kept their"
                       " low-capacity link (no eligible link within the maximum distance), see %s",
                       counts["fallback"], summary_path)
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

    context.config("large_facility_access_enabled", True)
    if context.config("large_facility_access_enabled"):
        values = [context.config(key, default) for key, default in _LARGE_FACILITY_ACCESS_DEFAULTS.items()]
        _check_large_facility_access_parameters(*values)
        context.config("large_facility_access_excluded_prefixes", _LARGE_FACILITY_ACCESS_EXCLUDED_PREFIXES)
    context.config(_QSIM_INSERTION_KEY, False)

def execute(context):
    # MATSim-phase thread count: explicit override or the global processes count.
    matsim_threads = context.config("matsim_threads") or context.config("processes")

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
        eqasim.run(context, _LARGE_FACILITY_ACCESS_RUNNER, [
            "--network-path", "%snetwork.xml.gz" % context.config("output_prefix"),
            "--input-facilities-path", "%sfacilities.xml.gz" % context.config("output_prefix"),
            "--output-facilities-path", "%sfacilities.xml.gz" % context.config("output_prefix"),
            "--input-population-path", "prepared_population.xml.gz",
            "--output-population-path", "prepared_population.xml.gz",
            "--summary-path", _LARGE_FACILITY_ACCESS_SUMMARY,
            "--sample-size", context.config("sampling_rate"),
            "--minimum-activities", context.config("large_facility_access_minimum_activities"),
            "--minimum-capacity-veh-per-h", context.config("large_facility_access_minimum_capacity_veh_per_h"),
            "--maximum-distance-m", context.config("large_facility_access_maximum_distance_m"),
            "--maximum-fallback-share", context.config("large_facility_access_maximum_fallback_share"),
            "--excluded-facility-prefixes", context.config("large_facility_access_excluded_prefixes"),
        ])
        summary_path = "%s/%s" % (context.path(), _LARGE_FACILITY_ACCESS_SUMMARY)
        assert os.path.exists(summary_path)
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
    logger.info("[matsim.simulation.prepare] qsim.%s = %s (generated value %s; ADR-0165)",
                _QSIM_INSERTION_PARAM, insertion, previous)
    
    # Optionally, perform mode choice
    if context.config("mode_choice"):
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
        eqasim.run(context, "org.eqasim.core.scenario.routing.RunPopulationRouting", [
            "--config-path", "%sconfig.xml" % context.config("output_prefix"),
            "--output-path", "%spopulation.xml.gz" % context.config("output_prefix"),
            "--threads", matsim_threads,
            "--config:plans.inputPlansFile", "prepared_population.xml.gz"
        ])

    assert os.path.exists("%s/%spopulation.xml.gz" % (context.path(), context.config("output_prefix")))

    # Validate scenario
    eqasim.run(context, "org.eqasim.core.scenario.validation.RunScenarioValidator", [
        "--config-path", "%sconfig.xml" % context.config("output_prefix")
    ])

    # Cleanup
    os.remove("%s/prepared_population.xml.gz" % context.path())

    return "%sconfig.xml" % context.config("output_prefix")
