"""Student in-commuter count anchor (#140 sub-item 2).

Per ZGB university commune, the number of student in-commuters is the real
enrollment NOT filled by resident placement:

    in_commuters_c = max(0, round(enrollment_c * sampling_rate) - residents_c)

``enrollment_c`` = summed ``capacity`` of that commune's local university
facilities (the LSN enrollment already distributed across OSM buildings by
``braunschweig.data.schools.university_facilities``). ``residents_c`` = resident
university students the education-gravity model assigned to a local facility in
commune c. This is arithmetic on two committed quantities, NOT an invented
reference (see the design spec, section 3.1).
"""
from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

_log = logging.getLogger(__name__)

# Local facilities carry ids prefixed "uni_loc_"; surrounding campuses "uni_sur_".
_LOCAL_PREFIX = "uni_loc_"


def facility_communes(university_facilities, municipalities):
    """Map each LOCAL university facility to its ZGB commune (5-digit ARS).

    Spatial join of the local (``uni_loc_*``) facility points to the municipality
    polygons. Surrounding campuses (``uni_sur_*``) and any facility outside every
    ZGB commune are dropped (they carry no ZGB enrollment count).
    Returns DataFrame ``[location_id, commune_ars5]``."""
    fac = university_facilities[
        university_facilities["location_id"].astype(str).str.startswith(_LOCAL_PREFIX)
    ].copy()
    muni = municipalities.copy()
    muni["commune_ars5"] = muni["commune_id"].astype(str).str[:5]
    joined = gpd.sjoin(
        fac[["location_id", "geometry"]], muni[["commune_ars5", "geometry"]],
        how="inner", predicate="within")
    return joined[["location_id", "commune_ars5"]].reset_index(drop=True)


def compute_incommuter_counts(university_facilities, municipalities,
                              resident_placement, sampling_rate):
    """Per-commune student in-commuter counts. See module docstring.

    Returns DataFrame ``[commune_ars5, enrollment_scaled, residents, in_commuters]``
    with one row per ZGB university commune (only communes that host local
    facilities). Logs enrollment/residents/in_commuters per commune; warns when a
    raw count is negative (residents exceed scaled enrollment)."""
    fac_comm = facility_communes(university_facilities, municipalities)
    cap = university_facilities[["location_id", "capacity"]].merge(
        fac_comm, on="location_id", how="inner")
    enrollment = cap.groupby("commune_ars5")["capacity"].sum()

    loc_to_comm = dict(zip(fac_comm["location_id"], fac_comm["commune_ars5"]))
    res = resident_placement.copy()
    res["commune_ars5"] = res["location_id"].map(loc_to_comm)
    residents = res.dropna(subset=["commune_ars5"]).groupby("commune_ars5").size()

    rows = []
    for comm in enrollment.index:
        enr_scaled = int(round(float(enrollment.loc[comm]) * float(sampling_rate)))
        res_c = int(residents.get(comm, 0))
        raw = enr_scaled - res_c
        if raw < 0:
            _log.warning(
                "[student_incommuter_counts] commune %s: residents %d exceed scaled "
                "enrollment %d -> in_commuters floored to 0 (check university slope)",
                comm, res_c, enr_scaled)
        in_c = max(0, raw)
        _log.info(
            "[student_incommuter_counts] commune %s: enrollment(scaled) %d, "
            "residents %d, in_commuters %d", comm, enr_scaled, res_c, in_c)
        rows.append({"commune_ars5": comm, "enrollment_scaled": enr_scaled,
                     "residents": res_c, "in_commuters": in_c})
    return pd.DataFrame(rows)
