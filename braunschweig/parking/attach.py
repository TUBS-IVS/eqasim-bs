"""Attach the parking cost zones and resident districts to the plan elements of the MATSim population (issue #249).

The zone-based parking costs (design spec 2026-09-28, sections 3.4 and 3.5) need three facts on the plans:
the zone of every activity location, the resident zone of every person and the free-parking draw of the
work/education activities; parking cost zones v2 (spec Amendment C3) adds the resident parking district of every
activity location and of every person's home. ``braunschweig.matsim.scenario.population`` calls the five functions
below in this order on the frames the plans writer consumes, AFTER the cross-cordon in-commuter merge, so injected
in-commuters are treated exactly like residents:

1. :func:`attach_parking_zones` adds ``parking_zone`` to the activities: the ``zone_id`` of the zone polygon
   that contains the activity location (``braunschweig.parking.zones.assign_zones``), NaN outside every
   zone (assumption Z1: parking there is free). Written as the activity attribute ``parkingZone``.
2. :func:`attach_resident_zones` adds ``resident_parking_zone`` to the persons: the zone of the person's
   home when that zone is a ``resident_zone`` (assumption R1: residence equals permit possession), NaN
   otherwise. Written as the person attribute ``residentParkingZone``.
3. :func:`draw_parking_free` adds ``parking_free`` to the activities: True on the ``ELIGIBLE_PURPOSES``
   activities in a ``street_paid`` or ``resident_zone`` zone of a person drawn to park free at the
   workplace, False everywhere else. One draw per person, P(free | workplace class) = ``share_free_total``
   of the SrV 2023 class row (assumption A1) shifted by the sensitivity arm
   ``parking_workplace_free_share_shift``; campus zones are never drawn (assumption C1: members pay the day
   product). Written as the activity attribute ``parkingFree`` (only where True).
4. :func:`attach_parking_districts` adds ``parking_district`` to the activities: the ``district_id`` of the resident
   parking district that contains the activity location (``braunschweig.parking.zones.assign_districts``), NaN
   outside every district. The districts are a second layer, independent of the fee zones and free to overlap them.
   Written as the activity attribute ``parkingDistrict``.
5. :func:`attach_resident_districts` adds ``resident_parking_district`` to the persons: the district of the person's
   home, NaN when the home lies in no district (assumption R2: residence inside a district equals permit possession;
   the cost model exempts a stay inside that very district). Written as the person attribute
   ``residentParkingDistrict``.

Every function returns a COPY of the frame it extends with exactly one column added -- same rows, same
order, same index -- and raises instead of writing a wrong attribute: a missing, duplicated or empty
location, a zone id without a tariff row, a workplace class without an SrV share, conflicting home zones or
districts. Coverage is logged as explicit rates under ``[parking]`` (CLAUDE.md "Fallback transparency"): "no zone",
"no resident zone", "no district" and "not free" are modelled states, and their rates are the evidence that the
joins worked. CRS: locations, zones and districts share one metric CRS (EPSG:25832,
``braunschweig.parking.zones.CRS``).
"""
from __future__ import annotations

import logging
import math

import geopandas as gpd
import numpy as np
import pandas as pd

from braunschweig.parking import zones as parking_zones

log = logging.getLogger(__name__)

_LOG_TAG = "[parking]"

#: Offset of the free-parking draw on the pipeline ``random_seed`` (spec 3.4). No other seeded stream of
#: the repository uses it (checked with ``grep -rn 7371`` on 2026-09-28 and again on 2026-09-29).
PARKING_FREE_SEED_OFFSET = 7371
#: Activity purposes whose parking the free-parking draw covers (spec 3.4).
ELIGIBLE_PURPOSES = ("work", "education")
#: Zone types in which the free-parking draw applies; campus zones use the member day product (C1).
FREE_DRAW_ZONE_TYPES = ("street_paid", "resident_zone")
RESIDENT_ZONE_TYPE = "resident_zone"
CAMPUS_ZONE_TYPE = "campus"
HOME_PURPOSE = "home"

