"""MATSim simulation preparation stage.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/matsim/simulation/prepare.py``.
Moved to ``braunschweig.matsim.simulation.prepare`` in Phase 2.12 of the
eqasim-bs refactor.

The transit-zone source has been switched from MVG (Munich) to VRB
(Verkehrsverbund Region Braunschweig). ``braunschweig.data.vrb.zones``
mirrors the MVG stage's output schema (``GeoDataFrame[zone, geometry]``
in EPSG:25832, 400 m buffered MultiPoint per zone) so the downstream
Java consumer sees an identical input.

The ``org.eqasim.bavaria.scenario.AddTransitZoneInformation`` Java class
reference is intentionally retained per Decision D-1c: renaming the Java
package to ``org.eqasim.braunschweig.*`` is out of scope for the Python
refactor. The cached eqasim-java checkout still publishes the bavaria
namespace; the class itself is region-neutral (point-in-polygon zone
attribution against the supplied shapefile).
"""

import gzip
import hashlib
import importlib
import inspect
import json
import shutil
import os.path
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import matsim.runtime.eqasim as eqasim
import matsim.simulation.prepare as delegate
from braunschweig.data.vrb import fare_config_xml, fare_model_export, line_scopes
from braunschweig.matsim import config_modules
from braunschweig.parking import tariff_export

#: ``delegate`` performs the preparation itself; ``eqasim`` owns ``run()``, i.e. HOW the Java
#: classes below (AddTransitZoneInformation, RunScenarioCutter) are invoked and with which
#: arguments, so a change there changes the prepared scenario without touching this file
#: (#327 gate). The three ``braunschweig.data.vrb`` helpers write the VRB zone fare inputs
#: (ADR-0133), ``tariff_export`` and ``config_modules`` the parking inputs (issue #249); all are
#: hashed unconditionally, like the deferred cordon helpers below.
_HELPER_MODULES = (delegate, eqasim, fare_config_xml, fare_model_export, line_scopes, tariff_export, config_modules)

#: VRB zone fare model (ADR-0133). Flag-gated; absent/false keeps the legacy ring cost model.
VRB_FARES_KEY = "vrb_zone_fares_enabled"
#: Assumption parameters of the fare model, declared only when the flag is on (ADR-0133 D7/D8).
VRB_FARE_DEFAULTS = {
    "vrb_fare_snapshot_date": "2026-06-20",
    "vrb_fare_day_ticket_cap_enabled": True,
    "vrb_fare_unsupported_fallback_cents": 370,        # ASSUMPTION: price of a counted fallback outcome
    "vrb_fare_maximum_unsupported_share": 0.05,        # ASSUMPTION: diagnostic threshold, not an accuracy target
    "vrb_fare_external_local_single_cents": 370,       # ASSUMPTION: GVH one-zone single 2026 for every external operator
    "vrb_fare_rail_distance_factor": 1.0,              # ASSUMPTION: ridden stop distance -> tariff distance
    "vrb_fare_long_distance_single_cents": 2190,       # ASSUMPTION: DB Sparpreis entry price 2026, every ticket
    # The router adds that price to long-distance rides, converted with the mode choice's value of time (D6).
    "vrb_fare_long_distance_routing_surcharge_enabled": True,
    # ASSUMPTION: GTFS route ids become the schedule line ids (pt2matsim), so nearly every line must have a
    # scope row; a lower share means the ids diverged, not that a few lines are legitimately unknown.
    "vrb_fare_minimum_line_scope_coverage": 0.99,
    # ASSUMPTION: floor on the share of final-schedule stop facilities carrying a VRB zone. A broken attribution
    # (CRS mismatch, misread zone field) gives almost 0; the timetable legitimately reaches far beyond the VRB,
    # so this is a failure detector, not a coverage target. Revisit against the smoke's reported share.
    "vrb_fare_minimum_facility_zone_coverage": 0.10,
}
VRB_ZONE_TOOL = "org.eqasim.braunschweig.scenario.AddVrbTariffZoneInformation"

