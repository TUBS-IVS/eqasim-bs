"""Per-household vehicle generation (Task F5 / spec component A4).

Replaces the legacy one-car-per-person ``default_car`` fleet with exactly the
household's MiD-H7 ``number_of_cars`` vehicles per household, each owned by a
licensed adult of that household and typed via the KBA/HBEFA per-vehicle
generative chain (:mod:`braunschweig.synthesis.vehicles.fleet_sampling_de`).

Design (spec A4)
----------------
For every household:

1. ``number_of_cars`` is the MiD H7 household car count (broadcast per person in
   the enriched stage). It is aggregated back to ONE value per household via the
   max (every member carries the same household value; max is robust to any
   per-person inconsistency, matching
   :func:`braunschweig.synthesis.population.enriched._derive_car_availability_consistent`).
2. A household with ``number_of_cars == 0`` produces NO vehicle.
3. Otherwise exactly ``number_of_cars`` vehicles are emitted and assigned to the
   household's licensed adults (``has_license`` and ``age >= ADULT_AGE``),
   **highest-need first** -- ordered by descending age (a deterministic,
   reproducible proxy for car need; oldest licensed adult first), tie-broken by
   ascending ``person_id`` so the assignment is fully deterministic. The first
   ``number_of_cars`` licensed adults each receive one car.
4. If ``number_of_cars`` exceeds the number of licensed adults, the surplus cars
   still belong to the household and are assigned round-robin over the licensed
   adults (so a 2-licensed-adult household with 3 cars gives one adult 2 cars).
5. If a household has cars but NO licensed adult (rare; e.g. an elderly couple
   without a recorded licence), the cars are assigned to the OLDEST adult of the
   household and the event is counted and logged (no-silent-fallback rule). No
   car is ever dropped.

"3+" handling
-------------
MiD H7 tops out at "3 or more" (``number_of_cars == 3`` means 3+). A small
empirical tail (4th/5th car) would require the KBA FZ 3 per-Gemeinde mean cars
per household; that table is NOT among the committed derived CSVs of this fleet
work (see ``eqasim-data/data/braunschweig/kba/derived/``), so the 3+ category is
kept at 3 vehicles. This is documented here and is a conservative lower bound:
inflating 3 -> 4+ without the FZ 3 reference would be an undocumented data
assumption (prohibited by CLAUDE.md). When FZ 3 is extracted, expand here.

Car-frame columns for F4
------------------------
:func:`build_household_car_frame` emits one row per household car with the
columns :func:`braunschweig.synthesis.vehicles.fleet_sampling_de.sample_fleet`
requires:

  * ``economic_status`` -- the household's MiD economic status (from the owner's
    enriched ``economic_status``; constant within a household).
  * ``kreis_ags5`` -- ``"03" + Kreis3`` derived from the home commune's 12-digit
    ARS (``ARS[2:5]`` is the Kreis 3-digit, consistent with
    :func:`braunschweig.data.bbsr.regiostar.ars_to_ags8` and the KBA Kennziffer).
  * ``gemeinde`` -- the home Gemeinde NAME, upper-cased, matching the
    ``gemeinde`` key of ``kba_gemeinde_private_bev.csv`` (FZ 27.17). Derived from
    the RegioStaR reference (``commune_id`` -> ``name``) so it agrees with the
    extraction.
  * ``raumtyp`` -- the home RegioStaR-7 code (71..77), from the RegioStaR
    reference (``commune_id`` -> ``regiostar7``) via the AGS-8 join.

plus ``household_id`` and ``owner_id`` (the assigned licensed adult's
``person_id``) carried through to the writer.

Off-equivalence
---------------
The vehicles stage selects this module only for ``vehicles_method:"household"``.
For ``vehicles_method:"default"`` the legacy
``synthesis.vehicles.cars.default`` stage is used unchanged (byte-identical).
:func:`legacy_one_car_per_person` reproduces that exact frame and is used by the
OFF-equivalence test only.
"""

from __future__ import annotations

import logging

import pandas as pd