#: The columns added here. ``matsim.scenario.population`` lists them in OPTIONAL_ACTIVITY_FIELDS and
#: OPTIONAL_PERSON_FIELDS and writes them as parkingZone, parkingFree, residentParkingZone, parkingDistrict and
#: residentParkingDistrict.
PARKING_ZONE_COLUMN = "parking_zone"
PARKING_FREE_COLUMN = "parking_free"
RESIDENT_PARKING_ZONE_COLUMN = "resident_parking_zone"
PARKING_DISTRICT_COLUMN = "parking_district"
RESIDENT_PARKING_DISTRICT_COLUMN = "resident_parking_district"

#: Key of one activity in the activities and the locations frame of the plans writer.
ACTIVITY_KEYS = ("person_id", "activity_index")

#: Layout of the SrV commute-parking table written by ``scripts/extract_srv_commute_parking.py``
#: (``braunschweig.calibration.srv_parking``): ``level`` is "class" for a workplace class and "total" for
#: the pooled row, which is informational and never used by the draw.
LEVEL_CLASS = "class"
WORKPLACE_SHARE_LEVELS = ("class", "total")
FREE_SHARE_COLUMN = "share_free_total"
WORKPLACE_SHARE_COLUMNS = ("workplace_class", "level", FREE_SHARE_COLUMN)

#: How many zones the coverage line of :func:`attach_parking_zones` names (by activity count).
TOP_ZONES_LOGGED = 10


# --------------------------------------------------------------------------- input checks


def _require_columns(frame: pd.DataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} the {what} lack the column(s) {missing}; found {list(frame.columns)}")


def _check_activity_keys(frame: pd.DataFrame, what: str) -> None:
    """Every row needs a complete, unique (person_id, activity_index) key: the joins match on it."""
    keys = frame[list(ACTIVITY_KEYS)]
    incomplete = keys.isna().any(axis=1)
    if incomplete.any():
        raise ValueError(f"{_LOG_TAG} {int(incomplete.sum())} row(s) of the {what} have no complete "
                         "(person_id, activity_index) key")
    duplicated = keys.duplicated(keep=False)
    if duplicated.any():
        examples = sorted(set(keys[duplicated].itertuples(index=False, name=None)))[:5]
        raise ValueError(f"{_LOG_TAG} {int(duplicated.sum())} row(s) of the {what} share a (person_id, "
                         f"activity_index) key, e.g. {examples}; a key must identify exactly one activity")


def _zone_attribute(tariffs: pd.DataFrame, column: str) -> pd.Series:
    """``column`` of the tariff table indexed by ``zone_id``; raises for a missing column or a duplicate id."""
    _require_columns(tariffs, ("zone_id", column), "tariffs")
    duplicated = sorted(set(tariffs.loc[tariffs["zone_id"].duplicated(), "zone_id"]))
    if duplicated:
        raise ValueError(f"{_LOG_TAG} the tariffs carry duplicate zone_id(s) {duplicated}")
    return pd.Series(tariffs[column].to_numpy(dtype=object),
                     index=pd.Index(tariffs["zone_id"].to_numpy(dtype=object), name="zone_id"), name=column)


def _reject_unknown_zones(zone_ids: pd.Series, known_zone_ids, what: str) -> None:
    unknown = sorted(set(zone_ids.dropna()) - set(known_zone_ids))
    if unknown:
        raise ValueError(f"{_LOG_TAG} zone id(s) {unknown} of the {what} have no tariff row: the zone polygons "
                         "and the tariff table must belong to one release (braunschweig.parking.zones_stage)")


def format_value_counts(values: pd.Series, limit: int | None = None, *, by_value: bool = False) -> str:
    """'value count, ...' of the non-missing ``values`` for a log line; 'none' when every value is missing.

    Sorted by descending count (ties by value), or by value with ``by_value`` (a fixed order, e.g. the zone
    types of a release); ``limit`` keeps the first entries only. The one count-line helper of the parking
    package: ``braunschweig.parking.zones_stage`` uses it, too.
    """
    counts = values.dropna().value_counts().items()
    if by_value:
        counts = sorted(counts, key=lambda item: str(item[0]))
    else:
        counts = sorted(counts, key=lambda item: (-item[1], str(item[0])))
    if limit is not None:
        counts = counts[:limit]
    return ", ".join(f"{value} {count}" for value, count in counts) or "none"


