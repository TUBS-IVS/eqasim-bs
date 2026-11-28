"""Student in-commuter count anchor (#140 sub-item 2).

Per ZGB university commune, the number of student in-commuters is the real
enrollment NOT filled by resident placement:

    in_commuters_c = max(0, round(enrollment_c * sampling_rate) - residents_c)

``enrollment_c`` = summed ``capacity`` of that commune's local university
facilities (the LSN enrollment already distributed across OSM buildings by
``braunschweig.data.schools.university_facilities``). ``residents_c`` = resident
university students the education-gravity model assigned to a local facility in
commune c. This is arithmetic on two committed quantities, NOT an invented
reference (see the design spec, section 3.1). This residual rule is the OFF path of
``braunschweig.students.flows_enabled``.

ON path (issue #140, Phase 4, design B6 and section 5a): the in-commuters are the
outside rows of the origin-destination table T (heads of every origin Kreis outside the
region at every site), turned into persons of the simulated day by the effective day
factor and the sampling rate and rounded with the margins kept
(:func:`balance_incommuter_counts`); each agent's commute length is the one T implies
(:func:`commute_length_km`), not the one of its home point.
"""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)

#: ``commute_length_km`` branch: a commune of the origin Kreis drawn by population x the weight curve of T.
COMMUTE_COMMUNE_CURVE = "commune_curve"
#: ``commute_length_km`` branch (counted fallback): the ``distance_km`` of the cell of T.
COMMUTE_OD_CELL = "od_cell"
_BALANCE_COLUMNS = ["orig_ars5", "destination_id", "n"]

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


def balance_incommuter_counts(outside_seed, f_eff, sampling_rate, rng):
    """In-commuter persons of the simulated day per origin Kreis and site (ON path, design B6).

    The real matrix ``f_eff x sampling_rate x seed`` (rows: the origin Kreise outside the region, columns: the
    sites of T) is rounded by ``university_assignment.controlled_round``: every cell to its floor or ceiling,
    unbiased, and every row (origin Kreis), every column (site) and the total within one person of its real value.

    Args:
        outside_seed: ``[home_ars5, destination_id, seed]``, the heads of T living outside the region
            (``student_flows_inputs.outside_seed``), one row per pair.
        f_eff: the effective day factor (``student_flows_inputs.effective_day_factor``), in (0, 1].
        sampling_rate: the run's sampling rate, in (0, 1].
        rng: the seeded ``numpy.random.Generator`` of the stage.

    Returns:
        ``[orig_ars5, destination_id, n]``, one row per pair of ``outside_seed`` (sorted by origin and site), ``n``
        an int64 count that can be 0.

    Raises:
        ValueError: on a missing column, a repeated pair, a negative or non-finite seed, or a factor or rate
            outside (0, 1].
    """
    from braunschweig.synthesis.locations.university_assignment import controlled_round

    missing = [column for column in ("home_ars5", "destination_id", "seed") if column not in outside_seed.columns]
    if missing:
        raise ValueError(f"[student_incommuter_counts] outside seed: missing column(s) {missing}")
    for name, value in (("f_eff", f_eff), ("sampling_rate", sampling_rate)):
        if not (np.isfinite(float(value)) and 0.0 < float(value) <= 1.0):
            raise ValueError(f"[student_incommuter_counts] {name} must lie in (0, 1], got {value!r}")
    seed = outside_seed[["home_ars5", "destination_id", "seed"]].copy()
    seed["home_ars5"] = seed["home_ars5"].astype(str)
    if seed.duplicated(["home_ars5", "destination_id"]).any():
        raise ValueError("[student_incommuter_counts] outside seed: a (home_ars5, destination_id) pair is repeated")
    values = pd.to_numeric(seed["seed"], errors="coerce")
    if not (np.isfinite(values) & (values >= 0)).all():
        raise ValueError("[student_incommuter_counts] outside seed: seed must be finite and >= 0")
    if seed.empty:
        return pd.DataFrame({"orig_ars5": pd.Series(dtype=object), "destination_id": pd.Series(dtype=object),
                             "n": pd.Series(dtype="int64")})
    scale = float(f_eff) * float(sampling_rate)
    real = (seed.assign(seed=values * scale)
            .pivot(index="home_ars5", columns="destination_id", values="seed")
            .sort_index(axis=0).sort_index(axis=1).fillna(0.0))
    rounded = controlled_round(real, rng)
    pairs = seed.sort_values(["home_ars5", "destination_id"], kind="mergesort")
    n = rounded.stack().reindex(pd.MultiIndex.from_frame(pairs[["home_ars5", "destination_id"]])).to_numpy()
    result = pd.DataFrame({"orig_ars5": pairs["home_ars5"].to_numpy(dtype=object),
                           "destination_id": pairs["destination_id"].to_numpy(dtype=object),
                           "n": np.rint(n).astype("int64")})
    _log.info("[student_incommuter_counts] balance: f_eff %.6f x sampling rate %g x %.3f heads of T = %.3f persons of "
              "the day, rounded to %d over %d origin Kreise and %d sites", float(f_eff), float(sampling_rate),
              float(values.sum()), float(real.to_numpy().sum()), int(result["n"].sum()), real.shape[0], real.shape[1])
    return result


