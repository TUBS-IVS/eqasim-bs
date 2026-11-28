"""Commune geometry, site points and weight-curve coefficients for the student table (#140, Phase 2).

Everything except :func:`load_gemeinden` is pure (no I/O). All coordinates are metric
(:data:`CRS`, EPSG:25832); distances are straight lines in kilometres.

The commuter weight of a (home Kreis, university site) pair is a continuous curve of the
distance with three anchor weights ``(short, middle, long)``. This module supplies, per pair,
the three FIXED coefficients (the basis of the curve averaged over the communes of the Kreis);
the weights themselves are calibrated elsewhere and multiplied with these coefficients.

ASSUMPTION (curve anchors): the anchors are the midpoints of the design's distance bands
``(BAND_EDGES_KM[0], BAND_EDGES_KM[1])``, ``(BAND_EDGES_KM[1], BAND_EDGES_KM[2])`` and
``(BAND_EDGES_KM[2], max_commute_km)``; 17.5, 37.5 and 75 km for ``max_commute_km`` 100.
ASSUMPTION (within-Kreis spread): the students of a site come from the communes of a Kreis in
proportion to the communes' resident population (``EWZ``). Both are modelling assumptions, not
observed values.
ASSUMPTION A10: no student commutes farther than ``max_commute_km`` (a caller argument; the balance
CLI defaults to 100 km); the curve and the bands are 0 beyond it.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

LOGGER = logging.getLogger(__name__)

CRS = "EPSG:25832"
WGS84 = "EPSG:4326"

VG250_LAYER = "vg250_gem"
LAND_GF = 4  # VG250 "Geofaktor" 4 = land area; GF 2 rows are water and carry EWZ 0

# Design B4: the hard distance bands (km) of the original rule and the anchors of the curve.
BAND_EDGES_KM = (10.0, 25.0, 50.0)

WEIGHT_RULE_COMMUNE_CURVE = "commune_curve"
WEIGHT_RULE_CENTROID_BANDS = "centroid_bands"
PAIR_COLUMNS = ["ars5", "destination_id", "distance_km", "coeff_short", "coeff_middle", "coeff_long"]


def _require_columns(frame: pd.DataFrame, columns: list[str], what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what}: missing column(s) {missing}; found {list(frame.columns)}")


def _require_finite(frame: pd.DataFrame, columns: list[str], id_column: str, what: str) -> None:
    """Raise on NaN or infinite values, naming the column and the first offending ids."""
    for column in columns:
        values = pd.to_numeric(frame[column], errors="raise").to_numpy(dtype=float)
        bad = ~np.isfinite(values)
        if bad.any():
            ids = frame[id_column].astype(str)[bad].tolist()
            raise ValueError(f"{what}: {int(bad.sum())} missing or non-finite value(s) in column {column!r}, "
                             f"{id_column} {ids[:5]}")


def _require_unique(frame: pd.DataFrame, column: str, what: str) -> None:
    duplicated = frame.loc[frame[column].duplicated(), column].unique()
    if len(duplicated):
        raise ValueError(f"{what}: duplicated {column} {sorted(map(str, duplicated))[:5]}")


def load_gemeinden(vg250_zip: Path) -> pd.DataFrame:
    """Read the VG250 commune layer: ``ags8, ewz, x, y`` (centroid in :data:`CRS`).

    Only land rows (``GF == 4``) are kept; water rows of the same commune carry ``EWZ`` 0.
    The key is the 8-digit ``AGS`` (``ARS`` is the 12-digit key and is not used). The layer's
    CRS must be :data:`CRS` and ``AGS`` must be unique among the land rows.
    """
    # Heavy modules are imported here so that the pure functions stay cheap to import.
    import geopandas as gpd

    from braunschweig.analysis.spatial import VG250_INNER

    vg250_zip = Path(vg250_zip)
    if not vg250_zip.is_file():
        raise ValueError(f"VG250 archive not found: {vg250_zip}")
    with zipfile.ZipFile(vg250_zip) as archive, archive.open(VG250_INNER) as handle:
        layer = gpd.read_file(handle, layer=VG250_LAYER)
    _require_columns(layer, ["AGS", "GF", "EWZ", "geometry"], f"{vg250_zip.name} layer {VG250_LAYER}")
    if layer.crs is None or layer.crs.to_epsg() != int(CRS.split(":")[1]):
        raise ValueError(f"{vg250_zip.name} layer {VG250_LAYER}: CRS is {layer.crs}, expected {CRS}")

    land = layer[layer["GF"] == LAND_GF]
    n_dropped = len(layer) - len(land)
    ags8 = land["AGS"].astype(str).str.zfill(8)
    if (ags8.str.len() != 8).any() or not ags8.str.isdigit().all():
        raise ValueError(f"{VG250_LAYER}: AGS is not an 8-digit code for land rows, e.g. "
                         f"{ags8[(ags8.str.len() != 8) | ~ags8.str.isdigit()].iloc[0]!r}")
    ewz = pd.to_numeric(land["EWZ"], errors="raise")
    if ewz.isna().any() or (ewz < 0).any():
        raise ValueError(f"{VG250_LAYER}: EWZ is missing or negative in "
                         f"{int(ewz.isna().sum() + (ewz < 0).sum())} land rows")
    centroids = land.geometry.centroid
    result = pd.DataFrame({"ags8": ags8.to_numpy(), "ewz": ewz.to_numpy(),
                           "x": centroids.x.to_numpy(), "y": centroids.y.to_numpy()})
    _require_unique(result, "ags8", f"{VG250_LAYER} land rows (GF == {LAND_GF})")
    LOGGER.info("[student_geometry] %s: %d land communes (GF == %d), %d inhabitants; dropped %d non-land rows "
                "(GF != %d) of %d, from %s", VG250_LAYER, len(result), LAND_GF, int(result["ewz"].sum()),
                n_dropped, LAND_GF, len(layer), vg250_zip)
    return result


def population_weighted_kreis_centroids(gemeinden: pd.DataFrame) -> pd.DataFrame:
    """Per Kreis (``ars5 = ags8[:5]``) the ``ewz``-weighted mean commune centroid: ``ars5, x, y, ewz``.

    A Kreis whose communes have no inhabitants has no population-weighted centroid and raises.
    """
    _require_columns(gemeinden, ["ags8", "ewz", "x", "y"], "gemeinden")
    frame = pd.DataFrame({"ars5": gemeinden["ags8"].astype(str).str[:5],
                          "ewz": gemeinden["ewz"].to_numpy(dtype=float)})
    frame["wx"] = frame["ewz"] * gemeinden["x"].to_numpy(dtype=float)
    frame["wy"] = frame["ewz"] * gemeinden["y"].to_numpy(dtype=float)
    totals = frame.groupby("ars5", sort=True)[["ewz", "wx", "wy"]].sum()
    empty = totals.index[totals["ewz"] <= 0].tolist()
    if empty:
        raise ValueError(f"Kreis without inhabitants (ewz total 0), no population-weighted centroid: {empty}")
    return pd.DataFrame({"ars5": totals.index, "x": (totals["wx"] / totals["ewz"]).to_numpy(),
                         "y": (totals["wy"] / totals["ewz"]).to_numpy(),
                         "ewz": totals["ewz"].to_numpy()}).reset_index(drop=True)


def site_points(sites: pd.DataFrame, gemeinden: pd.DataFrame, curated: pd.DataFrame) -> pd.DataFrame:
    """One point per destination: ``destination_id, x, y`` in :data:`CRS`.

    ``sites`` (``destination_id, site_ags8``) are located at the centroid of the commune
    ``site_ags8``. ``curated`` (``destination_id, lon, lat`` in WGS84; the institutions outside
    the LSN site table) is projected to :data:`CRS` and takes precedence for a destination that
    is in both. The output holds every destination of ``sites`` and of ``curated``. A site that
    is in ``curated`` or has a known commune is located; any other raises.
    """
    _require_columns(sites, ["destination_id", "site_ags8"], "sites")
    _require_columns(curated, ["destination_id", "lon", "lat"], "curated")
    _require_columns(gemeinden, ["ags8", "x", "y"], "gemeinden")
    _require_unique(sites, "destination_id", "sites")
    _require_unique(curated, "destination_id", "curated")
    if not (curated["lon"].between(-180, 180).all() and curated["lat"].between(-90, 90).all()):
        raise ValueError("curated: lon/lat outside the WGS84 range (swapped columns?)")

    transformer = Transformer.from_crs(WGS84, CRS, always_xy=True)
    cx, cy = transformer.transform(curated["lon"].to_numpy(dtype=float), curated["lat"].to_numpy(dtype=float))
    curated_xy = {str(i): (float(x), float(y)) for i, x, y in zip(curated["destination_id"], cx, cy)}
    commune_xy = {str(a): (float(x), float(y)) for a, x, y in zip(gemeinden["ags8"], gemeinden["x"], gemeinden["y"])}

    rows = []
    n_commune = 0
    overridden = []  # (destination_id, displacement text) of sites that also have curated coordinates
    for destination_id, site_ags8 in zip(sites["destination_id"].astype(str), sites["site_ags8"]):
        ags8 = None if pd.isna(site_ags8) else str(site_ags8)
        if destination_id in curated_xy:
            rows.append((destination_id, *curated_xy[destination_id]))
            if ags8 in commune_xy:
                displacement_km = np.hypot(curated_xy[destination_id][0] - commune_xy[ags8][0],
                                           curated_xy[destination_id][1] - commune_xy[ags8][1]) / 1000.0
                overridden.append(f"{destination_id} ({displacement_km:.2f} km from the centroid of {ags8})")
            else:
                overridden.append(f"{destination_id} (site_ags8 {site_ags8!r} unknown, no displacement)")
        elif ags8 in commune_xy:
            rows.append((destination_id, *commune_xy[ags8]))
            n_commune += 1
        else:
            raise ValueError(f"destination {destination_id!r}: site_ags8 {site_ags8!r} is not a commune of "
                             f"gemeinden and the destination is not in the curated list")
    if overridden:
        LOGGER.warning("[student_geometry] %d site(s) are in the site table AND the curated list; the curated "
                       "coordinates are used: %s", len(overridden), "; ".join(overridden))
    listed = {row[0] for row in rows}
    rows.extend((i, *xy) for i, xy in curated_xy.items() if i not in listed)
    LOGGER.info("[student_geometry] site points: commune centroid %d/%d (%.1f%%), curated coordinates %d "
                "(%d of them overriding a site row)", n_commune, len(rows), 100.0 * n_commune / max(len(rows), 1),
                len(rows) - n_commune, len(overridden))
    return pd.DataFrame(rows, columns=["destination_id", "x", "y"])


def curve_anchors_km(max_commute_km: float) -> tuple[float, float, float]:
    """The anchors of the weight curve: midpoints of the bands ``(10, 25)``, ``(25, 50)``, ``(50, max)``."""
    if not max_commute_km > BAND_EDGES_KM[2]:
        raise ValueError(f"max_commute_km must exceed the last band edge {BAND_EDGES_KM[2]} km, "
                         f"got {max_commute_km}")
    lo, mid, hi = BAND_EDGES_KM
    return ((lo + mid) / 2.0, (mid + hi) / 2.0, (hi + float(max_commute_km)) / 2.0)


def curve_basis(d_km: np.ndarray, max_commute_km: float) -> np.ndarray:
    """Piecewise-linear basis of the weight curve, shape ``(n, 3)`` (short, middle, long).

    With anchors ``a1 < a2 < a3`` (:func:`curve_anchors_km`): ``short`` is 1 up to ``a1`` and
    falls to 0 at ``a2``; ``middle`` rises 0 -> 1 between ``a1`` and ``a2`` and falls to 0 at
    ``a3``; ``long`` rises 0 -> 1 between ``a2`` and ``a3`` and falls to 0 at ``max_commute_km``.
    Every column is 0 beyond its end. The curve ``basis @ (short, middle, long)`` is continuous
    and, for a non-increasing triple, non-increasing.
    """
    d = np.asarray(d_km, dtype=float)
    if (d < 0).any() or np.isnan(d).any():
        raise ValueError("curve_basis: distances must be non-negative numbers")
    a1, a2, a3 = curve_anchors_km(max_commute_km)
    short = np.interp(d, [a1, a2], [1.0, 0.0])
    middle = np.interp(d, [a1, a2, a3], [0.0, 1.0, 0.0], left=0.0, right=0.0)
    long = np.interp(d, [a2, a3, float(max_commute_km)], [0.0, 1.0, 0.0], left=0.0, right=0.0)
    return np.column_stack([short, middle, long])


def band_basis(d_km: np.ndarray, max_commute_km: float) -> np.ndarray:
    """The design's original step rule (comparison run only): indicators of ``d < 25``,
    ``25 <= d < 50`` and ``50 <= d <= max_commute_km``, shape ``(n, 3)``."""
    curve_anchors_km(max_commute_km)  # validates max_commute_km
    d = np.asarray(d_km, dtype=float)
    if (d < 0).any() or np.isnan(d).any():
        raise ValueError("band_basis: distances must be non-negative numbers")
    edge_mid, edge_long = BAND_EDGES_KM[1], BAND_EDGES_KM[2]
    return np.column_stack([d < edge_mid, (d >= edge_mid) & (d < edge_long),
                            (d >= edge_long) & (d <= max_commute_km)]).astype(float)


def _check_destinations(destinations: pd.DataFrame) -> None:
    _require_columns(destinations, ["destination_id", "x", "y"], "destinations")
    _require_unique(destinations, "destination_id", "destinations")


def pair_coefficients(gemeinden: pd.DataFrame, destinations: pd.DataFrame, rule: str,
                      max_commute_km: float) -> pd.DataFrame:
    """The three fixed curve coefficients of every (Kreis, destination) pair: :data:`PAIR_COLUMNS`.

    ``distance_km`` is the straight line from the population-weighted Kreis centroid to the
    destination. Rule ``commune_curve``: coefficients are the ``ewz``-weighted mean of
    :func:`curve_basis` over the communes of the Kreis (distance from each commune centroid;
    communes with ``ewz`` 0 carry no weight); ``distance_km`` is then reporting only. Rule
    ``centroid_bands``: :func:`band_basis` of ``distance_km``. Any other rule raises.
    """
    if rule not in (WEIGHT_RULE_COMMUNE_CURVE, WEIGHT_RULE_CENTROID_BANDS):
        raise ValueError(f"unknown weight rule {rule!r}; expected {WEIGHT_RULE_COMMUNE_CURVE!r} "
                         f"or {WEIGHT_RULE_CENTROID_BANDS!r}")
    curve_anchors_km(max_commute_km)
    _require_columns(gemeinden, ["ags8", "ewz", "x", "y"], "gemeinden")
    _require_finite(gemeinden, ["ewz", "x", "y"], "ags8", "gemeinden")
    _check_destinations(destinations)
    _require_finite(destinations, ["x", "y"], "destination_id", "destinations")
    centroids = population_weighted_kreis_centroids(gemeinden)

    populated = gemeinden[gemeinden["ewz"] > 0]
    n_unpopulated = len(gemeinden) - len(populated)
    LOGGER.info("[student_geometry] pair coefficients: %d of %d communes with ewz == 0 carry no weight "
                "(%.1f%%)", n_unpopulated, len(gemeinden), 100.0 * n_unpopulated / max(len(gemeinden), 1))
    ars5_populated = populated["ags8"].astype(str).str[:5].to_numpy()
    weight = populated["ewz"].to_numpy(dtype=float)
    kreis_index = pd.Index(centroids["ars5"])
    positions = kreis_index.get_indexer(ars5_populated)
    total_weight = centroids["ewz"].to_numpy(dtype=float)

    frames = []
    for destination_id, dx, dy in zip(destinations["destination_id"], destinations["x"], destinations["y"]):
        distance_km = np.hypot(centroids["x"].to_numpy() - dx, centroids["y"].to_numpy() - dy) / 1000.0
        if rule == WEIGHT_RULE_CENTROID_BANDS:
            coefficients = band_basis(distance_km, max_commute_km)
        else:
            commune_km = np.hypot(populated["x"].to_numpy(dtype=float) - dx,
                                  populated["y"].to_numpy(dtype=float) - dy) / 1000.0
            weighted = curve_basis(commune_km, max_commute_km) * weight[:, None]
            summed = np.zeros((len(centroids), 3))
            np.add.at(summed, positions, weighted)
            coefficients = summed / total_weight[:, None]
        frames.append(pd.DataFrame({
            "ars5": centroids["ars5"], "destination_id": destination_id, "distance_km": distance_km,
            "coeff_short": coefficients[:, 0], "coeff_middle": coefficients[:, 1],
            "coeff_long": coefficients[:, 2]}))
    pairs = pd.concat(frames, ignore_index=True)[PAIR_COLUMNS] if frames else pd.DataFrame(columns=PAIR_COLUMNS)
    LOGGER.info("[student_geometry] pair coefficients (rule %s, max_commute_km %g): %d Kreise x %d destinations, "
                "%d pairs with a non-zero coefficient (%.1f%%)", rule, max_commute_km, len(centroids),
                len(destinations), int((pairs[PAIR_COLUMNS[3:]].sum(axis=1) > 0).sum()),
                100.0 * (pairs[PAIR_COLUMNS[3:]].sum(axis=1) > 0).mean() if len(pairs) else 0.0)
    return pairs


def kreise_within_reach(gemeinden: pd.DataFrame, destinations: pd.DataFrame, destination_ids,
                        max_km: float) -> list[str]:
    """Sorted ``ars5`` of the Kreise with at least one populated commune (``ewz > 0``) whose centroid
    lies within ``max_km`` of one of the listed destinations."""
    _require_columns(gemeinden, ["ags8", "ewz", "x", "y"], "gemeinden")
    _require_finite(gemeinden, ["ewz", "x", "y"], "ags8", "gemeinden")
    _check_destinations(destinations)
    _require_finite(destinations, ["x", "y"], "destination_id", "destinations")
    wanted = [str(i) for i in destination_ids]
    unknown = sorted(set(wanted) - set(destinations["destination_id"].astype(str)))
    if unknown:
        raise ValueError(f"destination_ids not in destinations: {unknown}")
    chosen = destinations[destinations["destination_id"].astype(str).isin(wanted)]
    populated = gemeinden[gemeinden["ewz"] > 0]
    px = populated["x"].to_numpy(dtype=float)
    py = populated["y"].to_numpy(dtype=float)
    inside = np.zeros(len(populated), dtype=bool)
    for dx, dy in zip(chosen["x"], chosen["y"]):
        inside |= np.hypot(px - dx, py - dy) / 1000.0 <= max_km
    reach = sorted(populated.loc[inside, "ags8"].astype(str).str[:5].unique().tolist())
    LOGGER.info("[student_geometry] reach %g km of %d destination(s): %d Kreise, %d communes in reach; "
                "%d unpopulated communes (ewz == 0) skipped", max_km, len(chosen), len(reach), int(inside.sum()),
                len(gemeinden) - len(populated))
    return reach