def _rate(count: int, total: int) -> float:
    return 100.0 * count / total if total else 0.0


# --------------------------------------------------------------------------- activity zones


def _located_points(activities: pd.DataFrame, locations: gpd.GeoDataFrame) -> gpd.GeoSeries:
    """The location point of every activity, in the row order of ``activities`` (shared by the zone and the district
    attachment, so both layers see exactly the same validated points).

    Rows are matched on ``ACTIVITY_KEYS``, never on their order. Raises ``TypeError`` unless ``locations`` is a
    GeoDataFrame, and ``ValueError`` for a missing column, an incomplete or duplicated key, an activity without a
    location row and a null, empty or non-point location.
    """
    _require_columns(activities, ACTIVITY_KEYS, "activities")
    if not isinstance(locations, gpd.GeoDataFrame):
        raise TypeError(f"{_LOG_TAG} the locations must be a GeoDataFrame with the activity points, "
                        f"got {type(locations).__name__}")
    _require_columns(locations, ACTIVITY_KEYS, "locations")
    _check_activity_keys(activities, "activities")
    _check_activity_keys(locations, "locations")
    n_activities = len(activities)

    location_points = pd.DataFrame({key: locations[key].to_numpy() for key in ACTIVITY_KEYS})
    location_points["_geometry"] = locations.geometry.to_numpy()
    located = activities[list(ACTIVITY_KEYS)].reset_index(drop=True).merge(
        location_points, on=list(ACTIVITY_KEYS), how="left", validate="one_to_one", indicator=True)
    without_location = (located["_merge"] == "left_only").to_numpy()
    if without_location.any():
        examples = located.loc[without_location, list(ACTIVITY_KEYS)].head(5).itertuples(index=False, name=None)
        raise ValueError(f"{_LOG_TAG} {int(without_location.sum())} of {n_activities} activities have no location "
                         f"row (joined on person_id, activity_index), e.g. {list(examples)}; every activity needs "
                         "its location, so the locations frame is broken")
    points = gpd.GeoSeries(located["_geometry"].to_numpy(), crs=locations.crs)
    missing_geometry = (points.isna() | points.is_empty).to_numpy()
    if missing_geometry.any():
        examples = located.loc[missing_geometry, list(ACTIVITY_KEYS)].head(5).itertuples(index=False, name=None)
        raise ValueError(f"{_LOG_TAG} {int(missing_geometry.sum())} of {n_activities} activity locations have no "
                         f"geometry (null or empty), e.g. {list(examples)}; every activity needs a location point")
    not_point = (points.geom_type != "Point").to_numpy()
    if not_point.any():
        raise ValueError(f"{_LOG_TAG} {int(not_point.sum())} activity location geometries are not points, e.g. "
                         f"{sorted(set(points[not_point].geom_type))}; the plans writer places activities at points")
    return points


