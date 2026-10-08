"""Portal stays as fixed anchors of the chain solver (eqasim-bs#442).

The portal stage already writes ``outside`` on both sides of each stay in the trip table, so
the vendored problem splitter (``problems.FIXED_PURPOSES``) splits the chain there; it looks the
gate coordinate up in the ``activity_anchors`` mapping exactly as it does for escort and
passive-joint anchors. The gate rows join the locations output so the eqasim location join
finds a geometry for every ``outside`` activity.

Location rows carry ``location_id = -1``: an ``outside`` activity is coordinate-only, like the
in-commuter gate home (``braunschweig.data.cordon.plans.build_incommuter_locations``); no
facility exists for a gate, and the MATSim population writer uses ``location_id`` as the
facility id. The gate id of each stay stays traceable in the anchors frame of
``braunschweig.synthesis.portal_trips.anchors`` (``person_id, activity_index, gate_id``).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LOCATION_COLUMNS = ["person_id", "activity_index", "location_id", "geometry"]

#: Placeholder facility id of a coordinate-only activity (eqasim home placeholder).
COORDINATE_ONLY_LOCATION_ID = -1


def anchors_dict(frame) -> dict:
    """``{(person_id, activity_index): Point}`` as consumed by ``find_assignment_problems``."""
    return {(int(row.person_id), int(row.activity_index)): row.geometry for row in frame.itertuples(index=False)}


def location_rows(frame) -> pd.DataFrame:
    """Locations-output rows of the gates; ``location_id`` is object dtype like the resident rows."""
    if len(frame) == 0:
        return pd.DataFrame(columns=LOCATION_COLUMNS)
    location_ids = np.empty(len(frame), dtype=object)
    location_ids[:] = COORDINATE_ONLY_LOCATION_ID
    return pd.DataFrame({"person_id": frame["person_id"].to_numpy(),
                         "activity_index": frame["activity_index"].to_numpy(),
                         "location_id": location_ids,
                         "geometry": frame["geometry"].to_numpy()},
                        columns=LOCATION_COLUMNS)


def merge_anchors(existing, portal: dict):
    """The escort anchors dict extended by the portal anchors; ``existing`` is returned when nothing is added."""
    if not portal:
        return existing
    merged = dict(existing or {})
    merged.update(portal)
    return merged
