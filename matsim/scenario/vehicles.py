import io, gzip

import numpy as np
import pandas as pd

import matsim.writers as writers

def configure(context):
    context.stage("synthesis.vehicles.vehicles")

TYPE_FIELDS = ["type_id", "nb_seats", "length", "width", "pce", "mode"]
VEHICLE_FIELDS = ["vehicle_id", "type_id", "critair", "technology", "age", "euro"]

# Legacy per-vehicle attributes always written when present (eqasim default fleet).
LEGACY_VEHICLE_ATTRIBUTES = ["critair", "technology", "age", "euro"]

# Additional per-vehicle attributes written by the differentiated German fleet
# (Task F6 / vehicles_method:"household"). They are written ONLY when the column
# is present in the vehicles frame AND the per-vehicle value is non-empty, so the
# legacy default fleet (which has none of these columns) stays byte-identical
# (OFF-equivalence). The order is fixed for a reproducible, stable XML.
GERMAN_VEHICLE_ATTRIBUTES = ["segment", "powertrain", "euro_class", "brand", "model"]


def execute(context):
    output_path = "%s/vehicles.xml.gz" % context.path()
    df_vehicle_types, df_vehicles = context.stage("synthesis.vehicles.vehicles")
    return write_vehicles(output_path, df_vehicle_types, df_vehicles, context)


def _vehicle_attributes(vehicle, present_german_columns):
    """Per-vehicle MATSim object attributes for one vehicle record.

    The four legacy attributes (``critair``/``technology``/``age``/``euro``) are
    written exactly as before whenever they are present in the record. The German
    fleet additionally annotates each vehicle with its KBA spec
    (``segment``/``powertrain``/``euro_class``/``brand``/``model``); each is
    written ONLY if its column is present in the frame and the value for this
    vehicle is non-empty. The legacy default fleet has none of the German columns,
    so this returns exactly the legacy four attributes for it -- the written XML
    is byte-identical to the upstream eqasim writer (OFF-equivalence).
    """
    attributes = {}
    for key in LEGACY_VEHICLE_ATTRIBUTES:
        if key in vehicle:
            attributes[key] = vehicle[key]
    for key in present_german_columns:
        value = vehicle.get(key)
        # Skip missing / empty values so an in-commuter or partial frame without
        # a brand/model does not emit empty attributes.
        if value is None:
            continue
        if isinstance(value, float) and np.isnan(value):
            continue
        if isinstance(value, str) and value == "":
            continue
        attributes[key] = value
    return attributes


def write_vehicles(output_path, df_vehicle_types, df_vehicles, context):
    """Write the vehicles XML. Factored out so a regional override can append
    injected in-commuter vehicles before writing."""
    with gzip.open(output_path, 'wb+') as writer:
        with io.BufferedWriter(writer, buffer_size = 2 * 1024**3) as writer:
            writer = writers.VehiclesWriter(writer)
            writer.start_vehicles()

            with context.progress(total = len(df_vehicle_types), label = "Writing vehicles types ...") as progress:
                for type in df_vehicle_types.to_dict(orient="records"):
                    writer.add_type(
                        type["type_id"],
                        length=type["length"],
                        width=type["width"],
                        engine_attributes = {
                            "HbefaVehicleCategory": type["hbefa_cat"],
                            "HbefaTechnology": type["hbefa_tech"],
                            "HbefaSizeClass": type["hbefa_size"],
                            "HbefaEmissionsConcept": type["hbefa_emission"]
                        }
                    )
                    progress.update()

            # German fleet (Task F6): the household method adds the per-vehicle KBA
            # spec columns. Resolve which are present ONCE (cheap) so the per-vehicle
            # loop only checks the columns that actually exist in this frame; the
            # legacy default fleet has none, so this list is empty and the written
            # attributes are exactly the legacy four (byte-identical OFF path).
            present_german_columns = [
                key for key in GERMAN_VEHICLE_ATTRIBUTES if key in df_vehicles.columns]

            with context.progress(total = len(df_vehicles), label = "Writing vehicles ...") as progress:
                for vehicle in df_vehicles.to_dict(orient="records"):

                    writer.add_vehicle(
                        vehicle["vehicle_id"],
                        vehicle["type_id"],
                        attributes = _vehicle_attributes(vehicle, present_german_columns)
                    )
                    progress.update()

            writer.end_vehicles()

    return "vehicles.xml.gz"