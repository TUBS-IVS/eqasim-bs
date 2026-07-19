"""Cross-cordon student in-commuter synthesis stage (#140 sub-item 2).

Default-ON per project convention, but dependency-gated: it needs the resident
university placement, which lives in the ``education_gravity`` feature. When that
parent feature is OFF and the flag is left at its default, the stage SKIPS
(empty frames + one warn) rather than raising -- a legitimate config state, not a
silent fallback. Explicitly enabling the flag while the parent is OFF is a
contradiction and raises. See docs/superpowers/specs/2026-07-18-student-incommuters-design.md.

Structurally parallel to ``braunschweig.synthesis.incommuters`` (the SvB cross-
cordon in-commuter stage), but Home->Education->Home instead of Home->Work->Home:

  - counts:  ``braunschweig.data.education.student_incommuter_counts`` (Task 2) --
             per-university-commune enrollment not filled by resident placement.
  - origins: ``braunschweig.data.education.student_origins`` (Task 3) -- reverse
             distance-decay draw of an origin Kreis per student in-commuter.
  - home:    ``braunschweig.data.cordon.incommuter_origins.incommuter_origin_homes``
             (shared with the SvB stage) for in-ring agents; agents whose origin
             Kreis has no in-ring Gemeinde fall back to the nearest cordon gate
             (never left NaN; see :func:`_nearest_gate_xy`).
  - core frames: the shared ``assemble_incommuter_core_frames`` helper (Task 1),
             called with ``middle_purpose="education"`` -- DRY with the SvB stage.
  - persons/households: student-specific, deliberately simpler than the SvB
             ``_build_persons``/``_build_households`` (no origin-Kreis income
             tilt, no per-agent German fleet draw); see
             :func:`_build_student_persons` / :func:`_build_student_households`.
  - vehicles: reuses the SvB stage's legacy (non-German-fleet) vehicle builders
             ``_build_legacy_vehicles`` / ``_build_incommuter_passenger_vehicles``
             -- one ``car`` vehicle per car-mode agent plus a ``car_passenger``
             vehicle for every agent (2026-07-18 Task 5 review fix: without this
             every student in-commuter was unroutable, see
             ``braunschweig.matsim.scenario.vehicles``).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)
_SENTINEL = object()
CRS_METRIC = "EPSG:25832"
# Fixed RNG offset so student in-commuters never share a substream with residents
# (100000) or SvB in-commuters. Distinct offset keeps draws reproducible+disjoint.
_RNG_OFFSET = 200000

# Person/household id block reserved above the resident id range for student
# in-commuters, additionally offset above the (unrelated) block the SvB
# in-commuter stage uses starting at the same n_residents/n_resident_households
# base. 10 million agents is far beyond any plausible SvB in-commuter count in
# this regional model, so this fixed offset keeps the two in-commuter id ranges
# disjoint WITHOUT a hard stage dependency on ``braunschweig.synthesis.incommuters``
# (which would drag in the full SvB gate/gravity pipeline just to read a count).
# ASSUMPTION (documented, not measured): re-verify against the actual SvB
# in-commuter count at merge time (Task 5); raise the offset if it is ever
# exceeded.
_ID_OFFSET_ABOVE_RESIDENTS = 10_000_000

# Student in-commuter person attribute defaults, mirroring the eqasim/INSEE
# activity-status mapping used for residents
# (braunschweig.ipf.attributed.derive_socioprofessional_class): studying takes
# precedence over employment, so SPC_STUDENT (=8) applies regardless of the HTS
# donor's own employment status. Deliberately simpler than
# braunschweig.synthesis.incommuters._INCOMMUTER_PERSON_DEFAULTS: no origin-Kreis
# income tilt (INKAR) and no German-fleet vehicle draw -- see
# ``_build_student_persons`` / ``_build_student_households``.
_STUDENT_PERSON_DEFAULTS = dict(
    employed=False, studies=True, household_size=1, consumption_units=1.0,
    socioprofessional_class=8,  # SPC_STUDENT, braunschweig.ipf.attributed
    number_of_bicycles=0,
    bicycle_availability="all", license_type="ja", has_license=True,
    has_pt_subscription=False, pt_subscription_type="fahre_nie",
    high_income=False,
    is_bs_resident=False, is_urban_resident=False, age_range="higher_education",
    # See braunschweig.synthesis.incommuters._INCOMMUTER_PERSON_DEFAULTS for why
    # this completeness attribute (synthesise_housing_tenure,
    # braunschweig.synthesis.population.enriched) is not applicable to in-commuters.
    housing_tenure="unknown",
    subpopulation="student_incommuter",
)


def _empty_frames(crs=CRS_METRIC):
    import geopandas as gpd
    return {
        "persons": pd.DataFrame(),
        "households": pd.DataFrame(),
        "trips": pd.DataFrame(),
        "activities": pd.DataFrame(),
        "locations": gpd.GeoDataFrame(geometry=[], crs=crs),
        # Empty vehicles/vehicle_types (2026-07-18 Task 5 review fix), matching
        # the column schema braunschweig.synthesis.incommuters._empty_frames uses,
        # so the OFF/skip path stays a no-op for both consumers
        # (braunschweig.matsim.scenario.vehicles and .population).
        "vehicles": pd.DataFrame(columns=["owner_id", "vehicle_id", "mode"]),
        "vehicle_types": pd.DataFrame(columns=["type_id", "length", "width", "mode",
                                               "hbefa_cat", "hbefa_tech", "hbefa_size",
                                               "hbefa_emission"]),
    }


def configure(context):
    context.config("cordon_enabled")
    context.config("cordon_student_incommuters_enabled", None)
    context.config("education_gravity_enabled", False)
    context.config("student_incommuter_age_band", [18, 29])
    context.config("education_university_slope", -0.1415)
    context.config("education_university_max_radius_km", 150.0)
    context.config("sampling_rate")
    context.config("random_seed")
    context.config("cordon_network_source_buffer_m")
    context.config("cordon_gate_speed_kmh", 30.0)
    context.config("data_path")
    # Mirror the keys declared by braunschweig.data.external_workplaces.configure()
    # (as braunschweig.synthesis.incommuters.configure() also does) so that
    # _inject()'s external_workplaces._load_gemeinden(context) can resolve the
    # VG250-EW archive path. synpp's ExecuteContext.config() raises for a key that
    # was not declared here, so an undeclared read would crash a real cordon run.
    context.config(
        "germany.population_path",
        "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
    context.config(
        "germany.population_source",
        "vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg")
    context.stage("braunschweig.data.schools.university_facilities")
    context.stage("synthesis.population.spatial.primary.locations")
    context.stage("data.spatial.municipalities")
    # German MiD donor (not ENTD): student trip TIMING comes from German
    # behaviour. See braunschweig.data.hts.mid_donor + the design spec.
    context.stage("braunschweig.data.hts.mid_donor", alias="hts")
    context.stage("braunschweig.synthesis.cordon_gates")
    context.stage("synthesis.population.enriched")


def _active(context):
    """Resolve the tri-state activation. Returns True/False; raises on the
    contradictory explicit-on-but-parent-off case."""
    if not context.config("cordon_enabled"):
        return False
    flag = context.config("cordon_student_incommuters_enabled")
    parent = bool(context.config("education_gravity_enabled"))
    if flag is True and not parent:
        raise RuntimeError(
            "cordon_student_incommuters_enabled=True requires "
            "education_gravity_enabled=True (the count anchor needs the resident "
            "university placement). Enable education_gravity or unset the flag.")
    if flag is False:
        return False
    if not parent:
        _log.warning(
            "[student_incommuters] skipped: requires education_gravity_enabled "
            "(parent feature off, flag left at default). Injecting no students.")
        return False
    return True


def execute(context):
    if not _active(context):
        return _empty_frames()
    return _inject(context)


def _nearest_gate_xy(orig_ars5, kreis_xy, gates_gdf):
    """Nearest major cordon-gate point (Euclidean, ``CRS_METRIC``) per agent's
    origin-Kreis centroid.

    Used as the home-point fallback for student in-commuters whose origin Kreis
    has no in-ring Gemeinde (see ``incommuter_origin_homes``): this mirrors the
    SvB stage's gate-anchored home for far agents, but picks the geometrically
    NEAREST gate rather than the SvB gravity-weighted draw
    (``sample_gate_per_agent``) -- the gravity gate-assignment table only covers
    Kreise with SvB commuter volume, which does not necessarily cover every
    student-origin Kreis, so a geometric nearest-gate lookup is the more general
    (and always-defined) fallback.

    Args:
        orig_ars5: per-agent 5-digit origin Kreis ARS (only the far agents).
        kreis_xy: DataFrame ``[ars5, x, y]`` -- candidate origin-Kreis centroids
            (see ``_inject`` step 3); must contain every ars5 in ``orig_ars5``.
        gates_gdf: GeoDataFrame of major cordon gates (point geometry), from the
            ``braunschweig.synthesis.cordon_gates`` stage.

    Returns:
        ``(x, y)`` float ndarrays, one entry per input agent.

    Raises:
        RuntimeError: if ``gates_gdf`` is empty (the gate stage did not run with
            ``cordon_enabled=True``, which should never happen once ``_active``
            returned True).
    """
    if gates_gdf is None or len(gates_gdf) == 0:
        raise RuntimeError(
            "student_incommuters: gate fallback needed for far agents but "
            "braunschweig.synthesis.cordon_gates returned no gates -- check that "
            "cordon_enabled is True for both stages.")
    gate_x = gates_gdf.geometry.x.to_numpy(dtype=float)
    gate_y = gates_gdf.geometry.y.to_numpy(dtype=float)
    kreis_pos = dict(zip(kreis_xy["ars5"].astype(str),
                         zip(kreis_xy["x"].astype(float), kreis_xy["y"].astype(float))))
    xs = np.empty(len(orig_ars5), dtype=float)
    ys = np.empty(len(orig_ars5), dtype=float)
    for i, ars in enumerate(orig_ars5):
        kx, ky = kreis_pos[str(ars)]
        d2 = (gate_x - kx) ** 2 + (gate_y - ky) ** 2
        j = int(np.argmin(d2))
        xs[i] = gate_x[j]
        ys[i] = gate_y[j]
    return xs, ys


def _donor_education_times(donors, hts_trips, person_col):
    """Per-agent Home->education->Home timings (seconds since midnight) from HTS
    donors, memoised per UNIQUE donor id.

    Donors are sampled WITH REPLACEMENT (student donors are far fewer than
    agents), so the same HTS person backs many agents; this mirrors
    ``braunschweig.synthesis.incommuters._agent_times``'s memoisation -- the
    (filter + sort) inside ``extract_activity_times`` runs once per DISTINCT
    donor id, not once per agent.

    Returns ``(depart_home, arrive_mid, depart_mid, arrive_home)`` float ndarrays,
    one entry per agent (donors row order).
    """
    from braunschweig.data.cordon.plans import extract_activity_times

    donor_ids = donors[person_col].to_numpy()
    unique_ids = pd.unique(donor_ids)
    used = hts_trips[hts_trips[person_col].isin(unique_ids)]
    by_person = {pid: sub for pid, sub in used.groupby(person_col)}
    times_by_id = {
        pid: extract_activity_times(by_person[pid], purpose="education")
        for pid in unique_ids
    }
    depart_home = np.array([times_by_id[pid][0] for pid in donor_ids], dtype=float)
    arrive_mid = np.array([times_by_id[pid][1] for pid in donor_ids], dtype=float)
    depart_mid = np.array([times_by_id[pid][2] for pid in donor_ids], dtype=float)
    arrive_home = np.array([times_by_id[pid][3] for pid in donor_ids], dtype=float)
    return depart_home, arrive_mid, depart_mid, arrive_home


def _build_student_persons(ids, donors, modes):
    """Persons frame for injected student in-commuters, mirroring the resident
    attribute schema used by ``braunschweig.synthesis.incommuters._build_persons``
    (age/sex/hts linkage columns, car_availability, income columns) but with the
    student-specific defaults from :data:`_STUDENT_PERSON_DEFAULTS` instead of the
    SvB employed-commuter defaults.

    Deliberately simpler than the SvB in-commuter persons builder: no origin-
    Kreis income tilt (INKAR) and no per-agent German-fleet draw -- students get
    a flat regional-mean household income
    (``braunschweig.synthesis.incommuters.INCOMMUTER_BASE_INCOME_EUR``) and a
    mode-consistent ``car_availability``/``number_of_cars`` (car-mode agents own a
    car; PT-mode agents do not -- a documented simplification, not a measured
    student car-ownership rate).
    """
    from braunschweig.synthesis.incommuters import INCOMMUTER_BASE_INCOME_EUR

    person_ids = ids["person_id"].to_numpy()
    household_ids = ids["household_id"].to_numpy()
    modes = np.asarray(modes)
    age = donors["age"].to_numpy() if "age" in donors.columns else 22
    sex = donors["sex"].to_numpy() if "sex" in donors.columns else "male"
    hts_id = donors["person_id"].to_numpy() if "person_id" in donors.columns else -1
    income_eur = np.full(len(person_ids), INCOMMUTER_BASE_INCOME_EUR, dtype=float)
    is_car = modes == "car"
    persons = pd.DataFrame({
        "person_id": person_ids,
        "household_id": household_ids,
        "census_person_id": person_ids,
        "census_household_id": household_ids,
        "hts_id": hts_id, "hts_household_id": -1,
        "age": age, "sex": sex,
        "car_availability": np.where(is_car, "all", "none"),
        "number_of_cars": np.where(is_car, 1, 0),
        "household_income_eur": income_eur,
        "household_income": [str(int(v)) for v in income_eur],
    })
    for key, value in _STUDENT_PERSON_DEFAULTS.items():
        persons[key] = value
    return persons


def _build_student_households(ids):
    """One single-person household per injected student in-commuter, mirroring
    ``braunschweig.synthesis.incommuters._build_households`` with the flat
    regional-mean income (no INKAR tilt; see :func:`_build_student_persons`)."""
    from braunschweig.synthesis.incommuters import INCOMMUTER_BASE_INCOME_EUR

    household_ids = ids["household_id"].to_numpy()
    person_ids = ids["person_id"].to_numpy()
    income_eur = np.full(len(household_ids), INCOMMUTER_BASE_INCOME_EUR, dtype=float)
    return pd.DataFrame({
        "household_id": household_ids,
        "person_id": person_ids,
        "census_household_id": household_ids,
        "household_income": [str(int(v)) for v in income_eur],
        "household_income_eur": income_eur,
        "high_income": False,
        "car_availability": "all", "bicycle_availability": "all",
    })


def _inject(context):
    """Build the Home->Education->Home student in-commuter frames.

    Orchestrates, in order: the count anchor (Task 2), the reverse-decay origin
    draw (Task 3), the shared in-ring/gate home placement, the shared core-frame
    assembly (Task 1), and the student-specific persons/households builders
    above. See the module docstring for the stage-level design.
    """
    from braunschweig.constants import ROUTED_DETOUR_FACTOR
    from braunschweig.data.cordon import plans
    from braunschweig.data.cordon.demand import make_incommuter_ids
    from braunschweig.data.cordon.incommuter_origins import incommuter_origin_homes
    from braunschweig.data.cordon.mode_reference import (
        MID_DISTANCE_EDGES, restrict_to_modes, route_distance_band)
    from braunschweig.data.education import student_incommuter_counts as sic
    from braunschweig.data.education import student_origins as so
    from braunschweig.data.external_workplaces import _load_gemeinden
    from braunschweig.data.mikrozensus.reference import load_commute_mode_by_distance
    from braunschweig.synthesis.incommuters import (
        _build_incommuter_passenger_vehicles, _build_legacy_vehicles,
        assemble_incommuter_core_frames)

    sampling_rate = float(context.config("sampling_rate"))
    slope = float(context.config("education_university_slope"))
    max_radius_km = float(context.config("education_university_max_radius_km"))
    age_lower, age_upper = context.config("student_incommuter_age_band")
    rng = np.random.default_rng(int(context.config("random_seed")) + _RNG_OFFSET)
    gate_speed_kmh = float(context.config("cordon_gate_speed_kmh"))
    data_path = context.config("data_path")

    facilities = context.stage(
        "braunschweig.data.schools.university_facilities").to_crs(CRS_METRIC)
    municipalities = context.stage("data.spatial.municipalities").to_crs(CRS_METRIC)
    resident_placement = context.stage("synthesis.population.spatial.primary.locations")
    residents = context.stage("synthesis.population.enriched")
    _hh, hts_persons, hts_trips = context.stage("hts")
    gate_frames = context.stage("braunschweig.synthesis.cordon_gates")

    # 1. Counts per university commune (data-anchored count anchor, Task 2).
    counts = sic.compute_incommuter_counts(
        facilities, municipalities, resident_placement, sampling_rate)
    counts = counts[counts["in_commuters"] > 0].reset_index(drop=True)
    if counts.empty:
        _log.warning(
            "[student_incommuters] no positive in-commuter count in any "
            "university commune (enrollment already filled by resident "
            "placement) -- nothing injected")
        return _empty_frames()

    # 2. Destination point per commune = capacity-weighted centroid of its local
    #    university facilities (the same buildings residents are placed in).
    fac_comm = sic.facility_communes(facilities, municipalities)
    fac = facilities.merge(fac_comm, on="location_id", how="inner")
    dest_xy = {}
    for comm, grp in fac.groupby("commune_ars5"):
        w = grp["capacity"].to_numpy(dtype=float)
        dest_xy[comm] = (float(np.average(grp.geometry.x, weights=w)),
                         float(np.average(grp.geometry.y, weights=w)))

    # 3. Candidate ORIGIN Kreise: all German Kreise OUTSIDE the ZGB cordon, one
    #    representative point (dissolved centroid) per Kreis.
    gem = _load_gemeinden(context).to_crs(CRS_METRIC)
    zgb_ars5 = {str(p) for p in context.config("braunschweig.political_prefix")}
    ext = gem[~gem["ars5"].astype(str).isin(zgb_ars5)].copy()
    centroids = ext.dissolve(by="ars5").geometry.centroid
    kreis_xy = pd.DataFrame({
        "ars5": centroids.index.astype(str),
        "x": centroids.x.to_numpy(dtype=float),
        "y": centroids.y.to_numpy(dtype=float),
    })
    kreis_pop = so.student_age_pop_by_kreis(
        data_path, kreis_xy["ars5"].tolist(), age_lower, age_upper)

    # 4. Draw origin Kreise (reverse decay, Task 3).
    origins = so.draw_origin_kreise(dest_xy, counts, kreis_xy, kreis_pop,
                                    slope=slope, max_radius_km=max_radius_km, rng=rng)
    n = len(origins)
    if n == 0:
        _log.warning(
            "[student_incommuters] origin-Kreis draw produced zero agents "
            "(check the 18-29 population table) -- nothing injected")
        return _empty_frames()

    # 5. Home points: real population-weighted origin point when the source
    #    Kreis has an in-ring Gemeinde (shared helper, also used by the SvB
    #    stage); far agents (no in-ring Gemeinde) fall back to the nearest
    #    cordon gate so home coordinates are NEVER left NaN
    #    (CLAUDE.md no-silent-fallbacks).
    zgb_polygon = municipalities.geometry.union_all()
    source_buffer_m = float(context.config("cordon_network_source_buffer_m"))
    inbound_by_kreis = (
        origins.groupby("orig_ars5").size().reset_index(name="flow")
        .rename(columns={"orig_ars5": "ars5"}))
    gemeinden_for_homes = gem[["ars5", "gem_ags", "ewz", "geometry"]].copy()
    home_x, home_y, is_in_ring = incommuter_origin_homes(
        origins["orig_ars5"].tolist(), inbound_by_kreis, gemeinden_for_homes,
        zgb_polygon, source_buffer_m, rng)

    n_far = int((~is_in_ring).sum())
    if n_far > 0:
        far_idx = np.where(~is_in_ring)[0]
        far_ars5 = origins["orig_ars5"].to_numpy()[far_idx]
        far_x, far_y = _nearest_gate_xy(far_ars5, kreis_xy, gate_frames["gates"])
        home_x[far_idx] = far_x
        home_y[far_idx] = far_y
    n_in_ring = n - n_far
    pct_in_ring = 100.0 * n_in_ring / n
    _log.info(
        "[student_incommuters] home placement: in-ring primary %d/%d (%.1f%%), "
        "gate fallback %d/%d (%.1f%%)",
        n_in_ring, n, pct_in_ring, n_far, n, 100.0 - pct_in_ring)
    if np.isnan(home_x).any() or np.isnan(home_y).any():
        raise RuntimeError(
            "student_incommuters: home coordinates still contain NaN after the "
            "in-ring + gate fallback -- this must never happen (fallback bug)")

    # 6. Destination (education) point per agent.
    dest_x = np.array([dest_xy[c][0] for c in origins["dest_commune"]], dtype=float)
    dest_y = np.array([dest_xy[c][1] for c in origins["dest_commune"]], dtype=float)

    # 7. Ids off the residents' running max+1, offset by a large fixed block so
    #    student in-commuters never collide with residents OR the SvB
    #    in-commuter block (see _ID_OFFSET_ABOVE_RESIDENTS).
    n_residents = int(residents["person_id"].max()) + 1
    n_resident_households = int(residents["household_id"].max()) + 1
    ids = make_incommuter_ids(n, n_residents + _ID_OFFSET_ABOVE_RESIDENTS,
                              n_resident_households + _ID_OFFSET_ABOVE_RESIDENTS)
    person_ids = ids["person_id"].to_numpy()
    edu_location_ids = [f"ic_edu_{int(pid)}" for pid in person_ids]

    # 8. Donors + education-leg timings (memoised per unique donor id) + fixed
    #    commute mode from the Mikrozensus distance-band reference.
    donors_pool = plans.select_student_donors(hts_persons, hts_trips, "person_id")
    donors = plans.sample_donors(donors_pool, n, rng)
    _donor_depart_home, arrive_mid, depart_mid, arrive_home = _donor_education_times(
        donors, hts_trips, "person_id")

    dist_km = plans.straight_line_distance_km(home_x, home_y, dest_x, dest_y)

    # Distance-consistent home-departure seed (mirrors the SvB path's _agent_times):
    # the RAW donor depart_home comes from an HTS trip whose length is unrelated to
    # this agent's synthetic home->campus distance, so for a far agent (home = nearest
    # gate, campus inside ZGB) it can imply an absurd travel speed in the seed plan.
    # Re-seed depart_home = arrive_mid - (routed home->campus distance / gate speed) so
    # the initial schedule is speed-consistent; arrive_mid (donor arrival at education)
    # stays the anchor and the return leg keeps the donor timing, exactly as the SvB
    # stage does. MATSim re-times the simulated leg over the iterations regardless; this
    # only fixes the seed plan (see docs/features/student-incommuters.md timing note).
    travel_s = (dist_km * ROUTED_DETOUR_FACTOR / gate_speed_kmh) * 3600.0
    depart_home = np.maximum(0.0, arrive_mid - travel_s)
    reference = restrict_to_modes(
        load_commute_mode_by_distance(data_path), allowed=("car", "pt"))
    band_fn = lambda d: route_distance_band(  # noqa: E731
        d, detour_factor=ROUTED_DETOUR_FACTOR, edges=MID_DISTANCE_EDGES)
    modes = list(plans.assign_fixed_mode(dist_km, reference, band_fn, rng))

    # 9. Shared core frames (DRY: same helper the SvB stage uses).
    core = assemble_incommuter_core_frames(
        person_ids, home_x, home_y, dest_x, dest_y, edu_location_ids,
        depart_home, arrive_mid, depart_mid, arrive_home, modes,
        municipalities.crs, middle_purpose="education")

    # 10. Minimal student persons/households (no income tilt, no fleet).
    persons = _build_student_persons(ids, donors, modes)
    households = _build_student_households(ids)

    # 10a. Origin Kreis + destination university commune, attached for the
    # downstream OD analysis (braunschweig.analysis.simwrapper.student_commuters,
    # #140 Task 6). ``origins`` (step 4) and ``ids``/``persons`` (step 7-10) are
    # both built from the SAME length-n draw without any intervening sort, so a
    # positional (not id-keyed) assignment is safe here. These are EXTRA columns
    # on top of the schema matsim.scenario.population's concat_frame expects --
    # concat_frame reindexes the in-commuter frame onto the resident columns
    # before concatenating, so the extra columns are dropped there and never
    # reach the MATSim population writer (verified against
    # braunschweig.synthesis.incommuter_merge._base.concat_frame).
    persons["orig_ars5"] = origins["orig_ars5"].to_numpy()
    persons["dest_commune"] = origins["dest_commune"].to_numpy()

    # 10b. Vehicles (2026-07-18 Task 5 review fix). Every in-commuter -- resident,
    # SvB, or student -- must own a "car_passenger" vehicle: it is a network-routed
    # mode (eqasim core NETWORK_MODES = [car, car_passenger, truck]) that the
    # in-loop discrete mode choice can assign to ANY agent regardless of car
    # ownership, so a missing vehicle aborts the MATSim router. Car-mode agents
    # additionally need a "car" vehicle. Students have no income/fleet model (see
    # _build_student_persons), so they reuse the SvB stage's LEGACY (non-German-
    # fleet) vehicle builders -- the same path the SvB stage takes when no
    # ``data_path`` is supplied; no distinct HBEFA vehicle type is introduced, so
    # ``vehicle_types`` stays empty (the resident/SvB ``default_car`` /
    # ``default_car_passenger`` types already cover these vehicles).
    vehicle_types = pd.DataFrame(columns=["type_id", "length", "width", "mode",
                                          "hbefa_cat", "hbefa_tech", "hbefa_size",
                                          "hbefa_emission"])
    vehicles = _build_legacy_vehicles(person_ids, modes)
    vehicles = pd.concat(
        [vehicles, _build_incommuter_passenger_vehicles(person_ids)], ignore_index=True)

    # 11. Per-commune injection counts (CLAUDE.md traceability).
    _log.info(
        "[student_incommuters] injected %d students across %d university "
        "communes: %s", n, len(counts),
        dict(zip(counts["commune_ars5"], counts["in_commuters"])))

    return {
        "persons": persons, "households": households,
        "trips": core["trips"], "activities": core["activities"],
        "locations": core["locations"],
        "vehicles": vehicles, "vehicle_types": vehicle_types,
    }