from braunschweig.data.bbsr.regiostar import ars_to_ags8
from braunschweig.data.kba import hsn_tsn
from braunschweig.synthesis.vehicles import fleet_sampling_de as fleet

logger = logging.getLogger(__name__)

#: Minimum age for a person to be a car-owning adult. The legal Pkw driving age
#: in Germany is 18 (Klasse B; matches ``LICENSE_MIN_AGE`` in the enriched
#: stage). ``has_license`` already forces sub-18 persons to no-licence, but the
#: explicit adult floor is kept for clarity and robustness.
ADULT_AGE = 18

#: MiD H7 caps the car count at "3 or more". A value of 3 therefore means 3+.
#: Without the KBA FZ 3 per-Gemeinde mean (not a committed derived CSV) the 3+
#: tail is kept at exactly 3 vehicles (documented in the module docstring).
NUMBER_OF_CARS_CAP = 3


def _kreis_ags5_from_ars(commune_id: str) -> str:
    """Kreis AGS-5 (``"03" + Kreis3`` inside NDS) from a home ARS.

    The 12-digit ARS is Land(2)+RB(1)+Kreis(2)+VG(4)+Gem(3); the AGS-5 Kreis code
    is Land(2)+RB(1)+Kreis(2) = ``ARS[0:5]`` (e.g. ``031010000000`` ->
    ``03101``). For an 8-digit AGS the Kreis-5 is likewise ``AGS[0:5]``. The
    leading ``03`` is the Niedersachsen Land prefix, so this equals
    ``"03" + Kreis3`` and is consistent with the KBA Kennziffer and
    :func:`braunschweig.data.bbsr.regiostar.ars_to_ags8`.
    """
    s = str(commune_id)
    return s[:5]


def build_household_car_frame(df_persons: pd.DataFrame, df_homes: pd.DataFrame,
                              df_regiostar: pd.DataFrame) -> pd.DataFrame:
    """Build the one-row-per-household-car frame F4 consumes.

    Parameters
    ----------
    df_persons : enriched population with at least ``household_id``,
        ``person_id``, ``age``, ``has_license``, ``number_of_cars`` (MiD H7,
        household value broadcast per person) and ``economic_status``.
    df_homes : home zones with ``household_id`` and ``commune_id`` (12-digit ARS).
    df_regiostar : RegioStaR reference with ``commune_id`` (8-digit AGS),
        ``name`` (Gemeinde name) and ``regiostar7`` (RS7 code).

    Returns
    -------
    DataFrame with one row per emitted vehicle carrying ``household_id``,
    ``owner_id`` and the F4 columns ``economic_status, kreis_ags5, gemeinde,
    raumtyp``. 0-car households contribute no row.
    """
    required = {"household_id", "person_id", "age", "has_license",
                "number_of_cars", "economic_status"}
    missing = required - set(df_persons.columns)
    if missing:
        raise ValueError(
            "build_household_car_frame: df_persons is missing required columns "
            f"{sorted(missing)}"
        )

    # Home commune (12-digit ARS) per household -> Kreis AGS-5 + AGS-8 join key.
    homes = df_homes[["household_id", "commune_id"]].drop_duplicates("household_id").copy()
    homes["commune_id"] = homes["commune_id"].astype(str)
    homes["kreis_ags5"] = homes["commune_id"].map(_kreis_ags5_from_ars)
    homes["ags8"] = homes["commune_id"].map(ars_to_ags8)

    # RegioStaR reference -> Gemeinde name (upper-cased, FZ 27.17 key) + RS7.
    rs = df_regiostar[["commune_id", "name", "regiostar7"]].copy()
    rs["commune_id"] = rs["commune_id"].astype(str)
    rs = rs.rename(columns={"commune_id": "ags8", "name": "gemeinde",
                            "regiostar7": "raumtyp"})
    rs["gemeinde"] = rs["gemeinde"].astype(str).str.strip().str.upper()
    homes = homes.merge(rs[["ags8", "gemeinde", "raumtyp"]], on="ags8", how="left")

    # One car-need-ordered record list per household.
    rows: list[dict] = []
    n_zero_licensed_households = 0
    n_zero_licensed_cars = 0

    # Group once; iterate households deterministically by household_id.
    for household_id, group in df_persons.groupby("household_id", sort=True):
        number_of_cars = int(group["number_of_cars"].max())
        if number_of_cars <= 0:
            continue

        home = homes[homes["household_id"] == household_id]
        if home.empty:
            raise ValueError(
                f"build_household_car_frame: no home commune for household "
                f"{household_id}"
            )
        home_row = home.iloc[0]
        economic_status = str(group["economic_status"].iloc[0])

        owners = _assign_owners(group, number_of_cars)
        if owners is None:
            # No licensed adult: assign to the oldest adult (logged fallback).
            n_zero_licensed_households += 1
            n_zero_licensed_cars += number_of_cars
            owners = _fallback_owners(group, number_of_cars)

        for owner_id in owners:
            rows.append({
                "household_id": household_id,
                "owner_id": owner_id,
                "economic_status": economic_status,
                "kreis_ags5": home_row["kreis_ags5"],
                "gemeinde": home_row["gemeinde"],
                "raumtyp": home_row["raumtyp"],
            })

    if n_zero_licensed_households:
        logger.warning(
            "[vehicles.household] %d household(s) with cars but no licensed "
            "adult (%d car(s)): assigned to the oldest adult as a fallback.",
            n_zero_licensed_households, n_zero_licensed_cars,
        )

    df_cars = pd.DataFrame.from_records(
        rows,
        columns=["household_id", "owner_id", "economic_status",
                 "kreis_ags5", "gemeinde", "raumtyp"],
    )
    return df_cars


