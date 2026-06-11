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

import shutil
import os.path

import matsim.runtime.eqasim as eqasim
import matsim.simulation.prepare as delegate

def configure(context):
    delegate.configure(context)
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

def execute(context):
    result = delegate.execute(context)

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

    return result


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