def commute_length_km(orig_ars5, destination_point, gemeinden, weights, rng, *, od_distance_km, detour_factor=None):
    """The routed commute length (km) an in-commuter's cell of T implies, and the branch that gave it.

    Primary branch ``commune_curve``: a commune of the origin Kreis is drawn with probability proportional to
    ``population x (curve_basis(d, max_commute_km) @ (short, middle, long))``, where ``d`` is the straight line
    (km) from the commune to ``destination_point`` and the weights are T's own (``student_flows_inputs.od_weights``):
    the rule T was built with (``student_geometry.curve_basis``). The length is that straight line times
    ``detour_factor`` (default ``braunschweig.constants.ROUTED_DETOUR_FACTOR``).

    Fallback branch ``od_cell`` (counted by the caller): ``od_distance_km`` (the ``distance_km`` of T's cell, a
    population-weighted Kreis centroid to the site point) times the factor, when ``gemeinden`` is missing or empty or
    no commune of the Kreis has a positive weight.

    The commune layer: ``gemeinden`` is the frame the in-commuter stage loads for its home rule,
    ``braunschweig.data.external_workplaces._load_gemeinden`` -- VG250-EW ``vg250_gem``, land rows (``GF == 4``),
    ``ewz > 0``, keyed by ``ars5``. T was built from the same VG250-EW archive through
    ``student_geometry.load_gemeinden`` (land rows, ``EWZ``, commune centroid); this function also takes the
    commune centroid, so the commune points and populations match those of T's construction.

    Args:
        orig_ars5: the 5-digit origin Kreis.
        destination_point: ``(x, y)`` in EPSG:25832: the SITE POINT T was built with (``x``, ``y`` of
            ``schools/student_destination_points.csv``), not the agent's education building, so the commune weights
            and distances are those of T's construction.
        gemeinden: ``[ars5, ewz, geometry]`` (EPSG:25832) or None.
        weights: ``{short, middle, long, max_commute_km}``.
        rng: a seeded ``numpy.random.Generator`` (one draw per call on the primary branch, none on the fallback).
        od_distance_km: the cell's ``distance_km`` of T (straight line, km).

    Returns:
        ``(routed_km, branch)`` with ``branch`` :data:`COMMUTE_COMMUNE_CURVE` or :data:`COMMUTE_OD_CELL`.
    """
    from braunschweig.constants import ROUTED_DETOUR_FACTOR
    from braunschweig.data.education.student_geometry import curve_basis

    factor = ROUTED_DETOUR_FACTOR if detour_factor is None else float(detour_factor)
    if gemeinden is not None and len(gemeinden):
        own = gemeinden[gemeinden["ars5"].astype(str) == str(orig_ars5)]
        if len(own):
            centroids = own.geometry.centroid
            x = centroids.x.to_numpy(dtype=float)
            y = centroids.y.to_numpy(dtype=float)
            d_km = np.hypot(x - float(destination_point[0]), y - float(destination_point[1])) / 1000.0
            curve = curve_basis(d_km, float(weights["max_commute_km"])) @ np.array(
                [float(weights["short"]), float(weights["middle"]), float(weights["long"])])
            weight = own["ewz"].to_numpy(dtype=float) * curve
            weight = np.where(np.isfinite(weight) & (weight > 0), weight, 0.0)
            if weight.sum() > 0:
                pick = int(rng.choice(len(weight), p=weight / weight.sum()))
                return float(d_km[pick]) * factor, COMMUTE_COMMUNE_CURVE
    distance = float(od_distance_km)
    if not (np.isfinite(distance) and distance >= 0):
        raise ValueError(f"[student_incommuter_counts] origin {orig_ars5}: no commune with a positive weight and no "
                         f"valid distance_km of T ({od_distance_km!r})")
    return distance * factor, COMMUTE_OD_CELL
