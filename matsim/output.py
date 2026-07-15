import shutil
import os
import os.path


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


def configure(context):
    if context.config("run_matsim", True):
        # allow disabling performing one run of the simulation
        context.stage("matsim.simulation.run")
    
    context.stage("matsim.simulation.prepare")
    context.stage("matsim.runtime.eqasim")

    context.config("output_path")
    context.config("output_prefix", "ile_de_france_")
    context.config("write_jar", True)
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