def _assign_owners(group: pd.DataFrame, number_of_cars: int):
    """Return the owner ``person_id`` list (length ``number_of_cars``) or ``None``.

    Licensed adults are ordered highest-need-first (descending age, tie-broken by
    ascending ``person_id``). The first ``number_of_cars`` cars go one-each to the
    leading adults; any surplus is assigned round-robin over the licensed adults.
    Returns ``None`` when the household has no licensed adult (caller handles the
    fallback).
    """
    licensed = group[group["has_license"].fillna(False) & (group["age"] >= ADULT_AGE)]
    if licensed.empty:
        return None
    ordered = licensed.sort_values(
        ["age", "person_id"], ascending=[False, True])["person_id"].tolist()
    owners = [ordered[i % len(ordered)] for i in range(number_of_cars)]
    return owners


def _fallback_owners(group: pd.DataFrame, number_of_cars: int) -> list:
    """Assign every car to the oldest adult (zero-licensed-adult fallback).

    Picks the oldest adult (``age >= ADULT_AGE``); if the household has no adult
    at all, the oldest person overall is used so a car is never dropped.
    """
    adults = group[group["age"] >= ADULT_AGE]
    pool = adults if not adults.empty else group
    oldest = pool.sort_values(
        ["age", "person_id"], ascending=[False, True])["person_id"].iloc[0]
    return [oldest] * number_of_cars


def assign_vehicle_ids(df_cars: pd.DataFrame) -> pd.DataFrame:
    """Attach a unique ``vehicle_id`` and ``mode`` to each household car.

    Vehicle ids are ``"<owner_id>:car:<k>"`` with ``k`` the per-owner running
    index, so an owner with several household cars gets distinct ids while a
    single-car owner still reads cleanly. The frame is returned in input order.
    """
    df = df_cars.copy()
    df["mode"] = "car"
    per_owner_index = df.groupby("owner_id").cumcount()
    df["vehicle_id"] = (
        df["owner_id"].astype(str) + ":car:" + per_owner_index.astype(str))
    return df