#: Zone-based parking costs (issue #249, design spec 3.6). Flag-gated; absent/false writes no parking input and
#: leaves the prepared config as it is.
PARKING_KEY = "parking_zones_enabled"
#: Parameters of the tariff export, declared only when the flag is on (spec 5.5).
PARKING_DEFAULTS = {
    # Tariff state the committed tariff table records; it names the tariff model file.
    "parking_tariff_snapshot_date": "2026-09-28",
    # ASSUMPTION T1: a terminal stay pays until the fee window of the arrival day ends (the only implemented rule).
    "parking_terminal_stay_rule": "until_fee_end",
}
#: MATSim config module read by the Java ParkingConfigGroup (eqasim-java-bs, org.eqasim.braunschweig.parking).
PARKING_MODULE = "braunschweigParking"

#: Modules this stage runs without importing them itself, hashed by dotted NAME because they are
#: not module objects here. The two cordon helpers are FUNCTION-LEVEL imports inside
#: :func:`_cut_to_cordon`: ``spatial.cordon`` decides the cordon POLYGON and the buffer width and
#: ``cordon.extent`` writes the extent file the cutter is driven by, so both shape the cut
#: scenario. Under the default (cross-cordon off) they are never imported at run time, but a
#: token must not depend on a flag: hashing them unconditionally is what makes the cached
#: scenario of a cordon RUN trustworthy, and it costs nothing when the flag is off.
_DEFERRED_HELPER_MODULE_NAMES = (
    # ``tariff_export`` turns every tariff row into a ``cost.ZoneTariff`` and writes its fields as the zone
    # entry of the parking tariff model, so the dataclass decides the model's content and its validation.
    "braunschweig.parking.cost",
    "braunschweig.data.cordon.extent",
    "braunschweig.data.spatial.cordon",
)


