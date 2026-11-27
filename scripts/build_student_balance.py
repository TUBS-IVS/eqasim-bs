"""Build the student origin-destination table, its balance, level table and calibration (#140, Phase 2).

The one CLI of Phase 2 of the student layer: it reads every input, runs the pure modules
(``student_geometry``, ``student_od_table``, ``student_commuter_weights``, ``student_level_table``,
``student_control_evidence``) and writes the run artifacts and the committed tables, each with a
``#`` provenance header (inputs with sha256, script, ``--source-commit``, date, weight rule and its
anchors, the weight triple, the mover share and its source, ``max_commute_km`` and a
``# Diagnostics:`` line with the counted fallbacks and exclusions of the builders).

Two sub-commands share the preparation (:func:`prepare`):

``calibrate``  searches the weight triple (and, when stage one ends at the short bound, the mover
               share) against the SrV destination reference and writes the calibration, fit and
               context tables, plus a configuration snippet for a decided stage only.
``balance``    builds T for ONE triple and mover share under the enrollment of the model semester
               (``--sites``) and once more under that of the census-year semester
               (``--sites-census-year``, same sites, same weights and movers), balances it, builds
               the level table (total of the balance, split over the Kreise by the census, ages and
               sexes by the MiD joint shares), the employed pre-check and the level gate against
               SrV. Exit code 0 whatever the gate says: the gate is a reported result, the owner
               decides on it.

Committed inputs default to their repository paths. Local-only inputs are required arguments
(``--sites``, ``--sites-census-year``, ``--vg250``, ``--zensus-students``, ``--zensus-employed``;
optional ``--mikrozensus-school-distance``): never a default, and only their file name and sha256
enter a header (no absolute path). Each site table carries ONE semester in its column ``semester``;
the three institutions outside the LSN statistics get their enrollment of that semester from
``OUTSIDE_LSN_STUDENTS`` of ``scripts/seed_lsn_university_sites.py`` (a semester without an entry
raises; a figure of an earlier semester is a counted fallback).

The tables count HEADS, never days: no day factor is applied anywhere. The level table and the
balance header only carry the day factor (and its two SrV shares) for Phases 3 and 4.

Usage (eqasim env, from a worktree):
    python scripts/build_student_balance.py calibrate --sites <lsn_university_sites.csv> \
        --vg250 <vg250 zip> --source-commit <sha> --out-dir <run artifact dir>
    python scripts/build_student_balance.py balance --sites ... --sites-census-year <csv> --vg250 ... \
        --source-commit <sha> --weights s,m,l --mover-share x --weights-source {calibrated,owner} \
        --zensus-students <csv> --zensus-employed <csv> \
        (--label <name> --artifact-dir <dir> | --final --out-dir <eqasim-data root> --artifact-dir <dir>)
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import logging
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig import synpp_tokens  # noqa: E402
from braunschweig.calibration import srv_student_reference as SR  # noqa: E402
from braunschweig.calibration import student_commuter_weights as W  # noqa: E402
from braunschweig.calibration import student_control_evidence as E  # noqa: E402
from braunschweig.calibration import student_level_table as LV  # noqa: E402
from braunschweig.calibration.mid_student_reference import (  # noqa: E402
    AGE_SEX_TABLE, DISTANCE_MODE_TABLE, REGION_GERMANY, SHARE_20_29_TABLE)
from braunschweig.calibration.srv_distance_targets import ZGB_KREISE  # noqa: E402
from braunschweig.calibration.srv_participation_universe import (  # noqa: E402
    REGION_CODE, UNSURVEYED_KREISE)
from braunschweig.data.education import student_geometry as G  # noqa: E402
from braunschweig.data.education import student_od_table as T  # noqa: E402
from braunschweig.popsim.zensus_employment_age import read_kreis_employed_by_age  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("build_student_balance")


def _load_script(name: str):
    """A sibling seed script as a module (never registered in ``sys.modules``): the owner of a constant."""
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CHE_SEED = _load_script("seed_che_student_origins")
_NDS_SEED = _load_script("seed_nds_hochschulen")
_LSN_SEED = _load_script("seed_lsn_university_sites")

DATA = REPO / "eqasim-data" / "data" / "braunschweig"
CHE_DEFAULT = _CHE_SEED.OUTPUT_PATH
SRV_DEFAULTS = {"destination": DATA / "srv" / SR.DESTINATION_TABLE,
                "srv_share": DATA / "srv" / SR.STUDENT_SHARE_TABLE,
                "srv_parameters": DATA / "srv" / SR.PARAMETER_TABLE,
                "srv_distance": DATA / "srv" / SR.EDUCATION_DISTANCE_TABLE}
MID_DEFAULTS = {"mid_share": DATA / "mid" / SHARE_20_29_TABLE, "mid_distance": DATA / "mid" / DISTANCE_MODE_TABLE,
                "mid_age_sex": DATA / "mid" / AGE_SEX_TABLE}
POPULATION_DEFAULT = DATA / "popsim" / "lsn_population_20_29_by_kreis.csv"
POPULATION_BASE_DATE_DEFAULT = "2022-12-31"
POPULATION_MODEL_DATE_DEFAULT = "2025-12-31"
# The Phase 1 measurement of the model today (``model_students_expanded_persons`` per Kreis).
MODEL_LEVEL_DEFAULT = (REPO / "docs" / "runs" / "artifacts" / "student-measurement-popsim100-20261120-20261126"
                       / "student_level_by_kreis.csv")

SCHOOLS_SUBDIR = Path("data") / "braunschweig" / "schools"
POPSIM_SUBDIR = Path("data") / "braunschweig" / "popsim"

# Output file names.
CALIBRATION_FILE = "student_commuter_weight_calibration.csv"
FIT_FILE = "student_commuter_weight_fit.csv"
CONTEXT_FILE = "student_commuter_weight_context.csv"
SNIPPET_FILE = "student_commuter_weight_snippet.yml"
OD_FILE = "student_od_table.csv"
BY_SITE_FILE = "student_balance_by_site.csv"
OUT_COMMUTERS_FILE = "student_balance_out_commuters.csv"
IN_COMMUTERS_FILE = "student_balance_in_commuters_by_origin.csv"
LEVEL_FILE = "student_level_table.csv"
GATE_FILE = "student_level_gate.csv"
PRECHECK_FILE = "student_employed_target_precheck.csv"
CONTEXT_COLUMNS = ["source", "code", "band", "share", "n_unweighted"]

# ASSUMPTION (design B3 step 7): the site Kreise whose movers may live in Braunschweig: Salzgitter,
# Wolfsburg and Wolfenbuettel. A CLI argument (``--mover-site-kreise``), never fixed in the modules.
MOVER_SITE_KREISE_DEFAULT = "03102,03103,03158"
WEIGHTS_SOURCES = ("calibrated", "owner")
SEPARATOR_DISTANCE_LEARNING = ";"
VG250_KREIS_LAYER = "vg250_krs"
LAND_GF = G.LAND_GF
# The key of the SrV parameter table's header that carries the share of the education-leg definition.
EDUCATION_LEG_KEY = "education_leg_share_control_definition"

# Required columns of every input, checked before any computation.
SITE_COLUMNS = ["institution", "site_ags8", "site_kreis_ars5", "scope", "students_persons", "semester"]
DESTINATION_COLUMNS = ["home_group", "destination_group", "share", "n_effective", "noise_floor_tvd_95",
                       "n_unweighted", "se_share"]
SRV_SHARE_COLUMNS = [*E.SRV_SHARE_COLUMNS, "level", "se_student_share_adults"]
PARAMETER_COLUMNS = ["group", "parameter", "base", "label", "share"]
SRV_BANDS = ["0_5", "5_10", "10_25", "25_50", "50_100", "100_plus"]
SRV_DISTANCE_COLUMNS = ["code", "level", "n_persons", *("share_" + band for band in SRV_BANDS)]
MID_SHARE_COLUMNS = ["region", "share_students", "share_pupils_and_students"]
MID_DISTANCE_COLUMNS = ["distance_band", "mode", "n_unweighted", "share_of_all"]
MID_AGE_SEX_COLUMNS = ["area", "sex", "age_group", "share_of_adult_students"]
MODEL_LEVEL_COLUMNS = ["code", "level", "model_students_expanded_persons"]
EMPLOYED_COLUMNS = ["region", "age_class", "sex", "employed_persons"]
# Mikrozensus row ``hochschule`` (percent) and the band labels of the context table.
MIKROZENSUS_ROW = "hochschule"
MIKROZENSUS_BANDS = {"lt5": "0_5", "b5_10": "5_10", "b10_25": "10_25", "b25_50": "25_50", "ge50": "50_plus"}
MIKROZENSUS_COLUMNS = ["school_type", *MIKROZENSUS_BANDS]
SHARE_TOLERANCE = 1e-6
# The model level table is written with rounded counts; its pooled row and the sum of its Kreis rows may
# differ by this many persons (rounding only, no modelling meaning).
POOLED_MODEL_TOLERANCE_PERSONS = 0.5


# --------------------------------------------------------------------------------------------- files
_HASHES: dict = {}


def _sha256(path: Path, committed_text: bool) -> str:
    """sha256 of ``path``: of a committed text file as git stores it (CRLF read as LF,
    :func:`braunschweig.synpp_tokens.text_file_sha256`), so a Windows and a Linux checkout of one
    commit give one hash; of a local-only or binary file over its raw bytes (``file_sha256``)."""
    key = (str(Path(path).resolve()), committed_text)
    if key not in _HASHES:
        _HASHES[key] = (synpp_tokens.text_file_sha256(path) if committed_text
                        else synpp_tokens.file_sha256(path))
    return _HASHES[key]


def _display_path(path: Path, local_only: bool) -> str:
    """Repository-relative path; the file name only for a local-only file or one outside the repository."""
    path = Path(path)
    if local_only:
        return path.name
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.name


def _input_lines(inputs: list) -> list:
    """``inputs``: ``(label, path, local_only)``; the header names the file, its sha256 and how it was hashed.

    Every committed input of this CLI is a text table or script: its hash is taken with CRLF read as
    LF (as git stores it). Local-only inputs (the LSN site table, the Zensus tables, the VG250 zip) are
    hashed over their raw bytes.
    """
    return ["#   %s: %s%s sha256 %s (%s)" % (
                label, _display_path(path, local_only), " (local-only)" if local_only else "",
                _sha256(path, committed_text=not local_only),
                "raw bytes" if local_only else "committed text, CRLF read as LF as git stores it")
            for label, path, local_only in inputs]


def _read_csv(path, required: list, what: str, text_columns=(), **kwargs) -> pd.DataFrame:
    """Read ``path`` (``#`` header lines skipped); fail early on a missing file or required column."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("%s: file not found: %s" % (what, path))
    dtype = {column: str for column in text_columns}
    header = pd.read_csv(path, comment="#", nrows=0, dtype=dtype, **kwargs)
    missing = [column for column in required if column not in header.columns]
    if missing:
        raise ValueError("%s (%s): missing required column(s) %s; found %s"
                         % (what, path.name, missing, list(header.columns)))
    return pd.read_csv(path, comment="#", dtype=dtype, **kwargs)


def read_header_value(path, key: str) -> str:
    """The value of ``key=value`` anywhere in the leading ``#`` lines of ``path``.

    A value ends at a comma, a semicolon, a closing bracket or a space. List-valued keys such as
    ``mover_site_kreise`` are written for the reader and are not read back by this function. Raises ``ValueError`` when
    the key is absent or found more than once (the value would be ambiguous).
    """
    pattern = re.compile(r"(?:^|[\s,;(:])" + re.escape(key) + r"=([^\s,;)\]]+)")
    values = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            values.extend(pattern.findall(line))
    if not values:
        raise ValueError("%s: header key %r is absent" % (Path(path).name, key))
    if len(values) > 1:
        raise ValueError("%s: header key %r found more than once (%s)" % (Path(path).name, key, values))
    return values[0]


def _json_default(value):
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return str(value)


def _diagnostics_line(diagnostics: dict) -> str:
    return "# Diagnostics: " + json.dumps(diagnostics, default=_json_default, sort_keys=False)


def _write(df: pd.DataFrame, path: Path, header: list) -> None:
    """Write the provenance-headed CSV with LF line endings and ``%.10g`` floats."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        logger.warning("overwriting %s", path)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        for line in header:
            handle.write(line + "\n")
        df.to_csv(handle, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("wrote %s (%d rows)", path, len(df))


def _format_weights(weights) -> str:
    return "short=%.10g middle=%.10g long=%.10g" % tuple(float(value) for value in weights)


# --------------------------------------------------------------------------------------- geometry
def kreis_of_points(kreise, points: pd.DataFrame) -> dict:
    """``destination_id`` -> ``ars5`` of the Kreis polygon that contains the projected point.

    ``kreise``: a GeoDataFrame with ``ars5`` and ``geometry`` (one polygon per Kreis) in the CRS
    of ``points`` (``destination_id, x, y``). A point in no polygon, or in more than one, raises.
    """
    import geopandas as gpd

    located = gpd.GeoDataFrame(points[["destination_id"]].reset_index(drop=True),
                               geometry=gpd.points_from_xy(points["x"], points["y"]), crs=kreise.crs)
    joined = gpd.sjoin(located, kreise[["ars5", "geometry"]], how="left", predicate="within")
    outside = joined.loc[joined["ars5"].isna(), "destination_id"].tolist()
    if outside:
        raise ValueError("curated site(s) %s lie in no VG250 Kreis polygon (check lon/lat)" % outside)
    twice = joined.loc[joined["destination_id"].duplicated(), "destination_id"].tolist()
    if twice:
        raise ValueError("curated site(s) %s lie in more than one VG250 Kreis polygon" % twice)
    return dict(zip(joined["destination_id"], joined["ars5"].astype(str)))


def locate_site_kreise(vg250_zip, points: pd.DataFrame) -> dict:
    """``destination_id`` -> ``ars5`` from the VG250 Kreis layer (``vg250_krs``, land rows) of the archive."""
    import geopandas as gpd

    from braunschweig.analysis.spatial import VG250_INNER
    from braunschweig.analysis.synthesis.work_participation_by_kreis import kreis_ars5

    with zipfile.ZipFile(vg250_zip) as archive, archive.open(VG250_INNER) as handle:
        layer = gpd.read_file(handle, layer=VG250_KREIS_LAYER)
    key = "ARS" if "ARS" in layer.columns else "AGS" if "AGS" in layer.columns else None
    if key is None or "geometry" not in layer.columns:
        raise ValueError("%s layer %s: needs an ARS or AGS column and a geometry; found %s"
                         % (Path(vg250_zip).name, VG250_KREIS_LAYER, list(layer.columns)))
    if layer.crs is None or layer.crs.to_epsg() != int(G.CRS.split(":")[1]):
        raise ValueError("%s layer %s: CRS is %s, expected %s"
                         % (Path(vg250_zip).name, VG250_KREIS_LAYER, layer.crs, G.CRS))
    if "GF" in layer.columns:
        layer = layer[layer["GF"] == LAND_GF]
    layer = layer.assign(ars5=kreis_ars5(layer[key]).to_numpy())
    return kreis_of_points(layer.dissolve(by="ars5").reset_index(), points)


# ------------------------------------------------------------------------------------- preparation
def _csv_list(text: str, separator: str = ",") -> list:
    return [item.strip() for item in text.split(separator) if item.strip()]


def _read_sites(path) -> tuple:
    """``(sites, semester)``: the LSN site table, validated (unique rows, consistent Kreis codes, known
    scope, numeric counts) and the ONE semester of its column ``semester``."""
    text_columns = [column for column in SITE_COLUMNS if column != "students_persons"]
    sites = _read_csv(path, SITE_COLUMNS, "LSN site table", text_columns=text_columns)
    ags8 = sites["site_ags8"]
    if sites[SITE_COLUMNS].isna().any().any():
        raise ValueError("LSN site table (%s): missing value(s) in %s" % (Path(path).name, SITE_COLUMNS))
    bad = ~ags8.str.fullmatch(r"\d{8}")
    if bad.any():
        raise ValueError("LSN site table: column 'site_ags8' needs 8-digit codes, got %s" % ags8[bad].tolist()[:5])
    mismatch = sites["site_kreis_ars5"] != ags8.str[:5]
    if mismatch.any():
        raise ValueError("LSN site table: 'site_kreis_ars5' differs from the first five digits of 'site_ags8' "
                         "in %s" % sites.loc[mismatch, ["institution", "site_ags8"]].values.tolist()[:5])
    unknown = sorted(set(sites["scope"]) - set(T.SCOPES))
    if unknown:
        raise ValueError("LSN site table: column 'scope' has unknown value(s) %s" % unknown)
    persons = pd.to_numeric(sites["students_persons"], errors="coerce")
    if not (np.isfinite(persons) & (persons >= 0)).all():
        raise ValueError("LSN site table: column 'students_persons' needs non-negative numbers")
    duplicated = sites.duplicated(["institution", "site_ags8"], keep=False)
    if duplicated.any():
        raise ValueError("LSN site table: duplicated (institution, site_ags8) %s"
                         % sites.loc[duplicated, ["institution", "site_ags8"]].drop_duplicates().values.tolist()[:5])
    semesters = sorted(set(sites["semester"]))
    if len(semesters) != 1 or not semesters[0].strip():
        raise ValueError("LSN site table (%s): column 'semester' needs exactly one non-empty value, found %s"
                         % (Path(path).name, semesters))
    return sites.assign(students_persons=persons.astype(float)), semesters[0]


def _che_name_of_lsn_institution(institutions) -> dict:
    inverse = {}
    for che_name, lsn_name in T.INSTITUTION_CROSSWALK.items():
        if lsn_name in inverse:
            raise ValueError("INSTITUTION_CROSSWALK: LSN name %r is the target of two CHE names" % lsn_name)
        inverse[lsn_name] = che_name
    unknown = sorted(set(institutions) - set(inverse))
    if unknown:
        raise ValueError("LSN site table: institution(s) %s have no entry in "
                         "student_od_table.INSTITUTION_CROSSWALK (column 'institution')" % unknown)
    return inverse


def _outside_lsn_enrollment(semester: str, what: str) -> tuple:
    """``(students per institution name, summary)`` of the institutions outside the LSN statistics for the
    site table's ``semester``, from ``OUTSIDE_LSN_STUDENTS`` of ``scripts/seed_lsn_university_sites.py``.

    PRIMARY is a figure of ``semester`` itself, FALLBACK a figure of the institution's last published
    winter semester (the seed script names the semester of each figure). Raises ``ValueError`` naming
    ``semester`` when the seed script has no entry for it or the entry misses an institution.
    """
    table = _LSN_SEED.OUTSIDE_LSN_STUDENTS
    if semester not in table:
        raise ValueError("%s: semester %r of the site table has no entry in OUTSIDE_LSN_STUDENTS of "
                         "scripts/seed_lsn_university_sites.py (entries: %s)" % (what, semester, sorted(table)))
    entry = table[semester]
    names = list(T.OUTSIDE_LSN_CROSSWALK.values())
    absent = [name for name in names if name not in entry]
    if absent:
        raise ValueError("%s: OUTSIDE_LSN_STUDENTS[%r] of scripts/seed_lsn_university_sites.py misses "
                         "institution(s) %s" % (what, semester, absent))
    for name in names:
        students = entry[name][0]
        if not (np.isfinite(students) and students > 0):
            raise ValueError("%s: OUTSIDE_LSN_STUDENTS[%r][%r] needs a positive student count, got %r"
                             % (what, semester, name, students))
    primary = sum(1 for name in names if entry[name][1] == semester)
    fallback = len(names) - primary
    summary = {"primary": "primary %d/%d" % (primary, len(names)),
               "fallback": "fallback %d/%d" % (fallback, len(names)),
               "institutions": {name: [int(entry[name][0]), entry[name][1]] for name in names}}
    (logger.warning if fallback else logger.info)(
        "[build_student_balance] enrollment outside LSN, %s (%s): %s of %s (own semester), %s (last published "
        "winter semester) %s", what, semester, summary["primary"], len(names), summary["fallback"],
        {name: entry[name][1] for name in names if entry[name][1] != semester})
    return {name: float(entry[name][0]) for name in names}, summary


def _curated_sites() -> pd.DataFrame:
    """The institutions outside the LSN table (Sachsen-Anhalt): ``destination_id, che_institution,
    institution, lon, lat`` from ``_SURROUNDING`` of ``scripts/seed_nds_hochschulen.py``."""
    known = {name: (lon, lat) for name, lon, lat, _ in _NDS_SEED._SURROUNDING}
    rows = []
    for che_name, seed_name in T.OUTSIDE_LSN_CROSSWALK.items():
        if seed_name not in known:
            raise ValueError("OUTSIDE_LSN_CROSSWALK: %r is not in _SURROUNDING of seed_nds_hochschulen.py" % seed_name)
        lon, lat = known[seed_name]
        rows.append((seed_name + " | curated", che_name, seed_name, lon, lat))
    return pd.DataFrame(rows, columns=["destination_id", "che_institution", "institution", "lon", "lat"])


def prepare(args) -> dict:
    """Everything the two sub-commands share: sites, geometry, CHE cells, pair coefficients, builder.

    Returns a dict with the site frame for the builder (``sites``), the raw LSN rows (``lsn_sites``),
    the points, the communes, ``cells``, ``che_totals``, ``pairs``, the Kreis lists, the counted
    ``diagnostics``, ``build(weights) -> (od, diagnostics)`` and, when ``--sites-census-year`` is given
    (``balance`` only), ``build_census_year`` with the same signature: the same sites, only
    ``students_persons`` (the LSN rows and the enrollment outside LSN) differs. ``semester`` and
    ``census_year_semester`` name the two enrollment semesters; ``enrollment_lines`` is the header text.
    Every input is validated before the geometry is loaded.
    """
    sites_path = Path(args.sites)
    lsn, semester = _read_sites(sites_path)
    outside, outside_summary = _outside_lsn_enrollment(semester, "--sites")
    census_path = getattr(args, "sites_census_year", None)
    lsn_census = census_semester = outside_census = outside_census_summary = None
    if census_path:
        census_path = Path(census_path)
        lsn_census, census_semester = _read_sites(census_path)
        keys, keys_census = (set(zip(frame["institution"], frame["site_ags8"])) for frame in (lsn, lsn_census))
        if keys != keys_census:
            raise ValueError("the census-year site table (%s) must hold the same (institution, site_ags8) as "
                             "--sites (%s): only in --sites %s, only in the census-year table %s"
                             % (census_path.name, sites_path.name, sorted(keys - keys_census)[:5],
                                sorted(keys_census - keys)[:5]))
        outside_census, outside_census_summary = _outside_lsn_enrollment(census_semester, "--sites-census-year")
    che = _read_csv(args.che, T.CHE_COLUMNS, "CHE origin table", text_columns=["institution", "origin_ars5", "row_kind", "view"])
    inverse = _che_name_of_lsn_institution(set(lsn["institution"]))
    curated = _curated_sites()
    distance_learning = tuple(_csv_list(args.distance_learning, SEPARATOR_DISTANCE_LEARNING))

    lsn_rows = pd.DataFrame({
        "destination_id": lsn["institution"] + " | " + lsn["site_ags8"],
        "che_institution": lsn["institution"].map(inverse), "institution": lsn["institution"],
        "site_ags8": lsn["site_ags8"], "site_kreis_ars5": lsn["site_kreis_ars5"], "scope": lsn["scope"],
        "students_persons": lsn["students_persons"]})
    curated_rows = pd.DataFrame({
        "destination_id": curated["destination_id"], "che_institution": curated["che_institution"],
        "institution": curated["institution"], "site_ags8": np.nan, "site_kreis_ars5": np.nan,
        "scope": T.SCOPE_SURROUNDING, "students_persons": curated["institution"].map(outside).astype(float)})

    gemeinden = G.load_gemeinden(Path(args.vg250))
    gemeinden_kreise = set(gemeinden["ags8"].astype(str).str[:5])
    region_kreise = list(ZGB_KREISE)
    absent = [code for code in region_kreise if code not in gemeinden_kreise]
    if absent:
        raise ValueError("VG250 communes: region Kreis(e) %s have no commune" % absent)
    # The LSN rows are the sites, the institutions outside LSN the curated list: the two are disjoint, so
    # site_points warns only on a real double listing.
    points = G.site_points(lsn_rows[["destination_id", "site_ags8"]], gemeinden,
                           curated[["destination_id", "lon", "lat"]])
    curated_points = points[points["destination_id"].isin(curated["destination_id"])]
    if len(curated_points) != len(T.OUTSIDE_LSN_CROSSWALK):
        raise ValueError("expected %d curated site point(s) (OUTSIDE_LSN_CROSSWALK), found %d"
                         % (len(T.OUTSIDE_LSN_CROSSWALK), len(curated_points)))
    curated_kreise = locate_site_kreise(Path(args.vg250), curated_points)
    if len(curated_kreise) != len(T.OUTSIDE_LSN_CROSSWALK):
        raise ValueError("the VG250 Kreis lookup located %d of the %d curated sites (OUTSIDE_LSN_CROSSWALK)"
                         % (len(curated_kreise), len(T.OUTSIDE_LSN_CROSSWALK)))
    missing = sorted(set(curated["destination_id"]) - set(curated_kreise))
    if missing:
        raise ValueError("Kreis lookup returned no Kreis for curated site(s) %s" % missing)
    curated_rows = curated_rows.assign(site_kreis_ars5=curated_rows["destination_id"].map(curated_kreise))
    n_sites = len(lsn_rows) + len(curated_rows)
    logger.info("[build_student_balance] site Kreis: LSN table (primary) %d/%d (%.1f%%), VG250 polygon lookup "
                "(curated institutions) %d (%.1f%%)", len(lsn_rows), n_sites, 100.0 * len(lsn_rows) / n_sites,
                len(curated_rows), 100.0 * len(curated_rows) / n_sites)

    sites = pd.concat([lsn_rows, curated_rows], ignore_index=True)
    sites_builder = sites[T.SITE_COLUMNS]
    if sites["destination_id"].duplicated().any():
        raise ValueError("destination_id is not unique: %s"
                         % sites.loc[sites["destination_id"].duplicated(), "destination_id"].tolist()[:5])
    local_ids = sites.loc[sites["scope"] == T.SCOPE_LOCAL, "destination_id"].tolist()
    site_kreise = sorted(set(sites.loc[sites["scope"] == T.SCOPE_LOCAL, "site_kreis_ars5"]))
    reach = G.kreise_within_reach(gemeinden, points, local_ids, args.max_commute_km)
    origin_kreise = sorted(set(region_kreise) | set(reach))
    institutions = list(dict.fromkeys(sites["che_institution"]))

    totals = T.che_totals(che)
    cells, cell_diagnostics = T.origin_cells(che, institutions, origin_kreise, region_kreise, distance_learning)
    pairs = G.pair_coefficients(gemeinden, points, args.weight_rule, args.max_commute_km)

    def build(weights):
        return T.build_od_table(cells, totals, sites_builder, pairs, region_kreise, T.CommuterWeights(*weights))

    build_census_year = None
    if census_path:
        census_by_site = dict(zip(lsn_census["institution"] + " | " + lsn_census["site_ags8"],
                                  lsn_census["students_persons"]))
        census_students = sites["destination_id"].map(census_by_site)
        outside_mask = sites["scope"] == T.SCOPE_SURROUNDING
        outside_mask &= sites["destination_id"].isin(curated["destination_id"])
        census_students[outside_mask] = sites.loc[outside_mask, "institution"].map(outside_census)
        sites_census_builder = sites.assign(students_persons=census_students.astype(float))[T.SITE_COLUMNS]
        if sites_census_builder["students_persons"].isna().any():
            raise ValueError("census-year site table: no enrollment for site(s) %s"
                             % sites_census_builder.loc[sites_census_builder["students_persons"].isna(),
                                                        "destination_id"].tolist()[:5])

        def build_census_year(weights):
            return T.build_od_table(cells, totals, sites_census_builder, pairs, region_kreise,
                                    T.CommuterWeights(*weights))

    diagnostics = {
        "origin_cells": cell_diagnostics,
        "sites": {"n_sites": n_sites, "site_kreis_from_lsn_table": "primary %d/%d" % (len(lsn_rows), n_sites),
                  "site_kreis_from_polygon_lookup": "fallback %d/%d" % (len(curated_rows), n_sites),
                  "local_site_kreise": site_kreise, "distance_learning": list(distance_learning),
                  "enrollment_semester": semester, "outside_lsn_enrollment": outside_summary},
        "geometry": {"n_communes": len(gemeinden), "origin_kreise": len(origin_kreise),
                     "origin_kreise_beyond_region": len(origin_kreise) - len(region_kreise)},
    }
    if census_path:
        diagnostics["sites"]["census_year_semester"] = census_semester
        diagnostics["sites"]["outside_lsn_enrollment_census_year"] = outside_census_summary
    enrollment_lines = [
        "# Enrollment: enrollment_semester=%s (column semester of the --sites table, LSN-Online K3100101); "
        "the three institutions outside the LSN statistics carry the figures of OUTSIDE_LSN_STUDENTS in "
        "scripts/seed_lsn_university_sites.py (%s, %s of their own semester or the last published winter "
        "semester; the rate is in the Diagnostics line)." % (
            semester.replace(" ", "_"), outside_summary["primary"], outside_summary["fallback"])]
    return {"sites": sites_builder, "lsn_sites": lsn, "semester": semester,
            "census_year_semester": census_semester, "build_census_year": build_census_year,
            "enrollment_lines": enrollment_lines, "points": points, "gemeinden": gemeinden, "cells": cells,
            "che_totals": totals, "pairs": pairs, "region_kreise": region_kreise, "site_kreise": site_kreise,
            "origin_kreise": origin_kreise, "diagnostics": diagnostics, "build": build,
            "inputs": [("che_origins", Path(args.che), False), ("lsn_site_table", sites_path, True),
                       *([("lsn_site_table_census_year", census_path, True)] if census_path else []),
                       ("vg250_archive", Path(args.vg250), True),
                       ("curated_sites_script", REPO / "scripts" / "seed_nds_hochschulen.py", False),
                       ("enrollment_outside_lsn_script", REPO / "scripts" / "seed_lsn_university_sites.py", False)]}


# ---------------------------------------------------------------------------------------- headers
def _weight_rule_lines(args) -> list:
    """The weight rule of the run: the curve with its anchors, or the design's hard bands."""
    if args.weight_rule == G.WEIGHT_RULE_CENTROID_BANDS:
        return ["# Weight rule: weight_rule=%s, band_edges_km=%s, max_commute_km=%.10g (ASSUMPTION A10: the largest "
                "commuting distance): weight short below %g km, middle from %g to %g km, long from %g km to "
                "max_commute_km, 0 beyond, at the straight line from the population-weighted Kreis centroid "
                "(the design's original rule, comparison run only; see student_geometry.py)."
                % (args.weight_rule, list(G.BAND_EDGES_KM), args.max_commute_km, G.BAND_EDGES_KM[1],
                   G.BAND_EDGES_KM[1], G.BAND_EDGES_KM[2], G.BAND_EDGES_KM[2])]
    anchors = G.curve_anchors_km(args.max_commute_km)
    return ["# Weight rule: weight_rule=%s, curve_anchors_km=%s, max_commute_km=%.10g (ASSUMPTION A10: the largest "
            "commuting distance)." % (args.weight_rule, list(anchors), args.max_commute_km),
            "#   The students of a site come from the communes of a Kreis in proportion to population (owner "
            "decision of 2026-11-26, ADR-0210; see student_geometry.py)."]


def _common_header(table_name: str, args, prepared: dict, inputs: list, diagnostics: dict, extra: list) -> list:
    return [
        "# Table: %s (issue #140, Phase 2). A construction from the inputs below, not a validation."
        % table_name,
        "# Generated by scripts/build_student_balance.py %s on %s; code state eqasim-bs %s."
        % (args.command, dt.date.today().isoformat(), args.source_commit),
        "# The tables count HEADS (students by the Kreis they live in); no day factor is applied. Assumptions",
        "#   A2 to A7 are described in braunschweig/data/education/student_od_table.py, A9, A11, A12, A13, A16, A18",
        "#   and A19 in braunschweig/calibration/student_level_table.py, A10 in "
        "braunschweig/data/education/student_geometry.py.",
        "# Inputs:", *_input_lines(inputs),
        *_weight_rule_lines(args),
        "# Region Kreise: %s; local site Kreise: %s; origin Kreise: %d."
        % (prepared["region_kreise"], prepared["site_kreise"], len(prepared["origin_kreise"])),
        *extra,
        _diagnostics_line(diagnostics),
    ]


def _weights_lines(weights, mover_share: float, source: str, mover_kreise=()) -> list:
    return ["# Weights: %s mover_share=%.10g mover_site_kreise=[%s] weights_source=%s (calibrated = optimum of a decided calibration "
            "stage, owner = set by the owner)." % (_format_weights(weights), mover_share, ",".join(mover_kreise), source)]


# ---------------------------------------------------------------------------------------- calibrate
def _band_shares_srv(srv_distance: pd.DataFrame) -> list:
    rows = []
    for record in srv_distance.itertuples(index=False):
        for band in SRV_BANDS:
            rows.append(("srv_education_distance", str(record.code), band,
                         getattr(record, "share_" + band), float(record.n_persons)))
    return rows


def _band_shares_mid(mid_distance: pd.DataFrame) -> list:
    grouped = mid_distance.groupby("distance_band")[["n_unweighted", "share_of_all"]].sum()
    absent = [band for band in SRV_BANDS if band not in grouped.index]
    unknown = sorted(set(grouped.index) - set(SRV_BANDS))
    if absent or unknown:
        raise ValueError("MiD distance table: column 'distance_band' lacks %s and has unexpected %s" % (absent, unknown))
    total = float(grouped["share_of_all"].sum())
    if abs(total - 1.0) > SHARE_TOLERANCE:
        raise ValueError("MiD distance table: 'share_of_all' sums to %.10g over the bands, expected 1" % total)
    return [("mid_education_distance", "all", band, float(grouped.loc[band, "share_of_all"]),
             float(grouped.loc[band, "n_unweighted"])) for band in SRV_BANDS]


def _band_shares_mikrozensus(mikrozensus: pd.DataFrame) -> list:
    row = mikrozensus[mikrozensus["school_type"] == MIKROZENSUS_ROW]
    if len(row) != 1:
        raise ValueError("Mikrozensus school distance: column 'school_type' needs exactly one row %r, found %d"
                         % (MIKROZENSUS_ROW, len(row)))
    return [("mikrozensus_school_distance", "all", band, float(row.iloc[0][column]) / 100.0, np.nan)
            for column, band in MIKROZENSUS_BANDS.items()]


def _calibrate_header(table_name: str, args, prepared: dict, inputs: list, results: list, by_group: dict,
                      diagnostics: dict, extra: list) -> list:
    lines = []
    for result in results:
        ranges = {name: [float(low), float(high)] for name, (low, high) in result.ranges.items()}
        lines.append("# Stage %s: optimum %s mover_share=%.10g, objective_tvd=%.10g, noise_floor_tvd_95=%.10g, "
                     "decided=%s, ranges=%s, reasons=%s" % (
                         result.stage, _format_weights(result.optimum), result.mover_share, result.objective,
                         result.noise_floor, result.decided, json.dumps(ranges), json.dumps(result.reasons)))
        for record in by_group[result.stage].itertuples(index=False):
            lines.append("# Stage %s, home group %s: total_variation_distance=%.10g, "
                         "unreachable_reference_share=%.10g (the model puts 0 there), n_effective=%.10g, "
                         "noise_floor_tvd_95=%.10g" % (result.stage, record.home_group, record.total_variation_distance,
                                                      record.unreachable_reference_share, record.n_effective,
                                                      record.noise_floor_tvd_95))
    lines.append("# Calibration grid: grid_step=%.10g, mover_step=%.10g, max_flat_range=%.10g (ASSUMPTIONS of the "
                 "lead, CLI arguments); mover_site_kreise=%s." % (
                     args.grid_step, args.mover_step, args.max_flat_range, _csv_list(args.mover_site_kreise)))
    return _common_header(table_name, args, prepared, inputs, diagnostics,
                          [*prepared["enrollment_lines"], *lines, *extra])


def _run_calibrate(args) -> int:
    reference = _read_csv(args.destination, DESTINATION_COLUMNS, "SrV destination reference",
                          text_columns=["home_group", "destination_group"])
    srv_share = _read_csv(args.srv_share, SRV_SHARE_COLUMNS, "SrV student share", text_columns=["code"])
    srv_distance = _read_csv(args.srv_distance, SRV_DISTANCE_COLUMNS, "SrV education distance", text_columns=["code"])
    mid_distance = _read_csv(args.mid_distance, MID_DISTANCE_COLUMNS, "MiD education distance")
    mikrozensus = None
    if args.mikrozensus_school_distance:
        mikrozensus = _read_csv(args.mikrozensus_school_distance, MIKROZENSUS_COLUMNS, "Mikrozensus school distance",
                                text_columns=["school_type"])
    context_rows = _band_shares_srv(srv_distance) + _band_shares_mid(mid_distance)
    if mikrozensus is not None:
        context_rows += _band_shares_mikrozensus(mikrozensus)
    kreis_mix = srv_share[srv_share["level"] == "kreis"].set_index("code")["students_weighted_persons"].astype(float)
    mover_kreise = tuple(_csv_list(args.mover_site_kreise))

    prepared = prepare(args)
    site_kreise = frozenset(prepared["site_kreise"])
    build = lambda weights: prepared["build"](weights)[0]  # noqa: E731
    results = [W.calibrate(build, reference, site_kreise, kreis_mix, args.grid_step, args.max_flat_range)]
    if W.stage_two_due(results[0]):
        logger.info("[build_student_balance] stage one ends at the short bound: calibrating the mover share too")
        results.append(W.calibrate_movers(build, reference, site_kreise, kreis_mix, mover_kreise, SR.BRAUNSCHWEIG_KREIS,
                                          args.grid_step, args.mover_step, args.max_flat_range))

    by_group, fits, diagnostics = {}, [], dict(prepared["diagnostics"])
    diagnostics["build_od_table_at_optimum"] = {}
    for result in results:
        od, built = prepared["build"](result.optimum)
        if result.mover_share > 0.0:
            od = T.apply_movers(od, result.mover_share, mover_kreise, SR.BRAUNSCHWEIG_KREIS)
        fit = W.fit_distance(W.implied_shares(od, site_kreise, kreis_mix), reference)
        by_group[result.stage] = fit.by_group
        diagnostics["build_od_table_at_optimum"][result.stage] = built
        fits.append(W.fit_table(result, build, reference, site_kreise, kreis_mix, mover_kreise, SR.BRAUNSCHWEIG_KREIS))
        for record in W.implied_distance_classes(od, prepared["region_kreise"]).itertuples(index=False):
            context_rows.append(("model_stage_" + result.stage, record.code, record.distance_class, record.share, np.nan))

    t_inputs = prepared["inputs"]
    fit_inputs = [*t_inputs, ("srv_destination_reference", Path(args.destination), False),
                  ("srv_student_share", Path(args.srv_share), False)]
    context_inputs = [*fit_inputs, ("srv_education_distance", Path(args.srv_distance), False),
                      ("mid_education_distance", Path(args.mid_distance), False)]
    if mikrozensus is not None:
        context_inputs.append(("mikrozensus_school_distance", Path(args.mikrozensus_school_distance), True))
    out = Path(args.out_dir)
    context_note = [
        "# Context rows: the SrV bands (srv2023_student_education_distance_by_kreis.csv, n_unweighted = n_persons), the",
        "#   MiD bands summed over modes (share_of_all, n_unweighted summed), the Mikrozensus row %s when given (percent"
        % MIKROZENSUS_ROW,
        "#   divided by 100, rounded in the source), and the model's distance classes at each stage's optimum",
        "#   (student_commuter_weights.DISTANCE_CLASSES, straight line centroid to site, band = class, n_unweighted empty).",
        "#   The sources measure different distances and different populations; none of them is fitted."]
    _write(pd.concat([result.table for result in results], ignore_index=True)[W.CALIBRATION_COLUMNS],
           out / CALIBRATION_FILE,
           _calibrate_header(CALIBRATION_FILE, args, prepared, fit_inputs, results, by_group, diagnostics, []))
    _write(pd.concat(fits, ignore_index=True)[W.FIT_COLUMNS], out / FIT_FILE,
           _calibrate_header(FIT_FILE, args, prepared, fit_inputs, results, by_group, diagnostics, []))
    _write(pd.DataFrame(context_rows, columns=CONTEXT_COLUMNS), out / CONTEXT_FILE,
           _calibrate_header(CONTEXT_FILE, args, prepared, context_inputs, results, by_group, diagnostics, context_note))

    final = results[-1]
    snippet = W.config_snippet(final)
    snippet_path = out / SNIPPET_FILE
    if snippet is None:
        if snippet_path.exists():
            logger.warning("removing the snippet of an earlier run: stage %s is not decided", final.stage)
            snippet_path.unlink()
    else:
        header = _calibrate_header(SNIPPET_FILE, args, prepared, fit_inputs, results, by_group, diagnostics, [])
        snippet_path.write_text("\n".join(header) + "\n" + snippet, encoding="utf-8", newline="")
        logger.info("wrote %s", snippet_path)
    logger.info("[build_student_balance] calibration done: final stage %s, decided %s, snippet %s",
                final.stage, final.decided, "written" if snippet else "absent")
    return 0


# ----------------------------------------------------------------------------------------- balance
def _parameter_shares(parameters: pd.DataFrame, group: str, parameter: str, base: str) -> pd.Series:
    rows = parameters[(parameters["group"] == group) & (parameters["parameter"] == parameter)
                      & (parameters["base"] == base)]
    if rows["label"].duplicated().any():
        raise ValueError("SrV parameter table: duplicated label(s) for %s / %s" % (parameter, base))
    if rows.empty:
        raise ValueError("SrV parameter table: no rows for parameter %r, base %r" % (parameter, base))
    return rows.set_index("label")["share"].astype(float)


def _model_today(model_level: pd.DataFrame) -> pd.Series:
    """Students per Kreis the model holds today; the pooled row is the sum over the surveyed Kreise."""
    kreise = model_level[model_level["level"] == "kreis"]
    if kreise["code"].duplicated().any():
        raise ValueError("model level table: duplicated Kreis code(s) in column 'code'")
    values = kreise.set_index("code")["model_students_expanded_persons"].astype(float)
    absent = [code for code in ZGB_KREISE if code not in values.index]
    if absent:
        raise ValueError("model level table: Kreis(e) %s missing in column 'code'" % absent)
    values = values.loc[list(ZGB_KREISE)]
    pooled = float(values.drop(list(UNSURVEYED_KREISE)).sum())
    own = model_level.loc[model_level["code"] == REGION_CODE, "model_students_expanded_persons"].astype(float)
    if len(own) != 1:
        raise ValueError("model level table: needs exactly one pooled row %r in column 'code', found %d"
                         % (REGION_CODE, len(own)))
    if abs(float(own.iloc[0]) - pooled) > POOLED_MODEL_TOLERANCE_PERSONS:
        raise ValueError("model level table: the pooled row %s holds %.10g students but the surveyed Kreise "
                         "(all but %s) sum to %.10g; the table and the Kreis rows disagree"
                         % (REGION_CODE, float(own.iloc[0]), list(UNSURVEYED_KREISE), pooled))
    values[REGION_CODE] = pooled
    return values


def _joint_shares(age_sex: pd.DataFrame) -> pd.Series:
    """The MiD joint share of (sex, age group) among the adult students of Germany (``area`` DE, sexes
    female and male, column ``share_of_adult_students``), indexed by (sex, age group). The rows ``all`` and
    the RegioStaR rows of the table are not read. A missing or duplicated cell raises; the level module
    checks that the shares sum to 1."""
    rows = age_sex[(age_sex["area"] == REGION_GERMANY) & age_sex["sex"].isin(LV.SEXES)]
    duplicated = rows.duplicated(["sex", "age_group"], keep=False)
    if duplicated.any():
        raise ValueError("MiD age-sex table: duplicated (sex, age_group) for area %s: %s"
                         % (REGION_GERMANY, rows.loc[duplicated, ["sex", "age_group"]].drop_duplicates().values.tolist()))
    shares = pd.Series(pd.to_numeric(rows["share_of_adult_students"], errors="coerce").values,
                       index=pd.MultiIndex.from_frame(rows[["sex", "age_group"]]))
    missing = [(sex, age) for sex in LV.SEXES for age in LV.AGE_LABELS if (sex, age) not in shares.index]
    if missing:
        raise ValueError("MiD age-sex table: area %s lacks cell(s) %s in columns sex, age_group, "
                         "share_of_adult_students" % (REGION_GERMANY, missing))
    return shares.loc[[(sex, age) for sex in LV.SEXES for age in LV.AGE_LABELS]]


def _parse_weights(text: str) -> T.CommuterWeights:
    try:
        values = [float(item) for item in _csv_list(text)]
    except ValueError:
        raise ValueError("--weights needs three comma-separated numbers 'short,middle,long', got %r" % text) from None
    weights = T.CommuterWeights(*values) if len(values) == 3 else None
    if weights is None:
        raise ValueError("--weights needs three comma-separated numbers 'short,middle,long', got %r" % text)
    T.validate_weights(weights)
    return weights


def _output_dirs(args) -> tuple:
    """(directory of the committed-style tables, directory of the level table, directory of the gate)."""
    artifact = Path(args.artifact_dir)
    if args.final:
        if not args.out_dir:
            raise ValueError("--final needs --out-dir (the eqasim-data root)")
        root = Path(args.out_dir)
        return root / SCHOOLS_SUBDIR, root / POPSIM_SUBDIR, artifact
    if not args.label or not re.fullmatch(r"[A-Za-z0-9._-]+", args.label):
        raise ValueError("--label needs a plain name (letters, digits, dot, dash, underscore), got %r" % args.label)
    folder = artifact / "options" / args.label
    return folder, folder, folder


def _balance_summary(tables: dict, od: pd.DataFrame) -> dict:
    by_site, out = tables["by_site"], tables["out_commuters"]
    return {
        "enrollment_persons": float(by_site["enrollment_persons"].sum()),
        "residents_region_persons": float(by_site["residents_region_persons"].sum()),
        "in_commuters_persons": float(by_site["in_commuters_persons"].sum()),
        "out_commuters_persons": float(out["out_commuters_persons"].sum()),
        "censored_upper_bound_persons": float(by_site["censored_upper_bound_persons"].sum()),
        "censored_upper_bound_commuters_persons": float(by_site["censored_upper_bound_commuters_persons"].sum()),
        "censored_cells_without_bound": {"local_sites": int(by_site["censored_cells_without_bound"].sum()),
                                         "surrounding_sites": int(out["censored_cells_without_bound"].sum())},
        "censored_upper_bound_commuters_capped_sites": tables["diagnostics"]["censored_upper_bound_commuters_capped_sites"],
        "movers_to_braunschweig_persons": float(od.loc[od["cell_kind"] == T.KIND_MOVER, "students_persons"].sum()),
        # The builder's ``rows_per_cell_kind`` is counted before the movers step; this count is after it.
        "rows_per_cell_kind_after_movers": {kind: int((od["cell_kind"] == kind).sum()) for kind in T.CELL_KINDS},
        "students_living_persons": float(tables["by_home_kreis"]["students_living_persons"].sum()),
    }


def _run_balance(args) -> int:
    weights = _parse_weights(args.weights)
    mover_share = float(args.mover_share)
    if not 0.0 <= mover_share <= 1.0:
        raise ValueError("--mover-share must lie in [0, 1], got %r" % args.mover_share)
    if bool(args.final) == bool(args.label):
        raise ValueError("balance needs exactly one of --label <name> and --final")
    table_dir, level_dir, gate_dir = _output_dirs(args)
    mover_kreise = tuple(_csv_list(args.mover_site_kreise))

    parameters = _read_csv(args.srv_parameters, PARAMETER_COLUMNS, "SrV student parameters")
    srv_share = _read_csv(args.srv_share, SRV_SHARE_COLUMNS, "SrV student share", text_columns=["code"])
    mid_share = _read_csv(args.mid_share, MID_SHARE_COLUMNS, "MiD student share", text_columns=["region"])
    model_level = _read_csv(args.model_level, MODEL_LEVEL_COLUMNS, "model level table", text_columns=["code"])
    zensus_students = _read_csv(args.zensus_students, E.CENSUS_COLUMNS, "Zensus students", text_columns=["region"])
    mid_age_sex = _read_csv(args.mid_age_sex, MID_AGE_SEX_COLUMNS, "MiD age-sex table",
                            text_columns=["area", "sex", "age_group"])
    population = _read_csv(args.population, LV.POPULATION_COLUMNS, "LSN population table",
                           text_columns=["district_ars5", "date"])
    _read_csv(args.zensus_employed, EMPLOYED_COLUMNS, "Zensus employed persons", text_columns=["region"])
    zensus_employed = read_kreis_employed_by_age(str(args.zensus_employed))
    education_leg_share = float(read_header_value(args.srv_parameters, EDUCATION_LEG_KEY))
    if not 0.0 <= education_leg_share <= 1.0:
        raise ValueError("%s header key %s = %r is outside [0, 1]" % (Path(args.srv_parameters).name, EDUCATION_LEG_KEY,
                                                                      education_leg_share))
    away = _parameter_shares(parameters, "day", "away_all_day", "students_all")
    if "yes" not in away.index:
        raise ValueError("SrV parameter table: no label 'yes' for away_all_day / students_all")
    away_share = float(away["yes"])
    model_today = _model_today(model_level)

    prepared = prepare(args)
    od, t_diagnostics = prepared["build"](weights)
    od = T.apply_movers(od, mover_share, mover_kreise, SR.BRAUNSCHWEIG_KREIS)
    tables = T.balance_tables(od, prepared["region_kreise"])
    students_living = tables["by_home_kreis"].set_index("home_ars5")["students_living_persons"].astype(float)
    # The same sites, weights and movers under the enrollment of the census-year semester (A12).
    od_census_year, t_diagnostics_census_year = prepared["build_census_year"](weights)
    od_census_year = T.apply_movers(od_census_year, mover_share, mover_kreise, SR.BRAUNSCHWEIG_KREIS)
    tables_census_year = T.balance_tables(od_census_year, prepared["region_kreise"])
    students_living_census_year = tables_census_year["by_home_kreis"].set_index(
        "home_ars5")["students_living_persons"].astype(float)

    joint = _joint_shares(mid_age_sex)
    ages = LV.age_margin(joint)
    change = LV.population_change(population, args.population_base_date, args.population_model_date)
    level_by_kreis, level_parts = LV.census_pattern_level(students_living, zensus_students, change)
    level_census_year, _ = LV.census_pattern_level(students_living_census_year, zensus_students)
    omega_value, parts = LV.omega(students_living_census_year, zensus_students, mid_share, ages)
    level = LV.level_table(level_by_kreis, joint, omega_value)
    precheck = LV.employed_target_precheck(level, zensus_employed)
    lsn_gate_sites = prepared["lsn_sites"][E.SITE_COLUMNS]
    level_gate = E.build_level_gate(srv_share, zensus_students, mid_share, lsn_gate_sites,
                                    residents_implied=students_living.to_dict())
    gate = LV.gate_table(level_gate, srv_share, away_share, ages, model_today, level_by_kreis, level_census_year)
    triggered = LV.gate_triggered(gate)
    triggered_area_consistent = LV.gate_triggered_area_consistent(gate)

    diagnostics = dict(prepared["diagnostics"])
    diagnostics["build_od_table"] = t_diagnostics
    diagnostics["build_od_table_census_year"] = t_diagnostics_census_year
    diagnostics["balance"] = _balance_summary(tables, od)
    day_factor = education_leg_share * (1.0 - away_share)
    weights_lines = _weights_lines(weights, mover_share, args.weights_source, mover_kreise)
    t_inputs = prepared["inputs"]
    balance_inputs = t_inputs + [("srv_student_parameters", Path(args.srv_parameters), False)]
    level_inputs = [*balance_inputs, ("srv_student_share", Path(args.srv_share), False),
                    ("mid_student_share", Path(args.mid_share), False),
                    ("mid_student_age_sex", Path(args.mid_age_sex), False),
                    ("lsn_population_20_29", Path(args.population), False),
                    ("zensus_students", Path(args.zensus_students), True)]
    precheck_inputs = [*level_inputs, ("zensus_employed", Path(args.zensus_employed), True)]
    gate_inputs = [*level_inputs, ("model_level", Path(args.model_level), False)]

    def head(name, inputs, extra=()):
        return _common_header(name, args, prepared, inputs, diagnostics,
                              [*prepared["enrollment_lines"], *weights_lines, *extra])

    day_line = ["# Day factor (for Phases 3 and 4; the tables count heads, no day factor applied): "
                "%s=%.10g * (1 - away_all_day_share=%.10g) = %.10g, both shares from %s."
                % (EDUCATION_LEG_KEY, education_leg_share, away_share, day_factor, SR.PARAMETER_TABLE)]
    _write(od[T.T_COLUMNS], table_dir / OD_FILE, head(OD_FILE, balance_inputs, day_line))
    _write(tables["by_site"], table_dir / BY_SITE_FILE, head(BY_SITE_FILE, balance_inputs, day_line))
    _write(tables["out_commuters"], table_dir / OUT_COMMUTERS_FILE, head(OUT_COMMUTERS_FILE, balance_inputs))
    _write(tables["in_commuters_by_origin"], table_dir / IN_COMMUTERS_FILE, head(IN_COMMUTERS_FILE, balance_inputs))
    # Values with a space are written with an underscore so that ``read_header_value`` reads them back.
    # ``enrollment_semester=`` is written once per file, by the enrollment line of ``prepare`` (``head``).
    semesters_line = ["# Census-year enrollment of the second build of T (omega, A12): "
                      "census_year_semester=%s (the semester of --sites-census-year)."
                      % prepared["census_year_semester"].replace(" ", "_")]
    omega_line = [
        "# omega=%.10g = 1 - Z / S; Z=%.10g, S=%.10g, mid_students_to_pupils_and_students_ratio=%.10g, "
        "age_share_20_29_among_adult_students=%.10g, zensus_students_nonemployed_persons=%.10g, "
        "students_living_census_year_persons=%.10g (all eight Kreise under the census-year enrollment, A12); "
        "level_total_persons=%.10g (the same eight Kreise under the enrollment of the balance)." % (
            omega_value, parts["Z"], parts["S"], parts["mid_students_to_pupils_and_students_ratio"],
            parts["age_share_20_29_among_adult_students"], parts["zensus_students_nonemployed_persons"],
            parts["students_living_persons"], level_parts["level_total_persons"])]
    shares_json = json.dumps({"balance": level_parts["balance_share"], "level": level_parts["level_share"]},
                             separators=(",", ":"), sort_keys=False)
    level_rule_lines = [
        "# Level rule (A18, A19): level_k = level_total_persons * Z_k * c_k / sum_j(Z_j * c_j), Z_k = Zensus 2022 "
        "pupils and students without paid work aged 20 to 29 of Kreis k, c_k = population change below; the share "
        "of each Kreis in the balance and in the level follows, shares_json=" + shares_json,
        "# Population change (A19): population_base_date=%s, population_model_date=%s, persons aged 20 to 29 on "
        "the model date over the base date per Kreis: %s (%s)." % (
            args.population_base_date, args.population_model_date,
            json.dumps(level_parts["population_change"], separators=(",", ":")), POPULATION_DEFAULT.name),
        "# Age and sex (A9): the cell is the Kreis level times the joint share of (sex, age group) among the adult "
        "students of Germany, %s (area %s), the same mix in every Kreis; the age shares are %s." % (
            Path(args.mid_age_sex).name, REGION_GERMANY,
            ", ".join("age_share_%s=%.10g" % (age, share) for age, share in ages.items())),
        "# " + LV.LEVEL_DECISION_NOTE]
    level_lines = [*semesters_line, *omega_line, *level_rule_lines]
    _write(level, level_dir / LEVEL_FILE, head(LEVEL_FILE, level_inputs, [*level_lines, *day_line]))
    gate_line = ["# gate_triggered=%s (Braunschweig row or pooled row beyond %.1f SrV standard errors: the owner decides "
                 "on the level then)." % (triggered, E.MIN_DIFFERENCE_STANDARD_ERRORS),
                 "# gate_triggered_area_consistent=%s (the same rule on the comparison of the students aged 20 to 29, "
                 "column beyond_two_se_20_29)." % triggered_area_consistent,
                 "# " + LV.SRV_COVERAGE_NOTE,
                 "# " + LV.AWAY_SHARE_COLUMN_NOTE,
                 "# The SrV standard error is that of the student share; the away share's own sampling error is ignored.",
                 "# model_today_students_persons: model_students_expanded_persons of the Phase 1 measurement; the "
                 "pooled row is the sum over the surveyed Kreise."]
    _write(gate, gate_dir / GATE_FILE, head(GATE_FILE, gate_inputs, [*level_lines, *gate_line]))
    _write(precheck, gate_dir / PRECHECK_FILE, head(PRECHECK_FILE, precheck_inputs, level_lines))
    logger.info("[build_student_balance] balance done: omega %.4f, gate_triggered %s, gate_triggered_area_consistent "
                "%s (exit code 0 either way)", omega_value, triggered, triggered_area_consistent)
    return 0


# -------------------------------------------------------------------------------------------- CLI
def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--sites", required=True, help="local-only LSN site table (lsn_university_sites.csv); never a default")
    parser.add_argument("--vg250", required=True, help="local-only VG250 archive (zip); never a default")
    parser.add_argument("--source-commit", required=True, help="git commit of the code state, written to every header")
    parser.add_argument("--max-commute-km", type=float, default=100.0,
                        help="largest commuting distance (km) of the weight curve (ASSUMPTION A10), default 100")
    parser.add_argument("--weight-rule", choices=[G.WEIGHT_RULE_COMMUNE_CURVE, G.WEIGHT_RULE_CENTROID_BANDS],
                        default=G.WEIGHT_RULE_COMMUNE_CURVE,
                        help="commune_curve (default) or centroid_bands (the design's original rule, comparison run only)")
    parser.add_argument("--distance-learning", default=SEPARATOR_DISTANCE_LEARNING.join(T.DISTANCE_LEARNING_DEFAULT),
                        help="CHE names of distance-learning institutions, separated by '%s' (ASSUMPTION A7)"
                             % SEPARATOR_DISTANCE_LEARNING)
    parser.add_argument("--mover-site-kreise", default=MOVER_SITE_KREISE_DEFAULT,
                        help="site Kreise whose movers may live in Braunschweig (design B3 step 7; ASSUMPTION)")
    parser.add_argument("--che", default=str(CHE_DEFAULT), help="CHE origin table (committed)")
    parser.add_argument("--destination", default=str(SRV_DEFAULTS["destination"]), help="SrV destination reference (committed)")
    parser.add_argument("--srv-share", default=str(SRV_DEFAULTS["srv_share"]), help="SrV student share by Kreis (committed)")
    parser.add_argument("--srv-parameters", default=str(SRV_DEFAULTS["srv_parameters"]), help="SrV parameter table (committed)")
    parser.add_argument("--srv-distance", default=str(SRV_DEFAULTS["srv_distance"]), help="SrV education distance (committed)")
    parser.add_argument("--mid-share", default=str(MID_DEFAULTS["mid_share"]), help="MiD student share by Land (committed)")
    parser.add_argument("--mid-distance", default=str(MID_DEFAULTS["mid_distance"]), help="MiD education distance by mode (committed)")
    parser.add_argument("--model-level", default=str(MODEL_LEVEL_DEFAULT), help="Phase 1 model level by Kreis (committed)")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    common = _common_parser()
    calibrate = commands.add_parser("calibrate", parents=[common], help="calibrate the weight triple (and mover share)")
    calibrate.add_argument("--out-dir", required=True, help="directory of the calibration artifacts")
    calibrate.add_argument("--grid-step", type=float, default=0.05, help="weight grid resolution (ASSUMPTION)")
    calibrate.add_argument("--mover-step", type=float, default=0.05, help="mover share grid resolution (ASSUMPTION)")
    calibrate.add_argument("--max-flat-range", type=float, default=0.2,
                           help="widest range of a parameter over the indifference set that still counts as decided (ASSUMPTION)")
    calibrate.add_argument("--mikrozensus-school-distance", default=None,
                           help="optional local-only Mikrozensus school-distance table (row hochschule, percent)")
    calibrate.set_defaults(run=_run_calibrate)
    balance = commands.add_parser("balance", parents=[common], help="build T, its balance, the level table and the gate")
    balance.add_argument("--weights", required=True, help="commuter weights 'short,middle,long', each in [0, 1], not increasing")
    balance.add_argument("--mover-share", required=True, type=float, help="share of the movers moved to Braunschweig, in [0, 1]")
    balance.add_argument("--weights-source", required=True, choices=WEIGHTS_SOURCES,
                         help="where the triple and the mover share come from (written to every header)")
    balance.add_argument("--sites-census-year", required=True,
                         help="local-only LSN site table of the census-year semester (same sites as --sites); never a default")
    balance.add_argument("--mid-age-sex", default=str(MID_DEFAULTS["mid_age_sex"]),
                        help="MiD adult-student shares by sex and age group (committed)")
    balance.add_argument("--population", default=str(POPULATION_DEFAULT),
                        help="LSN persons aged 20 to 29 by Kreis and date (committed)")
    balance.add_argument("--population-base-date", default=POPULATION_BASE_DATE_DEFAULT,
                        help="date of the census pattern (A19), default %s" % POPULATION_BASE_DATE_DEFAULT)
    balance.add_argument("--population-model-date", default=POPULATION_MODEL_DATE_DEFAULT,
                        help="date the pattern is carried to (A19), default %s" % POPULATION_MODEL_DATE_DEFAULT)
    balance.add_argument("--zensus-students", required=True, help="local-only Zensus student table; never a default")
    balance.add_argument("--zensus-employed", required=True, help="local-only Zensus employed-by-age table; never a default")
    balance.add_argument("--artifact-dir", required=True, help="run artifact directory")
    balance.add_argument("--label", default=None, help="option name: everything goes to <artifact-dir>/options/<label>/")
    balance.add_argument("--final", action="store_true",
                         help="write the five committed tables under --out-dir, gate and pre-check to --artifact-dir")
    balance.add_argument("--out-dir", default=None, help="eqasim-data root (with --final)")
    balance.set_defaults(run=_run_balance)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
