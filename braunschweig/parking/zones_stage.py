"""synpp stage ``braunschweig.parking.zones_stage``: the parking cost zone release (issue #249).

Release semantics. The zone-based parking costs (design spec 2026-09-28, sections 3, 4 and 5.5) read ONE
release of six inputs, each configured relative to ``data_path``:

* the zone polygons (``parking_zones_path``; Data Registry ``parking_zones_2026``),
* the tariff table (``parking_tariffs_path``; ``parking_tariffs_2026``),
* the coverage register (``parking_coverage_register_path``; ``parking_coverage_register_2026``),
* the SrV 2023 free-parking shares by workplace class (``parking_workplace_shares_path``;
  ``srv2023_commute_parking_by_workplace_class``),
* the resident parking districts (``parking_resident_districts_path``; ``parking_resident_districts_2026``; parking
  cost zones v2, spec Amendment C3), a second layer next to the fee zones,
* the parking garages (``parking_garages_path``; ``parking_garages_2026``; parking cost zones v2, spec Amendment E1):
  points with their own tariffs, the distance-weighted garage options of a stay.

The five committed parking files are curated together and the SrV table is the committed aggregate that
gives P(free | workplace class) of the free-parking draw. They are only meaningful together: a polygon
without its tariff row, a tariff row in a municipality the register does not mark ``zoned``, a district in a
municipality the register does not know or a workplace class without an SrV share would each misprice stays
silently. This stage therefore loads and validates them as one unit and raises on any inconsistency:

1. polygons: loaded, reprojected to EPSG:25832 and validated for provenance, unique ids, valid polygons and pairwise
   overlap (``braunschweig.parking.zones``); strictly, ``max_repairs=0``: a polygon that would need the loader's repair
   stops the stage, because the committed release is valid as stored;
2. tariffs: typed and validated per zone type (``validate_tariffs``, the synthetic test-set marker allowed),
   then every row converted by ``braunschweig.parking.tariff_export.tariff_row_to_zone``, the stricter
   per-row contract of the tariff model the Java cost model reads;
3. exactly one tariff row per polygon (``cross_validate``);
4. the coverage register against the tariffs (``validate_coverage_register``: every ``zoned`` municipality
   owns a tariff row and every tariff row lies in a ``zoned`` municipality);
5. the SrV table: a class row with a share in [0, 1] for the workplace class of every tariff row
   (``braunschweig.parking.attach.free_share_by_class`` and ``check_workplace_classes``);
6. the resident districts: valid as stored (never repaired), unique ids, no overlap
   (``braunschweig.parking.zones.load_resident_districts``), and every district municipality has a status row of
   the coverage register (``validate_district_municipalities``; a district in a municipality that is not ``zoned`` is
   inert and logged as a WARNING);
7. the garages: loaded and validated as stored (``braunschweig.parking.garages.load_garages`` and ``validate_garages``:
   the tariff structures, the assumptions each row names, the position inside the ZGB extent). The loader logs how many
   garages are priced and how many rest on each assumption; an unpriced garage stays listed and is no option.

Rows carrying the synthetic test-set marker ``braunschweig.parking.zones.FIXTURE_MARKER`` (the fixtures under
``tests/fixtures/parking``; a garage carries it as its ``geometry_method``) are accepted, because the tests run this stage
on them, but logged as ONE WARNING naming the zones, the districts and the garages, so a fixture release cannot price a
model run unnoticed.

Output: ``{"zones", "tariffs", "workplace_shares", "coverage_register", "districts", "garages", "sources"}``.
``workplace_shares`` is the SrV table as read (class and total rows, ``workplace_class`` as text). ``districts`` is
the district layer and ``garages`` the garage dataset (priced and unpriced rows), both in EPSG:25832. ``sources`` lists
the six inputs in the order above as ``{"source_id": <Data
Registry dataset id>, "path": <the configured path relative to data_path, POSIX>, "sha256": <LF-normalised content
hash, tariff_export.content_sha256>}``; the preparation stage copies it into the tariff model JSON, so every prepared
scenario names the exact release it was priced with, identically on every machine. Consumers:
``braunschweig.matsim.scenario.population`` (through ``braunschweig.parking.attach``) and
``braunschweig.matsim.simulation.prepare`` (tariff model), both of which declare this stage only when
``parking_zones_enabled`` is true. With the flag off nothing is declared or read, the districts included. CRS of the
returned polygons: EPSG:25832.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import logging
from pathlib import Path, PurePosixPath, PureWindowsPath

import pandas as pd

from braunschweig.parking import attach, tariff_export
from braunschweig.parking import garages as parking_garages
from braunschweig.parking import zones as parking_zones

log = logging.getLogger(__name__)

_LOG_TAG = "[parking]"

KEY_DATA_PATH = "data_path"
KEY_ZONES_PATH = "parking_zones_path"
KEY_TARIFFS_PATH = "parking_tariffs_path"
KEY_COVERAGE_REGISTER_PATH = "parking_coverage_register_path"
KEY_WORKPLACE_SHARES_PATH = "parking_workplace_shares_path"
KEY_RESIDENT_DISTRICTS_PATH = "parking_resident_districts_path"
KEY_GARAGES_PATH = "parking_garages_path"

#: The release in the order of ``sources``: (config key, default path relative to data_path (spec 5.5),
#: Data Registry dataset id).
RELEASE_INPUTS = (
    (KEY_ZONES_PATH, "braunschweig/parking/parking_zones_2026.geojson", "parking_zones_2026"),
    (KEY_TARIFFS_PATH, "braunschweig/parking/parking_tariffs_2026.csv", "parking_tariffs_2026"),
    (KEY_COVERAGE_REGISTER_PATH, "braunschweig/parking/parking_coverage_register_2026.csv",
     "parking_coverage_register_2026"),
    (KEY_WORKPLACE_SHARES_PATH, "braunschweig/srv/srv2023_commute_parking_by_workplace_class.csv",
     "srv2023_commute_parking_by_workplace_class"),
    (KEY_RESIDENT_DISTRICTS_PATH, "braunschweig/parking/parking_resident_districts_2026.geojson",
     "parking_resident_districts_2026"),
    (KEY_GARAGES_PATH, "braunschweig/parking/parking_garages_2026.geojson", "parking_garages_2026"),
)

#: Modules whose code decides the content or the validation of the release; ``validate()`` hashes their
#: source (synpp hashes only this module's own). ``parking_zones`` loads and validates the four parking
#: files (the districts included), ``parking_garages`` loads and validates the garage dataset, ``attach`` validates the SrV
#: table, ``tariff_export`` converts every tariff row and hashes the inputs.
_HELPER_MODULES = (parking_zones, parking_garages, attach, tariff_export)
#: Reached through ``tariff_export`` rather than imported here: ``tariff_row_to_zone`` builds a
#: ``cost.ZoneTariff``, whose construction IS the per-row validation. Hashed by dotted name, like the deferred
#: helpers of ``braunschweig.matsim.simulation.prepare``. ``free_draw_options`` is imported by ``attach`` (the draw
#: options of the free-parking draw, not part of the release itself) and hashed with it.
_DEFERRED_HELPER_MODULE_NAMES = ("braunschweig.parking.cost", "braunschweig.parking.free_draw_options")


def _require_relative_posix_path(key: str, value, default: str) -> str:
    """The configured path text, recorded as ``sources[].path``: non-empty, relative, '/'-separated.

    The preparation stage refuses a source path with backslashes, and an absolute path would make the
    tariff model differ between machines, so both fail here, at configure time, before any stage runs.
    """
    if (not isinstance(value, str) or not value.strip() or "\\" in value or PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).drive):
        raise ValueError(f"{_LOG_TAG} {key} must be a non-empty path relative to data_path with '/' separators "
                         f"(default {default!r}), got {value!r}; it is recorded as the source path of the "
                         "parking tariff model")
    return value


def configure(context):
    context.config(KEY_DATA_PATH)
    for key, default, _ in RELEASE_INPUTS:
        _require_relative_posix_path(key, context.config(key, default), default)


def _release_files(context) -> list[tuple[str, str, str, Path]]:
    """(config key, dataset id, configured relative path, file) per release input in ``sources`` order.

    Raises ``FileNotFoundError`` naming the config key for a missing file.
    """
    data_path = Path(context.config(KEY_DATA_PATH))
    files = []
    for key, _, source_id in RELEASE_INPUTS:
        relative = context.config(key)
        path = data_path / relative
        if not path.is_file():
            raise FileNotFoundError(f"{_LOG_TAG} {key} = {relative!r} does not exist under data_path "
                                    f"{str(data_path)!r} ({path}); the committed input is described in "
                                    f"docs/registry/data/{source_id}.yml")
        files.append((key, source_id, relative, path))
    return files


def validate(context):
    """Cache token: LF-normalised sha256 of the five release files and the source of every module that decides
    the release (``_HELPER_MODULES``, ``_DEFERRED_HELPER_MODULE_NAMES``).

    A missing file raises ``FileNotFoundError`` naming its config key. A deferred module that cannot be
    imported or read raises ``RuntimeError`` rather than being skipped, because skipping it would silently
    reuse a stale release. The LF normalisation gives a CRLF and an LF checkout of the same files one token.
    """
    digest = hashlib.sha256()
    for _, source_id, _, path in _release_files(context):
        digest.update(f"{source_id}={tariff_export.content_sha256(path)};".encode("ascii"))
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for module_name in _DEFERRED_HELPER_MODULE_NAMES:
        try:
            deferred_source = inspect.getsource(importlib.import_module(module_name))
        except Exception as error:
            raise RuntimeError(
                f"braunschweig.parking.zones_stage validate(): cannot hash the deferred helper module "
                f"{module_name!r} ({type(error).__name__}: {error}); it must not be skipped, because skipping it "
                "would silently reuse a stale parking zone release.") from error
        digest.update(deferred_source.encode("utf-8"))
    return digest.hexdigest()


def load_workplace_shares(path) -> pd.DataFrame:
    """The SrV commute-parking table as committed: '#' documentation lines skipped, ``workplace_class`` and
    ``level`` as text (the county keys carry a leading zero, 03102), the numeric columns as numbers."""
    shares = pd.read_csv(path, comment="#", dtype={"workplace_class": str, "level": str})
    log.info("%s loaded %d workplace share rows from %s", _LOG_TAG, len(shares), path)
    return shares


def _check_tariff_model_rows(tariffs: pd.DataFrame) -> None:
    """Convert every row with ``tariff_export.tariff_row_to_zone`` (the per-row contract of the tariff model,
    stricter than ``validate_tariffs``); raise ``ValueError`` listing every row the conversion rejects."""
    problems = []
    for row in tariffs.to_dict(orient="records"):
        try:
            tariff_export.tariff_row_to_zone(row)
        except ValueError as error:
            problems.append(f"{row.get('zone_id')!r}: {error}")
    if problems:
        raise ValueError(f"{_LOG_TAG} tariff rows the parking tariff model rejects:\n  " + "\n  ".join(problems))


def _fixture_marked_zones(zone_polygons: pd.DataFrame, tariffs: pd.DataFrame) -> list[str]:
    """Zone ids whose tariff or polygon provenance carries the synthetic test-set marker."""
    marked = set()
    for frame, columns in ((tariffs, ("source_url", "fee_window_source")),
                           (zone_polygons, ("geometry_source", "source_url"))):
        for column in columns:
            if column in frame.columns:
                marked.update(frame.loc[frame[column].isin([parking_zones.FIXTURE_MARKER]), "zone_id"])
    return sorted(marked)


def _fixture_marked_garages(garages: pd.DataFrame) -> list[str]:
    """Garage ids whose geometry method carries the synthetic test-set marker."""
    return sorted(garages.loc[garages["geometry_method"].isin([parking_zones.FIXTURE_MARKER]), "garage_id"])


def _fixture_marked_districts(districts: pd.DataFrame) -> list[str]:
    """District ids whose provenance carries the synthetic test-set marker."""
    marked = set()
    for column in ("geometry_source", "source_url"):
        marked.update(districts.loc[districts[column].isin([parking_zones.FIXTURE_MARKER]), "district_id"])
    return sorted(marked)


def execute(context):
    files = _release_files(context)
    paths = {key: path for key, _, _, path in files}

    # Strict: the committed release is valid as stored, so a polygon that would need the loader's repair means the file
    # changed (a repair silently replaces the polygon the file states); scripts/validate_parking_zones.py is as strict.
    zone_polygons = parking_zones.load_zone_polygons(paths[KEY_ZONES_PATH], max_repairs=0)
    # load_zone_polygons validates as well; repeated so that the release contract does not rest on the
    # loader's internals.
    parking_zones.validate_zone_polygons(zone_polygons)
    tariffs = parking_zones.load_tariffs(paths[KEY_TARIFFS_PATH])
    parking_zones.validate_tariffs(tariffs, allow_fixture_marker=True)
    parking_zones.cross_validate(zone_polygons, tariffs)
    _check_tariff_model_rows(tariffs)
    coverage_register = parking_zones.load_coverage_register(paths[KEY_COVERAGE_REGISTER_PATH])
    parking_zones.validate_coverage_register(coverage_register, tariffs)
    workplace_shares = load_workplace_shares(paths[KEY_WORKPLACE_SHARES_PATH])
    free_shares = attach.free_share_by_class(workplace_shares)
    attach.check_workplace_classes(tariffs, free_shares, source=str(paths[KEY_WORKPLACE_SHARES_PATH]))
    # Valid as stored and free of overlaps (load_resident_districts), and every municipality known to the register.
    districts = parking_zones.load_resident_districts(paths[KEY_RESIDENT_DISTRICTS_PATH])
    parking_zones.validate_district_municipalities(districts, coverage_register)
    # Loaded in EPSG:25832 and validated as stored; the loader logs the priced share and the assumption counts. Unpriced
    # garages stay listed (spec E1) and are left out of the tariff model by tariff_export.garage_entries.
    garages = parking_garages.load_garages(paths[KEY_GARAGES_PATH])
    parking_garages.validate_garages(garages)

    marked = _fixture_marked_zones(zone_polygons, tariffs)
    marked_districts = _fixture_marked_districts(districts)
    marked_garages = _fixture_marked_garages(garages)
    if marked or marked_districts or marked_garages:
        log.warning("%s the release carries the synthetic test-set marker %r in %d of %d zone(s) %s, %d of %d "
                    "resident district(s) %s and %d of %d garage(s) %s: this is TEST data (tests/fixtures/parking pins "
                    "arithmetic, not truth) and must never price a model run", _LOG_TAG, parking_zones.FIXTURE_MARKER,
                    len(marked), len(tariffs), marked, len(marked_districts), len(districts), marked_districts,
                    len(marked_garages), len(garages), marked_garages)

    sources = [{"source_id": source_id, "path": relative, "sha256": tariff_export.content_sha256(path)}
               for _, source_id, relative, path in files]
    log.info("%s zone release: %d zones (%s) in %d municipalities; coverage register %d rows (%s); free shares "
             "for %d workplace classes; resident districts %d (%s); garages %d (priced %d); sources %s", _LOG_TAG,
             len(zone_polygons),
             attach.format_value_counts(tariffs["zone_type"], by_value=True), tariffs["municipality_ags"].nunique(),
             len(coverage_register), attach.format_value_counts(coverage_register["status"], by_value=True),
             len(free_shares), len(districts), attach.format_value_counts(districts["municipality_ags"], by_value=True),
             len(garages), int(garages["priced"].sum()),
             ", ".join(f"{source['source_id']} {source['sha256'][:12]}" for source in sources))
    return {
        "zones": zone_polygons,
        "tariffs": tariffs,
        "workplace_shares": workplace_shares,
        "coverage_register": coverage_register,
        "districts": districts,
        "garages": garages,
        "sources": sources,
    }
