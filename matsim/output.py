import shutil
import os
import os.path
import json
import datetime


def mirror_directory_tree(source_dir, target_dir):
    """Recursively mirror source_dir into target_dir.

    Each file is hardlinked (os.link) where the filesystem allows it and
    copied (shutil.copy2) as a fallback (e.g. cross-volume, OSError/EXDEV),
    so the archive costs zero extra disk on the same volume. An existing
    target_dir is removed first, so the archive always reflects the latest
    run.

    :param source_dir: directory to mirror (e.g. .../simulation_output)
    :param target_dir: destination directory (e.g. <output_path>/matsim_output)
    :returns: (hardlink_count, copy_count, file_count)
    """
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)

    hardlink_count = 0
    copy_count = 0
    file_count = 0

    for current_dir, _sub_dirs, file_names in os.walk(source_dir):
        relative_dir = os.path.relpath(current_dir, source_dir)
        destination_dir = os.path.join(target_dir, relative_dir)
        os.makedirs(destination_dir, exist_ok=True)

        for file_name in file_names:
            source_file = os.path.join(current_dir, file_name)
            destination_file = os.path.join(destination_dir, file_name)
            file_count += 1
            try:
                os.link(source_file, destination_file)
                hardlink_count += 1
            except OSError:
                shutil.copy2(source_file, destination_file)
                copy_count += 1

    return hardlink_count, copy_count, file_count


def archive_simulation_output(run_path, output_path):
    """Mirror <run_path>/simulation_output into <output_path>/matsim_output.

    Logs the hardlink-vs-copy rate (fallback transparency, CLAUDE.md), writes an
    ARCHIVE_INFO.json provenance file next to the archive, and raises
    RuntimeError if the run stage produced no simulation output (e.g. a stale or
    wiped hash cache -- the exact failure issue #156 guards against).

    :param run_path: matsim.simulation.run stage cache dir (contains simulation_output/)
    :param output_path: scenario-export output dir (archive goes to <output_path>/matsim_output)
    :returns: (hardlink_count, copy_count, file_count)
    """
    source_dir = "%s/simulation_output" % run_path
    target_dir = "%s/matsim_output" % output_path

    if os.path.exists(target_dir):
        print("[matsim.output] overwriting existing matsim_output archive at %s" % target_dir)

    hardlink_count, copy_count, file_count = mirror_directory_tree(source_dir, target_dir)

    if file_count == 0:
        # mirror_directory_tree may have created an empty target_dir (from an
        # existing-but-empty source) before finding no files; remove it so the
        # failure leaves no stray empty archive behind.
        shutil.rmtree(target_dir, ignore_errors=True)
        raise RuntimeError(
            "[matsim.output] no files found under %s -- the MATSim run stage "
            "produced no simulation_output (stale or wiped hash cache?); cannot "
            "archive. See issue #156." % source_dir)

    hardlink_rate = 100.0 * hardlink_count / file_count
    copy_rate = 100.0 * copy_count / file_count
    print("[matsim.output] archived %d files from %s to %s: hardlink %d (%.1f%%), copy %d (%.1f%%)" % (
        file_count, source_dir, target_dir, hardlink_count, hardlink_rate, copy_count, copy_rate))
    if hardlink_count == 0:
        # 100% copy means source and target sit on different volumes; the
        # zero-extra-disk property was lost -- surface it loudly.
        print("[matsim.output] WARNING! 0%% hardlinks -- source and target are on different volumes; archive used extra disk")

    # Provenance: record the opaque source hash dir next to the archive,
    # mirroring the documentation.meta_output *meta.json pattern.
    archive_info = dict(
        source_hash_dir=run_path,
        created=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        file_count=file_count,
        hardlink_count=hardlink_count,
        copy_count=copy_count,
    )
    with open("%s/ARCHIVE_INFO.json" % target_dir, "w") as f:
        json.dump(archive_info, f, indent=4)

    assert os.path.exists("%s/output_events.xml.gz" % target_dir), (
        "[matsim.output] archive at %s is missing output_events.xml.gz" % target_dir)

    return hardlink_count, copy_count, file_count


def configure(context):
    if context.config("run_matsim", True):
        # allow disabling performing one run of the simulation
        context.stage("matsim.simulation.run")
    
    context.stage("matsim.simulation.prepare")
    context.stage("matsim.runtime.eqasim")

    context.config("output_path")
    context.config("output_prefix", "ile_de_france_")
    context.config("write_jar", True)
    # Archive the MATSim simulation_output/ (events, plans, ITERS, config,
    # logfile) from the run stage's hash cache dir into a stable, run-named
    # <output_path>/matsim_output/. Hardlink where possible (zero extra disk),
    # copy as fallback. Default ON so every run leaves a durable, findable
    # artefact; a cache-dir wipe no longer destroys the only copy (issue #156).
    context.config("archive_matsim_output", True)
    need_osm = context.config("export_detailed_network", False)
    if need_osm:
        context.stage("matsim.scenario.supply.osm")
    

    context.stage("documentation.meta_output")

def execute(context):
    config_path = "%s/%s" % (
        context.path("matsim.simulation.prepare"),
        context.stage("matsim.simulation.prepare")
    )

    file_names = [
        "%shouseholds.xml.gz" % context.config("output_prefix"),
        "%spopulation.xml.gz" % context.config("output_prefix"),
        "%svehicles.xml.gz" % context.config("output_prefix"),
        "%sfacilities.xml.gz" % context.config("output_prefix"),
        "%snetwork.xml.gz" % context.config("output_prefix"),
        "%stransit_schedule.xml.gz" % context.config("output_prefix"),
        "%stransit_vehicles.xml.gz" % context.config("output_prefix"),
        "%sconfig.xml" % context.config("output_prefix")
    ]

    for name in file_names:
        shutil.copy(
            "%s/%s" % (context.path("matsim.simulation.prepare"), name),
            "%s/%s" % (context.config("output_path"), name)
        )

    if context.config("export_detailed_network"):
        shutil.copy(
            "%s/%s" % (context.path("matsim.scenario.supply.osm"), "detailed_network.csv"),
            "%s/%s" % (context.config("output_path"), "%sdetailed_network.csv" % context.config("output_prefix"))
        )
    
    if context.config("write_jar"):
        shutil.copy(
            "%s/%s" % (context.path("matsim.runtime.eqasim"), context.stage("matsim.runtime.eqasim")),
            "%s/%srun.jar" % (context.config("output_path"), context.config("output_prefix"))
        )

    # Mirror the MATSim simulation output into a stable, run-named location so
    # it survives a synpp hash-cache wipe (issue #156). Only when a run actually
    # happened (run_matsim) and the archive flag is on.
    if context.config("run_matsim") and context.config("archive_matsim_output"):
        archive_simulation_output(
            context.path("matsim.simulation.run"),
            context.config("output_path"),
        )