def validate(context):
    """Invalidate this wrapper when its delegated preparation helper changes.

    A deferred module that fails to import raises rather than being skipped: skipping it
    would keep a stale prepared scenario alive exactly when the helper is broken.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    # The committed tariff tables are read at execute time by fare_model_export: a corrected price, matrix or
    # rail table must invalidate the prepared scenario. Hashed unconditionally (a token must not depend on a flag).
    # The parking inputs are not read here: they arrive through the declared braunschweig.parking.zones_stage,
    # whose own token hashes them and whose change synpp propagates to this stage.
    for path in fare_model_export.committed_input_paths():
        digest.update(fare_model_export.content_sha256(path).encode("ascii"))
    for module_name in _DEFERRED_HELPER_MODULE_NAMES:
        try:
            deferred_module = importlib.import_module(module_name)
            deferred_source = inspect.getsource(deferred_module)
        except Exception as error:
            raise RuntimeError(
                f"matsim.simulation.prepare validate(): cannot hash the deferred helper "
                f"module {module_name!r} ({type(error).__name__}: {error}); it must not be "
                "skipped, because skipping it would silently reuse a stale prepared scenario."
            ) from error
        digest.update(deferred_source.encode("utf-8"))
    return digest.hexdigest()

def configure(context):
    delegate.configure(context)
    # VRB zone fare model (ADR-0133): official zone polygons instead of the legacy concentric rings.
    context.config(VRB_FARES_KEY, False)
    if context.config(VRB_FARES_KEY):
        context.stage("braunschweig.data.vrb.zone_polygons")
        context.stage("data.gtfs.cleaned")
        for key, default in VRB_FARE_DEFAULTS.items():
            context.config(key, default)
    else:
        context.stage("braunschweig.data.vrb.zones")

    # Cross-cordon feature (flag-gated, default off). When enabled, the prepared
    # full scenario is cut to the cordon (dissolved municipalities + a fractional
    # buffer) with eqasim's native RunScenarioCutter, turning every boundary-
    # crossing trip into an "outside" activity (DMC then keeps it fixed) and
    # cutting the (enlarged) network at the cordon so motorways become real
    # boundary links/gates. Because both matsim.simulation.run and matsim.output
    # consume this stage, the cut output flows downstream transparently -- no
    # changes to run.py / output.py are needed.
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.config("cordon_network_buffer_fraction", 0.10)
        context.stage("data.spatial.municipalities")

    # Long-haul freight injection (german-wide-freight v3, Lu et al. 2022).
    # Default ON; requires the local-only v3 inputs (the data stage fails early
    # with the download command when they are missing). The trips stage (and the
    # extraction it depends on) is sampling-rate independent (cached); sampling
    # happens here in the hook and the Java injector just writes the CSV.
    context.config("freight_enabled", True)
    if context.config("freight_enabled"):
        context.stage("braunschweig.freight.trips")
        context.config("freight_sampling_rate", None)
        # ASSUMPTION: PCE 3.5 (common MATSim freight practice, e.g. the
        # matsim-duesseldorf freight vehicle type); max speed 80 km/h is the
        # statutory German HGV limit (StVO 3). Both configurable, not calibrated.
        context.config("freight_truck_pce", 3.5)
        context.config("freight_truck_max_velocity_kmh", 80.0)
        context.config("random_seed")

    # Zone-based parking costs (issue #249): the tariff model JSON and the braunschweigParking module the Java
    # car cost model reads. OFF declares nothing else and writes nothing.
    context.config(PARKING_KEY, False)
    if context.config(PARKING_KEY):
        context.stage("braunschweig.parking.zones_stage")
        for key, default in PARKING_DEFAULTS.items():
            context.config(key, default)
        _check_parking_parameters(context.config("parking_tariff_snapshot_date"),
                                  context.config("parking_terminal_stay_rule"))


def _check_parking_parameters(snapshot_date, terminal_stay_rule):
    """Reject a parking parameter the tariff export cannot use, at configure time.

    The export itself runs at the very end of the preparation, i.e. after the whole synthesis; a typo or an
    unquoted YAML date (which arrives as ``datetime.date``, not as text) must not surface only then.
    """
    if terminal_stay_rule not in tariff_export.SUPPORTED_TERMINAL_STAY_RULES:
        raise ValueError(f"parking_terminal_stay_rule {terminal_stay_rule!r} is not implemented; supported: "
                         f"{list(tariff_export.SUPPORTED_TERMINAL_STAY_RULES)}")
    try:
        # The model file name embeds the snapshot date and accepts only ISO date text.
        tariff_export.tariff_model_file_name("", snapshot_date)
    except ValueError as error:
        raise ValueError(f"parking_tariff_snapshot_date: {error}; write it as quoted YAML text, e.g. "
                         f"{PARKING_DEFAULTS['parking_tariff_snapshot_date']!r}") from error


def execute(context):
    result = delegate.execute(context)

    if context.config(VRB_FARES_KEY):
        _attach_vrb_tariff_zones(context)
    else:
        df_zones = context.stage("braunschweig.data.vrb.zones")
        df_zones.to_file("{}/transit_zones.shp".format(context.path()))

        eqasim.run(context, "org.eqasim.braunschweig.scenario.AddTransitZoneInformation", [
            "--input-path", "{}transit_schedule.xml.gz".format(context.config("output_prefix")),
            "--output-path", "{}transit_schedule.xml.gz".format(context.config("output_prefix")),
            "--zones-path", "transit_zones.shp"
        ])

    # Cut the full scenario to the cordon (after the transit zones are attached, so
    # the cut transit schedule keeps them). Must be the last step: it rewrites the
    # prefixed scenario files in place with their cut versions.
    if context.config("cordon_enabled"):
        result = _cut_to_cordon(context)

    # Inject sampled freight agents LAST: after the cordon cut, so the eqasim
    # cutter never rewrites freight agents (they have no households/facilities and
    # their boundary trimming was already done by the extraction).
    if context.config("freight_enabled"):
        _inject_freight(context, result)

    # The fare inputs refer to the FINAL schedule and config, so they are written after the cut
    # and the freight injection.
    if context.config(VRB_FARES_KEY):
        _write_vrb_fare_inputs(context, result)

    # The parking module goes into the FINAL config too, and the tariff model next to it: written last.
    if context.config(PARKING_KEY):
        _write_parking_inputs(context, result)

    return result


def _attach_vrb_tariff_zones(context):
    """Write the official zones as a shapefile and attach vrbTariffZone to every stop facility."""
    zones = context.stage("braunschweig.data.vrb.zone_polygons")
    zones[["zone_id", "geometry"]].to_file("{}/vrb_tariff_zones.shp".format(context.path()))
    schedule = "{}transit_schedule.xml.gz".format(context.config("output_prefix"))
    eqasim.run(context, VRB_ZONE_TOOL, [
        "--input-path", schedule, "--output-path", schedule,
        "--zones-path", "vrb_tariff_zones.shp", "--report-path", "vrb_tariff_zone_report.json",
    ])


def _schedule_summary(schedule_path) -> dict:
    """Line ids and the VRB zone counts of the stop facilities of a MATSim schedule, in one streaming pass.

    Each finished stopFacility / transitLine element is cleared after reading, so a large schedule is not
    kept in memory as a whole tree.
    """
    line_ids, facilities, zoned_by_zone = set(), 0, Counter()
    with gzip.open(schedule_path, "rb") as stream:
        for _, element in ET.iterparse(stream):
            if element.tag == "stopFacility":
                facilities += 1
                zone = next((attribute.text for attribute in element.iter("attribute")
                             if attribute.get("name") == "vrbTariffZone"), None)
                if zone is not None and zone.strip():
                    zoned_by_zone[zone.strip()] += 1
                element.clear()
            elif element.tag == "transitLine":
                line_ids.add(element.get("id"))
                element.clear()
    return {"line_ids": line_ids, "facilities": facilities, "zoned_facilities": sum(zoned_by_zone.values()),
            "zoned_by_zone": dict(sorted(zoned_by_zone.items()))}


def _write_vrb_fare_inputs(context, config_name):
    """Line scopes, fare model, vrbFare config module and a coverage report for the enabled fare model.

    Coverage is counted on the FINAL schedule (after the cordon cut and freight injection), which is the one
    the run prices with; the zone tool's own counts are kept as ``pre_cut_zone_tool``. Two guards fail the
    stage: line scope coverage below ``vrb_fare_minimum_line_scope_coverage`` (the GTFS route ids and the
    schedule line ids diverged) and stop facility zone coverage below ``vrb_fare_minimum_facility_zone_coverage``
    (the zone attribution broke, which would silently turn every trip into external pricing).
    """
    root = Path(context.path())
    model_name, scopes_name, report_name = fare_config_xml.fare_input_file_names(
        context.config("output_prefix"), context.config("vrb_fare_snapshot_date"))
    scopes = line_scopes.build_line_scopes(Path(context.path("data.gtfs.cleaned")) / "output")
    line_scopes.write_line_scopes(scopes, root / scopes_name)
    model = fare_model_export.build_fare_model(
        snapshot_date=context.config("vrb_fare_snapshot_date"),
        assumptions={"external_local_single_cents": int(context.config("vrb_fare_external_local_single_cents")),
                     "rail_distance_factor": float(context.config("vrb_fare_rail_distance_factor")),
                     "long_distance_single_cents": int(context.config("vrb_fare_long_distance_single_cents")),
                     "unsupported_fallback_cents": int(context.config("vrb_fare_unsupported_fallback_cents"))})
    fare_model_export.write_fare_model(root / model_name, model)
    fare_config_xml.write_vrb_fare_module(root / config_name, {
        "enabled": "true", "fareModelPath": model_name, "lineScopesPath": scopes_name,
        "dayTicketCapEnabled": str(bool(context.config("vrb_fare_day_ticket_cap_enabled"))).lower(),
        "longDistanceRoutingSurchargeEnabled":
            str(bool(context.config("vrb_fare_long_distance_routing_surcharge_enabled"))).lower(),
        "maximumUnsupportedShare": repr(float(context.config("vrb_fare_maximum_unsupported_share"))),
    })
    zone_tool = json.loads((root / "vrb_tariff_zone_report.json").read_text(encoding="utf-8"))
    summary = _schedule_summary(root / "{}transit_schedule.xml.gz".format(context.config("output_prefix")))
    line_ids = summary["line_ids"]
    covered = line_ids & set(scopes["line_id"])
    line_coverage = len(covered) / max(len(line_ids), 1)
    facility_coverage = summary["zoned_facilities"] / max(summary["facilities"], 1)
    report = {"fare_input_files": [model_name, scopes_name, report_name],
              "facility_zone_coverage": facility_coverage, "facilities": summary["facilities"],
              "zoned_facilities": summary["zoned_facilities"], "zoned_facilities_by_zone": summary["zoned_by_zone"],
              "pre_cut_zone_tool": zone_tool, "schedule_lines": len(line_ids), "lines_with_scope": len(covered),
              "line_scope_coverage": line_coverage, "lines_without_scope": sorted(line_ids - covered)[:50]}
    (root / report_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("[vrb-fares] prepared inputs (final schedule): facilities zoned %d/%d (%.2f%%), lines with scope %d/%d "
          "(%.2f%%), boundary ties %d (zone tool)" % (
              summary["zoned_facilities"], summary["facilities"], 100 * facility_coverage, len(covered),
              len(line_ids), 100 * line_coverage, zone_tool["boundary_ties"]))
    minimum = float(context.config("vrb_fare_minimum_line_scope_coverage"))
    if line_coverage < minimum:
        raise RuntimeError("[vrb-fares] only %.2f%% of schedule lines have a tariff scope row (minimum %.2f%%); "
                           "first missing ids: %s" % (100 * line_coverage, 100 * minimum,
                                                      report["lines_without_scope"][:10]))
    minimum = float(context.config("vrb_fare_minimum_facility_zone_coverage"))
    if facility_coverage < minimum:
        raise RuntimeError("[vrb-fares] only %.2f%% of the final schedule's stop facilities carry a VRB zone "
                           "(minimum %.2f%%); check the CRS of the schedule and the zone polygons and the zone "
                           "field of the polygon layer" % (100 * facility_coverage, 100 * minimum))


def _write_parking_inputs(context, config_name):
    """Tariff model, braunschweigParking config module and an inputs report for the zone-based parking costs.

    The tariff table of ``braunschweig.parking.zones_stage`` (spec 5.3: euros, decimal hours) becomes the tariff
    model JSON (spec 5.4: integer cents and seconds) next to ``config_name``. The module names that file without
    a directory, so the Java side resolves it relative to the config wherever the scenario is moved, and the
    report lists every file ``matsim.output`` must copy with the scenario: the model and the report itself.
    An identical module already in the config is kept, a conflicting one raises (``config_modules.write_module``);
    the report is written last, so it exists only when the files it lists do.
    """
    root = Path(context.path())
    prefix = context.config("output_prefix")
    snapshot_date = context.config("parking_tariff_snapshot_date")
    terminal_stay_rule = context.config("parking_terminal_stay_rule")
    release = context.stage("braunschweig.parking.zones_stage")
    model = tariff_export.build_tariff_model(release["tariffs"], snapshot_date=snapshot_date,
                                             sources=release["sources"], terminal_stay_rule=terminal_stay_rule)
    tariffs_name = tariff_export.tariff_model_file_name(prefix, snapshot_date)
    report_name = tariff_export.inputs_report_name(prefix)
    tariff_export.write_tariff_model(root / tariffs_name, model)
    config_modules.write_module(root / config_name, PARKING_MODULE, {
        "enabled": "true", "tariffsPath": tariffs_name, "terminalStayRule": terminal_stay_rule})
    zone_types = dict(sorted(Counter(zone["zone_type"] for zone in model["zones"].values()).items()))
    report = {"parking_input_files": [tariffs_name, report_name], "zones": len(model["zones"]),
              "zone_types": zone_types, "terminal_stay_rule": terminal_stay_rule, "sources": model["sources"]}
    tariff_export.write_json_document(root / report_name, report)
    print("[parking] prepared inputs: %d zones (%s), terminal stay rule %s; tariff model %s, module %s in %s, "
          "report %s" % (len(model["zones"]), ", ".join("%s %d" % item for item in zone_types.items()),
                         terminal_stay_rule, tariffs_name, PARKING_MODULE, config_name, report_name))


def _cut_to_cordon(context):
    """Cut the prepared scenario to the cordon extent with RunScenarioCutter.

    Builds the cordon polygon (dissolved in-scope municipalities + a fractional
    buffer) as a single-polygon GeoPackage, then runs eqasim's native cutter on the
    prepared config. The cutter writes the cut ``<prefix>*`` scenario files into the
    stage directory (overwriting the uncut inputs -- safe, as the cutter loads the
    whole scenario before writing) and we return the cut config filename so all
    downstream consumers see the cut scenario.
    """
    from braunschweig.data.spatial.cordon import build_cordon_polygon, buffer_m_from_fraction
    from braunschweig.data.cordon.extent import write_cordon_extent

    prefix = context.config("output_prefix")
    threads = context.config("matsim_threads") or context.config("processes")

    df_muni = context.stage("data.spatial.municipalities")
    fraction = float(context.config("cordon_network_buffer_fraction"))
    buffer_m = buffer_m_from_fraction(df_muni, fraction)
    cordon = build_cordon_polygon(df_muni, buffer_m)
    write_cordon_extent("%s/cordon_extent.gpkg" % context.path(), cordon, crs=str(df_muni.crs))

    # The cutter loads the scenario relative to the config file's directory (the
    # stage path, the java cwd), so config-path / extent-path are given relative.
    # No --eqasim-configurator is passed: like every other prepare/run java call,
    # the cutter falls back to the bavaria jar's default configurator (the cutter's
    # option is "eqasim-configurator", not "...-class", and is unnecessary here).
    eqasim.run(context, "org.eqasim.core.scenario.cutter.RunScenarioCutter", [
        "--config-path", "%sconfig.xml" % prefix,
        "--output-path", context.path(),
        "--extent-path", "cordon_extent.gpkg",
        "--threads", threads,
        "--prefix", prefix,
    ])

    cut_config = "%sconfig.xml" % prefix
    assert os.path.exists("%s/%s" % (context.path(), cut_config)), "cutter did not write the cut config"
    return cut_config


def _inject_freight(context, config_name):
    """Sample the ZGB-relevant freight trips and inject them into the scenario.

    The trips stage returns the full ZGB-relevant trips DataFrame. Here we
    Bernoulli-sample it at the pipeline sampling rate (seeded -- required because
    the generated qsim flowCapacityFactor scales with the global sampling rate),
    write a flat ``freight_trips_sampled.csv``, and delegate the MATSim scenario
    surgery to org.eqasim.braunschweig.scenario.RunInjectFreight (rewrites
    population/vehicles/network/config in place). Per-category total vs sampled
    counts are logged (no-silent-fallback).
    """
    import numpy as np

    rate = context.config("freight_sampling_rate")
    if rate is None:
        rate = context.config("sampling_rate")
    rate = float(rate)

    df = context.stage("braunschweig.freight.trips")

    # Deterministic Bernoulli sample. Offset the seed so freight sampling is
    # independent of any other seeded draw in the pipeline.
    seed = int(context.config("random_seed")) + 81247
    rng = np.random.default_rng(seed)
    mask = rng.random(len(df)) < rate
    sampled = df.loc[mask]

    # Observability: total vs sampled per trip type (no silent thinning).
    totals = df["trip_type"].value_counts().to_dict()
    taken = sampled["trip_type"].value_counts().to_dict()
    for trip_type in sorted(totals):
        print("[freight.injection] trip type %s: total %d, sampled %d"
              % (trip_type, totals[trip_type], taken.get(trip_type, 0)))
    print("[freight.injection] sampled %d / %d trips at rate %s"
          % (len(sampled), len(df), rate))

    # Column order is the cross-language contract: the Java injector validates
    # the header verbatim against its EXPECTED_HEADER (RunInjectFreight.java).
    # TRIP_COLUMNS is the single source of truth, pinned by a regression test.
    from braunschweig.freight.trips import TRIP_COLUMNS

    csv_name = "freight_trips_sampled.csv"
    sampled[list(TRIP_COLUMNS)].to_csv("%s/%s" % (context.path(), csv_name), sep=";", index=False)

    summary_name = "freight_injection_summary.csv"
    eqasim.run(context, "org.eqasim.braunschweig.scenario.RunInjectFreight", [
        "--config-path", config_name,
        "--freight-csv-path", csv_name,
        "--truck-pce", context.config("freight_truck_pce"),
        "--truck-max-velocity-kmh", context.config("freight_truck_max_velocity_kmh"),
        "--summary-path", summary_name,
    ])

    summary_path = "%s/%s" % (context.path(), summary_name)
    assert os.path.exists(summary_path), "freight injection did not write its summary"
    with open(summary_path, encoding="utf-8") as f:
        for line in f.read().strip().splitlines():
            print("[freight.injection] %s" % line)