def legacy_one_car_per_person(df_persons: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the legacy one-car-per-person frame (OFF path reference).

    Mirrors ``synthesis/vehicles/cars/default.py`` exactly: one ``car`` vehicle
    per person, ``vehicle_id = "<person_id>:car"``, ``type_id = "default_car"``.
    Used by the OFF-equivalence test; the production OFF path uses the unchanged
    legacy stage directly.
    """
    df = df_persons[["person_id"]].copy().rename(columns={"person_id": "owner_id"})
    df["mode"] = "car"
    df["vehicle_id"] = df["owner_id"].astype(str) + ":car"
    df["type_id"] = "default_car"
    return df


# --------------------------------------------------------------------------- #
# synpp stage: vehicles_method == "household"
# --------------------------------------------------------------------------- #
#: Allowed values for the electric-share calibration mode (``fleet_electric_calibration``).
#: Only the per-Kreis KBA FZ 27.15 mix with the per-Gemeinde FZ 27.17 BEV tilt is
#: implemented (the model the per-vehicle chain in
#: :mod:`braunschweig.synthesis.vehicles.fleet_sampling_de` realises); the key exists
#: so an unsupported mode fails early rather than silently ignoring the request.
FLEET_ELECTRIC_CALIBRATIONS = ("kreis_mix_gemeinde_bev_tilt",)


def configure(context):
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.spatial.home.zones")
    context.stage("braunschweig.data.bbsr.regiostar")
    context.config("data_path")
    context.config("random_seed")
    context.config("hbefa_segment_size_map", None)
    # Fleet model switches (Task F7). All flag-gated with the spec defaults; with
    # the German fleet disabled the stage reproduces the legacy one-car-per-person
    # default_car fleet byte-identically (OFF-equivalence).
    context.config("fleet_model_enabled", True)
    context.config("fleet_model_brands", True)
    # Additive HSN/TSN engine attributes (power kW/PS, displacement ccm, a
    # representative HSN/TSN and the dominant fuel) matched onto every vehicle by
    # brand + model family. Default True. OFF -> the five engine columns are
    # absent and the writer emits no engine attributes (byte-identical). Requires
    # fleet_model_brands (the matcher keys on brand/model); with brands off the
    # attach is skipped.
    context.config("fleet_hsn_tsn_attributes", True)
    context.config("fleet_electric_calibration", "kreis_mix_gemeinde_bev_tilt")
    # Optional override for the KBA derived-CSV directory. Default None -> the
    # readers (braunschweig.data.kba.fleet_tables) resolve the tables under
    # ``data_path``; the key is registered so an explicit override invalidates the
    # synpp cache and is documented in one place.
    context.config("kba_fleet_paths", None)


def execute(context):
    df_persons = context.stage("synthesis.population.enriched")
    df_homes = context.stage("synthesis.population.spatial.home.zones")
    df_regiostar = context.stage("braunschweig.data.bbsr.regiostar")

    # All defaults are registered in configure(); the execute context's config()
    # takes the key alone (see tests/test_execute_context_config_contract.py).
    data_path = context.config("data_path")
    random_seed = context.config("random_seed")
    size_map = context.config("hbefa_segment_size_map")
    fleet_model_enabled = bool(context.config("fleet_model_enabled"))
    model_brands = bool(context.config("fleet_model_brands"))
    hsn_tsn_attributes = bool(context.config("fleet_hsn_tsn_attributes"))
    electric_calibration = context.config("fleet_electric_calibration")
    # Optional explicit KBA derived-CSV directory; default None -> use data_path.
    kba_fleet_paths = context.config("kba_fleet_paths")
    fleet_data_path = kba_fleet_paths if kba_fleet_paths is not None else data_path

    # OFF switch: the German fleet model disabled -> emit the legacy
    # one-car-per-person default_car fleet (byte-identical to
    # synthesis.vehicles.cars.default), so a single config flag turns the whole
    # fleet feature off even while vehicles_method stays "household".
    if not fleet_model_enabled:
        logger.info(
            "[vehicles.household] fleet_model_enabled=False -> legacy "
            "one-car-per-person default_car fleet (OFF).")
        return _legacy_default_fleet(df_persons)

    if electric_calibration not in FLEET_ELECTRIC_CALIBRATIONS:
        raise ValueError(
            f"unknown fleet_electric_calibration '{electric_calibration}' "
            f"(supported: {FLEET_ELECTRIC_CALIBRATIONS})"
        )

    df_cars = build_household_car_frame(df_persons, df_homes, df_regiostar)
    df_cars = assign_vehicle_ids(df_cars)

    logger.info(
        "[vehicles.household] %d vehicles for %d households (mean %.2f cars/HH "
        "over car-owning households)",
        len(df_cars), df_cars["household_id"].nunique(),
        len(df_cars) / max(df_cars["household_id"].nunique(), 1),
    )

    # Attach the full KBA/HBEFA spec to every household car (F4 chain).
    df_spec, df_vehicle_types = fleet.sample_fleet(
        df_cars, fleet_data_path, random_seed=random_seed, size_map=size_map,
        model_brands=model_brands)

    # Additive HSN/TSN engine attributes (power/displacement/fuel + a
    # representative HSN/TSN), matched by brand + model family. Requires the
    # brand/model columns; skipped (with a logged note) when brands are off so
    # the matcher never keys on empty brand/model strings. The match-tier rates
    # are logged by attach_hsn_tsn (no-silent-fallback rule).
    if hsn_tsn_attributes:
        if not model_brands:
            logger.info(
                "[vehicles.household] fleet_hsn_tsn_attributes=True but "
                "fleet_model_brands=False -> brand/model are empty, skipping the "
                "HSN/TSN attach (no engine attributes written).")
        else:
            df_spec = hsn_tsn.attach_hsn_tsn(
                df_spec, data_path=fleet_data_path, keep_tier=True)

    # The current vehicles writer consumes critair/technology/age/euro per
    # vehicle (synthesis.vehicles.vehicles + matsim.scenario.vehicles). The
    # differentiated writer is Task F6; until then, expose writer-compatible
    # columns derived from the spec so the household frame can be written and
    # concatenated with the passenger frame.
    df_vehicles = _attach_writer_columns(df_spec)

    return df_vehicle_types, df_vehicles


def _legacy_default_fleet(df_persons: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reproduce the legacy ``synthesis.vehicles.cars.default`` stage output.

    Returns the exact ``(df_vehicle_types, df_vehicles)`` of the upstream eqasim
    default fleet (one ``default_car`` per person, single ``default_car`` type with
    HBEFA "average" attributes, the four legacy per-vehicle attributes), so that
    ``fleet_model_enabled=False`` yields a byte-identical legacy vehicles file even
    while ``vehicles_method`` stays ``"household"``. Mirrors
    ``synthesis/vehicles/cars/default.py`` field-for-field.
    """
    df_vehicle_types = pd.DataFrame.from_records([{
        "type_id": "default_car", "nb_seats": 4, "length": 5.0, "width": 1.0,
        "pce": 1.0, "mode": "car", "hbefa_cat": "PASSENGER_CAR",
        "hbefa_tech": "average", "hbefa_size": "average", "hbefa_emission": "average",
    }])

    df_vehicles = df_persons[["person_id"]].copy()
    df_vehicles = df_vehicles.rename(columns={"person_id": "owner_id"})
    df_vehicles["mode"] = "car"
    df_vehicles["vehicle_id"] = df_vehicles["owner_id"].astype(str) + ":car"
    df_vehicles["type_id"] = "default_car"
    df_vehicles["critair"] = "Crit'air 1"
    df_vehicles["technology"] = "Gazole"
    df_vehicles["age"] = 0
    df_vehicles["euro"] = 6
    return df_vehicle_types, df_vehicles


def _attach_writer_columns(df_spec: pd.DataFrame) -> pd.DataFrame:
    """Add legacy writer-compatible columns derived from the KBA spec.

    The legacy MATSim vehicles writer needs ``critair`` and ``technology`` per
    vehicle. ``technology`` is the powertrain label and ``critair`` is left empty
    (the German fleet is described by HBEFA attributes on the VehicleType, not by
    the French Crit'air scheme). ``age`` and ``euro`` already come from the spec.
    """
    df = df_spec.copy()
    df["technology"] = df["powertrain"]
    df["critair"] = ""
    df["euro"] = df["euro_class"]
    return df