def attach_parking_zones(activities: pd.DataFrame, locations: gpd.GeoDataFrame,
                         zones: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return ``activities`` with ``parking_zone``: the zone id of each activity location, NaN outside.

    ``locations`` is the locations frame of the plans writer (``synthesis.population.spatial.locations``
    plus the injected in-commuters): one Point per (person_id, activity_index) in the CRS of ``zones``
    (EPSG:25832); rows are matched on that key, never on their order. ``zones`` carries ``zone_id`` and the
    polygons (``braunschweig.parking.zones_stage``); ``braunschweig.parking.zones.assign_zones`` makes the
    point-in-polygon test and raises for a point inside two zones. The new column is of object dtype.

    Raises ``TypeError`` unless ``locations`` is a GeoDataFrame, and ``ValueError`` for a missing column, an
    incomplete or duplicated key, an activity without a location row, a null, empty or non-point location,
    a CRS mismatch, and -- the signature of a broken join or CRS -- zones present but not one activity
    inside any of them. Logs ``[parking] activities in zones: n/N (x %)`` with the most frequent zones.
    """
    points = _located_points(activities, locations)
    result = activities.copy()
    n_activities = len(activities)

    if len(zones) == 0:
        result[PARKING_ZONE_COLUMN] = pd.Series(np.nan, index=result.index, dtype=object)
        log.warning("%s no parking zones given: all %d activities lie outside every zone and park free "
                    "(assumption Z1)", _LOG_TAG, n_activities)
        return result

    assigned = parking_zones.assign_zones(gpd.GeoDataFrame(geometry=points), zones)
    result[PARKING_ZONE_COLUMN] = pd.Series(assigned.to_numpy(dtype=object), index=result.index, dtype=object)
    n_inside = int(assigned.notna().sum())
    log.info("%s activities in zones: %d/%d (%.1f %%), %d outside every zone park free (assumption Z1); "
             "top zones: %s", _LOG_TAG, n_inside, n_activities, _rate(n_inside, n_activities),
             n_activities - n_inside, format_value_counts(assigned, TOP_ZONES_LOGGED))
    if n_activities > 0 and n_inside == 0:
        raise ValueError(f"{_LOG_TAG} not one of the {n_activities} activities lies in any of the {len(zones)} "
                         f"parking zones: this is the signature of a broken locations join or a CRS mismatch "
                         f"(locations {locations.crs}, zones {zones.crs}), not of a population that never enters "
                         "a zone")
    return result


# --------------------------------------------------------------------------- resident zones


def _home_activities(activities: pd.DataFrame, column: str, what: str) -> pd.DataFrame:
    """The ``person_id`` and ``column`` of the ``home`` activities (``what`` names the attribute read from them).

    Raises ``ValueError`` when no activity at all has the purpose ``home``: that is a purpose-label mismatch that
    would silently exempt nobody, not a population without homes.
    """
    is_home = (activities["purpose"] == HOME_PURPOSE).to_numpy()
    if len(activities) > 0 and not is_home.any():
        raise ValueError(f"{_LOG_TAG} none of the {len(activities)} activities has the purpose {HOME_PURPOSE!r}; "
                         f"the {what} is read from the home activities, so this is a purpose-label mismatch, "
                         "not a population without homes")
    return activities.loc[is_home, ["person_id", column]]


def _reject_conflicting_homes(homes: pd.DataFrame, column: str, what: str) -> None:
    """Raise unless all home activities of every person share one ``column`` value (``what``: the plural noun)."""
    values_per_person = homes.groupby("person_id")[column].nunique(dropna=False)
    conflicting = list(values_per_person.index[values_per_person.to_numpy() > 1])
    if conflicting:
        raise ValueError(f"{_LOG_TAG} {len(conflicting)} person(s) have home activities in different {what}, e.g. "
                         f"person_id {conflicting[:5]}; all home activities of a person lie at the household home, "
                         "so the locations frame is broken")


def attach_resident_zones(persons: pd.DataFrame, activities: pd.DataFrame, tariffs: pd.DataFrame) -> pd.DataFrame:
    """Return ``persons`` with ``resident_parking_zone``: the resident zone containing the person's home.

    ``activities`` must already carry ``parking_zone`` (:func:`attach_parking_zones`). The home of a person
    is where its ``home`` activities take place; all of them lie at the household home, so they share one
    zone, and a person whose home activities lie in different zones (or inside and outside a zone) raises.
    The zone counts only when its tariff row has ``zone_type == "resident_zone"`` (assumption R1); a home
    in a zone of another type or outside every zone, and a person without a home activity, get NaN. The new
    column is of object dtype. Raises ``ValueError`` as well for a missing column, a home zone without a
    tariff row, and activities without a single ``home`` purpose (a label mismatch that would silently
    exempt nobody). Logs the resident count per zone as a rate.
    """
    _require_columns(persons, ("person_id",), "persons")
    _require_columns(activities, ("person_id", "purpose", PARKING_ZONE_COLUMN), "activities")
    zone_types = _zone_attribute(tariffs, "zone_type")
    homes = _home_activities(activities, PARKING_ZONE_COLUMN, "resident zone")
    _reject_unknown_zones(homes[PARKING_ZONE_COLUMN], zone_types.index, "home activities")
    _reject_conflicting_homes(homes, PARKING_ZONE_COLUMN, "parking zones (or inside and outside a zone)")

    home_zone = homes.drop_duplicates("person_id").set_index("person_id")[PARKING_ZONE_COLUMN]
    home_zone_type = home_zone.map(zone_types).to_numpy(dtype=object)
    in_resident_zone = home_zone_type == RESIDENT_ZONE_TYPE
    resident_zone = home_zone[in_resident_zone]
    result = persons.copy()
    result[RESIDENT_PARKING_ZONE_COLUMN] = pd.Series(result["person_id"].map(resident_zone).to_numpy(dtype=object),
                                                     index=result.index, dtype=object)

    n_persons = len(result)
    n_resident = int(result[RESIDENT_PARKING_ZONE_COLUMN].notna().sum())
    in_other_zone = home_zone.index[home_zone.notna().to_numpy() & ~in_resident_zone]
    n_other_zone = int(result["person_id"].isin(in_other_zone).sum())
    n_without_home = int((~result["person_id"].isin(home_zone.index)).sum())
    log.info("%s persons with a resident parking zone: %d/%d (%.1f %%) (%s); %d live in a zone of another type "
             "(no resident exemption, assumption R1); %d have no home activity", _LOG_TAG, n_resident, n_persons,
             _rate(n_resident, n_persons), format_value_counts(result[RESIDENT_PARKING_ZONE_COLUMN]), n_other_zone,
             n_without_home)
    return result


# --------------------------------------------------------------------------- resident parking districts


def attach_parking_districts(activities: pd.DataFrame, locations: gpd.GeoDataFrame,
                             districts: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return ``activities`` with ``parking_district``: the district id of each activity location, NaN outside.

    The resident parking districts (spec Amendment C3) are a second layer next to the fee zones: independent of
    them and free to overlap them, so an activity can lie in a district and in no zone, and the other way round.
    ``locations`` is the locations frame of the plans writer, validated exactly as for :func:`attach_parking_zones`
    (``_located_points``); ``districts`` carries ``district_id`` and the polygons (``braunschweig.parking.
    zones_stage``, EPSG:25832) and ``braunschweig.parking.zones.assign_districts`` makes the point-in-polygon test.
    The new column is of object dtype.

    Raises like :func:`attach_parking_zones` for the locations, and ``ValueError`` for an empty ``districts`` frame:
    the stage never delivers one, and the district rule would silently exempt nobody. A population in which not one
    activity lies in a district is logged as a WARNING, not an error: unlike the zones, whose zero coverage is the
    signature of a broken join, a population may live outside Braunschweig and Goslar. Logs ``[parking] activities in
    resident districts: n/N (x %)`` with the most frequent districts.
    """
    points = _located_points(activities, locations)
    n_activities = len(activities)
    _require_columns(districts, ("district_id",), "districts")
    if len(districts) == 0:
        raise ValueError(f"{_LOG_TAG} no resident parking districts given: the district rule (R2) would exempt nobody, "
                         "which is not a modelled state; the zones stage delivers the committed district layer")
    assigned = parking_zones.assign_districts(gpd.GeoDataFrame(geometry=points), districts)
    result = activities.copy()
    result[PARKING_DISTRICT_COLUMN] = pd.Series(assigned.to_numpy(dtype=object), index=result.index, dtype=object)
    n_inside = int(assigned.notna().sum())
    log.info("%s activities in resident districts: %d/%d (%.1f %%), %d outside every district (no district "
             "exemption); districts: %s", _LOG_TAG, n_inside, n_activities, _rate(n_inside, n_activities),
             n_activities - n_inside, format_value_counts(assigned, TOP_ZONES_LOGGED))
    if n_activities > 0 and n_inside == 0:
        log.warning("%s not one of the %d activities lies in a resident parking district (%d districts): the district "
                    "rule (R2) exempts nobody in this population; if the population should reach Braunschweig or "
                    "Goslar, check the locations and their CRS (locations %s, districts %s)", _LOG_TAG, n_activities,
                    len(districts), locations.crs, districts.crs)
    return result


def attach_resident_districts(persons: pd.DataFrame, activities: pd.DataFrame) -> pd.DataFrame:
    """Return ``persons`` with ``resident_parking_district``: the resident district containing the person's home.

    ``activities`` must already carry ``parking_district`` (:func:`attach_parking_districts`). The home of a person is
    where its ``home`` activities take place; all of them lie at the household home, so they share one district, and a
    person whose home activities lie in different districts (or inside and outside a district) raises. A home outside
    every district, and a person without a home activity, get NaN. Unlike the resident zone (R1) there is no
    tariff to consult: every district is a resident district. The new column is of object dtype. Raises
    ``ValueError`` as well for a missing column and for activities without a single ``home`` purpose (a label mismatch
    that would silently exempt nobody). Logs the resident count per district as a rate.
    """
    _require_columns(persons, ("person_id",), "persons")
    _require_columns(activities, ("person_id", "purpose", PARKING_DISTRICT_COLUMN), "activities")
    homes = _home_activities(activities, PARKING_DISTRICT_COLUMN, "resident district")
    _reject_conflicting_homes(homes, PARKING_DISTRICT_COLUMN, "resident parking districts (or inside and outside a "
                                                              "district)")
    home_district = homes.drop_duplicates("person_id").set_index("person_id")[PARKING_DISTRICT_COLUMN]
    result = persons.copy()
    result[RESIDENT_PARKING_DISTRICT_COLUMN] = pd.Series(result["person_id"].map(home_district).to_numpy(dtype=object),
                                                         index=result.index, dtype=object)
    n_persons = len(result)
    n_resident = int(result[RESIDENT_PARKING_DISTRICT_COLUMN].notna().sum())
    n_without_home = int((~result["person_id"].isin(home_district.index)).sum())
    log.info("%s persons with a resident parking district: %d/%d (%.1f %%) (%s); %d live in no district (no district "
             "exemption, assumption R2); %d have no home activity", _LOG_TAG, n_resident, n_persons,
             _rate(n_resident, n_persons), format_value_counts(result[RESIDENT_PARKING_DISTRICT_COLUMN]),
             n_persons - n_resident - n_without_home, n_without_home)
    return result


# --------------------------------------------------------------------------- free-parking draw


def free_share_by_class(workplace_shares: pd.DataFrame) -> pd.Series:
    """P(free | workplace class): ``share_free_total`` of the class rows, indexed by the class name.

    ``workplace_shares`` is the SrV commute-parking table (``srv2023_commute_parking_by_workplace_class``);
    only its ``level == "class"`` rows are used, the pooled ``total`` row is informational. Raises
    ``ValueError`` for a missing column, a level other than class/total, no class row, a class name that is
    not text (the county keys carry a leading zero, so the column must be read as text), a duplicated class
    or a share that is not a number in [0, 1].
    """
    _require_columns(workplace_shares, WORKPLACE_SHARE_COLUMNS, "workplace shares")
    unknown_levels = sorted({str(level) for level in workplace_shares["level"] if level not in WORKPLACE_SHARE_LEVELS})
    if unknown_levels:
        raise ValueError(f"{_LOG_TAG} workplace shares: level(s) {unknown_levels} are not one of "
                         f"{list(WORKPLACE_SHARE_LEVELS)}")
    rows = workplace_shares[workplace_shares["level"] == LEVEL_CLASS]
    if rows.empty:
        raise ValueError(f"{_LOG_TAG} workplace shares: no level == 'class' row; the free-parking draw needs one "
                         "row per workplace class")
    classes = pd.Series(rows["workplace_class"].to_numpy(dtype=object))
    not_text = [value for value in classes if not isinstance(value, str) or not value.strip()]
    if not_text:
        raise ValueError(f"{_LOG_TAG} workplace shares: workplace_class value(s) {not_text!r} are not text; read "
                         "the column as text (dtype str), because the county keys carry a leading zero (03102)")
    duplicated = sorted(set(classes[classes.duplicated()]))
    if duplicated:
        raise ValueError(f"{_LOG_TAG} workplace shares: duplicate class row(s) for {duplicated}")
    shares = pd.to_numeric(rows[FREE_SHARE_COLUMN], errors="coerce").to_numpy(dtype=float)
    invalid = ~((shares >= 0.0) & (shares <= 1.0))
    if invalid.any():
        found = {workplace_class: value for workplace_class, value, bad
                 in zip(classes, rows[FREE_SHARE_COLUMN], invalid) if bad}
        raise ValueError(f"{_LOG_TAG} workplace shares: {FREE_SHARE_COLUMN} must be a number in [0, 1], found {found}")
    return pd.Series(shares, index=pd.Index(list(classes), name="workplace_class"), name=FREE_SHARE_COLUMN)


def check_workplace_classes(tariffs: pd.DataFrame, free_shares: pd.Series, *, zone_types=None,
                            source: str = "the workplace shares") -> None:
    """Raise unless every tariff zone (of ``zone_types``; every zone by default) has a free share for its class.

    ``free_shares`` is the result of :func:`free_share_by_class`. The error names each missing class with its
    zones: a workplace class the SrV table lacks would leave P(free | class) undefined (assumption A1).
    """
    _require_columns(tariffs, ("zone_id", "zone_type", "workplace_class"), "tariffs")
    rows = tariffs if zone_types is None else tariffs[tariffs["zone_type"].isin(zone_types)]
    missing: dict[str, list[str]] = {}
    for zone_id, workplace_class in zip(rows["zone_id"], rows["workplace_class"]):
        if not isinstance(workplace_class, str) or workplace_class not in free_shares.index:
            missing.setdefault(str(workplace_class), []).append(str(zone_id))
    if missing:
        raise ValueError(f"{_LOG_TAG} workplace class(es) {sorted(missing)} of the tariff zones "
                         f"{dict(sorted(missing.items()))} have no level == 'class' row in {source}, so "
                         "P(free | workplace class) of the free-parking draw (assumption A1) is undefined for them")


def _require_seed(random_seed) -> int:
    if isinstance(random_seed, (bool, np.bool_)) or not isinstance(random_seed, (int, np.integer)):
        raise TypeError(f"{_LOG_TAG} random_seed must be an integer (the pipeline random_seed; the offset "
                        f"{PARKING_FREE_SEED_OFFSET} is added here), got {random_seed!r}")
    return int(random_seed)


def _require_shift(shift) -> float:
    """The sensitivity shift: a difference of shares in [-1, 1]; NaN would silently free nobody."""
    valid = (not isinstance(shift, (bool, np.bool_)) and isinstance(shift, (int, float, np.integer, np.floating))
             and math.isfinite(shift) and -1.0 <= shift <= 1.0)
    if not valid:
        raise ValueError(f"{_LOG_TAG} shift must be a number in [-1, 1] (parking_workplace_free_share_shift, a "
                         f"difference of free-parking shares added before clipping); got {shift!r}")
    return float(shift)


def draw_parking_free(activities: pd.DataFrame, tariffs: pd.DataFrame, workplace_shares: pd.DataFrame,
                      random_seed: int, shift: float = 0.0) -> pd.DataFrame:
    """Return ``activities`` with the boolean ``parking_free``: the free-parking draw of spec 3.4.

    Eligible: an activity whose purpose is in ``ELIGIBLE_PURPOSES`` and whose ``parking_zone`` (attached by
    :func:`attach_parking_zones`) is of a type in ``FREE_DRAW_ZONE_TYPES``. The workplace class of a person
    is the tariff ``workplace_class`` of the zone of the person's FIRST eligible activity (by
    ``activity_index``), and ``p = clip(share_free_total[class] + shift, 0, 1)`` with the class rows of the
    SrV table ``workplace_shares`` (:func:`free_share_by_class`). A person parks free at ALL its eligible
    activities when ``u < p`` and at none otherwise; every other activity is False.

    Random numbers: ONE ``numpy.random.RandomState(random_seed + PARKING_FREE_SEED_OFFSET)`` draws one
    uniform ``u`` per person of the WHOLE frame -- eligible or not -- with the persons sorted by
    ``person_id``. The result therefore does not depend on the row order: the same persons delivered as two
    concatenated chunks, in either order, give identical results. Separate calls on disjoint subsets of the
    persons do NOT reproduce the draw of the whole frame, because each call numbers the persons it is given.
    Drawing for every person rather than for the eligible ones keeps common random numbers across zone
    releases and shift arms: a person keeps its ``u`` when another release makes it eligible, and the free
    set only grows with ``shift``.

    Raises ``TypeError`` for a non-integer ``random_seed`` and ``ValueError`` for a missing column, an
    incomplete or duplicated key, a ``shift`` outside [-1, 1], a zone id without a tariff row, a workplace
    class of a street_paid/resident_zone tariff zone without a class row and an invalid shares table. Logs
    per workplace class ``persons n, p_free, realised`` and one summary line with the rates.
    """
    random_seed = _require_seed(random_seed)
    shift = _require_shift(shift)
    _require_columns(activities, ACTIVITY_KEYS + ("purpose", PARKING_ZONE_COLUMN), "activities")
    _check_activity_keys(activities, "activities")
    zone_types = _zone_attribute(tariffs, "zone_type")
    zone_classes = _zone_attribute(tariffs, "workplace_class")
    free_shares = free_share_by_class(workplace_shares)
    check_workplace_classes(tariffs, free_shares, zone_types=FREE_DRAW_ZONE_TYPES)
    zone_ids = activities[PARKING_ZONE_COLUMN]
    _reject_unknown_zones(zone_ids, zone_types.index, "activities")

    zone_type = zone_ids.map(zone_types)
    purpose_eligible = activities["purpose"].isin(ELIGIBLE_PURPOSES).to_numpy()
    eligible = purpose_eligible & zone_type.isin(FREE_DRAW_ZONE_TYPES).to_numpy()
    on_campus = purpose_eligible & (zone_type == CAMPUS_ZONE_TYPE).to_numpy()

    person_ids = np.sort(activities["person_id"].unique())
    random = np.random.RandomState(random_seed + PARKING_FREE_SEED_OFFSET)
    uniform = pd.Series(random.random_sample(len(person_ids)), index=person_ids)

    first_eligible_zone = (activities.loc[eligible, list(ACTIVITY_KEYS) + [PARKING_ZONE_COLUMN]]
                           .sort_values(list(ACTIVITY_KEYS), kind="mergesort")
                           .drop_duplicates("person_id", keep="first")
                           .set_index("person_id")[PARKING_ZONE_COLUMN])
    person_class = first_eligible_zone.map(zone_classes)
    probability = (person_class.map(free_shares) + shift).clip(lower=0.0, upper=1.0)
    person_free = uniform.reindex(probability.index) < probability

    free_by_activity = activities.loc[eligible, "person_id"].map(person_free)
    if free_by_activity.isna().any():  # impossible by construction; guards the bool cast, where NaN is True
        raise RuntimeError(f"{_LOG_TAG} free-parking draw: {int(free_by_activity.isna().sum())} eligible activities "
                           "lost their person's draw")
    parking_free = np.zeros(len(activities), dtype=bool)
    parking_free[eligible] = free_by_activity.to_numpy(dtype=bool)
    result = activities.copy()
    result[PARKING_FREE_COLUMN] = pd.Series(parking_free, index=result.index, dtype=bool)

    for workplace_class in sorted(person_class.unique()):
        members = person_class.index[person_class.to_numpy(dtype=object) == workplace_class]
        share = float(free_shares[workplace_class])
        log.info("%s free-parking draw, workplace class %s: persons %d, share_free_total %.4f, p_free %.4f, "
                 "realised %.4f", _LOG_TAG, workplace_class, len(members), share,
                 min(max(share + shift, 0.0), 1.0), float(person_free[members].mean()))
    n_persons, n_eligible_persons = len(person_ids), len(probability)
    n_free_persons = int(person_free.sum())
    n_eligible = int(eligible.sum())
    log.info("%s free-parking draw (RandomState(random_seed %d + %d), shift %+.3f): %d/%d persons (%.1f %%) have a "
             "work/education activity in a street_paid or resident_zone zone, %d of them (%.1f %%) park free; "
             "parkingFree on %d of %d eligible activities; %d work/education activities in campus zones are never "
             "drawn (assumption C1)", _LOG_TAG, random_seed, PARKING_FREE_SEED_OFFSET, shift, n_eligible_persons,
             n_persons, _rate(n_eligible_persons, n_persons), n_free_persons, _rate(n_free_persons, n_eligible_persons),
             int(parking_free.sum()), n_eligible, int(on_campus.sum()))
    return result
