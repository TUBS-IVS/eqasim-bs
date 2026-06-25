"""Pure fit helpers for the detour/circuity calibration: exp-decay curve fit and
the convergence-driven sample-size stop rule (with a minimum-samples floor)."""
from __future__ import annotations

import numpy as np
from scipy.optimize import curve_fit


def _model(d, c_inf, a, tau):
    return c_inf + a * np.exp(-d / tau)


def fit_circuity_curve(euclidean_km, routed_km):
    """Fit c(d)=c_inf+a*exp(-d/tau) to the per-pair ratio routed/euclidean.

    Returns {"c_inf","a","tau","r2","n"}. Bounds enforce c_inf>=1, a>=0, tau>0.
    """
    d = np.asarray(euclidean_km, dtype=float)
    r = np.asarray(routed_km, dtype=float)
    keep = d > 1e-6
    d, r = d[keep], r[keep]
    ratio = r / d
    popt, _ = curve_fit(
        _model, d, ratio,
        p0=(1.2, 0.5, 2.0),
        bounds=((1.0, 0.0, 1e-3), (3.0, 5.0, 100.0)),
        maxfev=20000,
    )
    pred = _model(d, *popt)
    ss_res = float(np.sum((ratio - pred) ** 2))
    ss_tot = float(np.sum((ratio - ratio.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"c_inf": float(popt[0]), "a": float(popt[1]), "tau": float(popt[2]),
            "r2": r2, "n": int(d.size)}


def params_changed_within(prev, cur, tol):
    """True if every fitted parameter changed by < tol (relative) vs prev."""
    for k in ("c_inf", "a", "tau"):
        denom = max(abs(prev[k]), 1e-9)
        if abs(cur[k] - prev[k]) / denom >= tol:
            return False
    return True


class ConvergenceTracker:
    """Stop when params are stable for `patience` consecutive rounds, but only
    once the sample has reached `min_samples` (floor guards premature stop)."""

    def __init__(self, min_samples, tol, patience):
        self.min_samples = int(min_samples)
        self.tol = float(tol)
        self.patience = int(patience)
        self._prev = None
        self._stable_streak = 0
        self.history: list[dict] = []

    def update(self, n_samples, params):
        self.history.append({"n": int(n_samples), **params})
        converged = False
        if self._prev is not None and params_changed_within(self._prev, params, self.tol):
            self._stable_streak += 1
        else:
            self._stable_streak = 0
        self._prev = dict(params)
        if n_samples >= self.min_samples and self._stable_streak >= self.patience:
            converged = True
        return converged


# ---------------------------------------------------------------------------
# Graph construction + network routing helpers
# ---------------------------------------------------------------------------
import gzip
import xml.etree.ElementTree as ET

from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree


def build_graph_from_edges(node_xy, edges):
    """Symmetric CSR adjacency (weights in metres) from node coords + edges."""
    node_xy = np.asarray(node_xy, dtype=float)
    n = node_xy.shape[0]
    rows, cols, data = [], [], []
    for u, v, length_m in edges:
        rows += [u, v]
        cols += [v, u]
        data += [float(length_m), float(length_m)]
    csr = csr_matrix((data, (rows, cols)), shape=(n, n))
    return csr, node_xy


def read_matsim_network(path_xml_gz):
    """Stream-parse a MATSim network.xml.gz into (node_xy, edges, node_ids).

    Edges use link ``length`` (metres). Repo-local matsim package import is
    avoided (it shadows matsim-tools); xml.etree iterparse is used directly.
    """
    node_ids, node_xy = [], []
    id_to_index = {}
    edges = []
    opener = gzip.open if str(path_xml_gz).endswith(".gz") else open
    with opener(path_xml_gz, "rb") as fh:
        for _, elem in ET.iterparse(fh, events=("end",)):
            if elem.tag == "node":
                nid = elem.get("id")
                id_to_index[nid] = len(node_ids)
                node_ids.append(nid)
                node_xy.append((float(elem.get("x")), float(elem.get("y"))))
                elem.clear()
            elif elem.tag == "link":
                u = id_to_index.get(elem.get("from"))
                v = id_to_index.get(elem.get("to"))
                if u is not None and v is not None:
                    edges.append((u, v, float(elem.get("length"))))
                elem.clear()
    return np.array(node_xy, dtype=float), edges, node_ids


def read_osm_network_pyrosm(pbf_path, network_type="walking", bbox=None):
    """OSM road/walk network from a PBF via pyrosm -> (node_xy, edges, node_ids).

    Parameters
    ----------
    pbf_path : str or Path
        Path to the OSM PBF file.
    network_type : str
        pyrosm network type: ``"walking"`` or ``"driving"``.  Passed directly to
        ``OSM.get_network(network_type=...)``.
    bbox : list or None
        Optional bounding box [minx, miny, maxx, maxy] in WGS84 (EPSG:4326)
        passed to pyrosm ``OSM(bounding_box=bbox)``.  When None the whole PBF is
        read (slow for large files).

    Returns
    -------
    node_xy : np.ndarray, shape (N, 2), EPSG:25832 metres
    edges : list of (u_idx, v_idx, length_m)
    node_ids : list of OSM node ids (in the same order as node_xy rows)

    Edge length is the geometry length in the project metric CRS (EPSG:25832).
    Import of pyrosm is deferred to keep module-level import light.
    """
    from pyrosm import OSM
    osm = OSM(pbf_path, bounding_box=bbox)
    nodes, edges_gdf = osm.get_network(network_type=network_type, nodes=True)
    nodes = nodes.to_crs(25832)
    edges_gdf = edges_gdf.to_crs(25832)
    id_to_index = {nid: i for i, nid in enumerate(nodes["id"].tolist())}
    node_xy = np.column_stack([nodes.geometry.x.values, nodes.geometry.y.values])
    edges = []
    for u_osm, v_osm, geom in zip(edges_gdf["u"], edges_gdf["v"], edges_gdf.geometry):
        u, v = id_to_index.get(u_osm), id_to_index.get(v_osm)
        if u is not None and v is not None and geom is not None:
            edges.append((u, v, float(geom.length)))
    return node_xy, edges, list(id_to_index.keys())


def read_walk_network_pyrosm(pbf_path, bbox=None):
    """Walking network from an OSM PBF via pyrosm -> (node_xy, edges, node_ids).

    Thin wrapper around :func:`read_osm_network_pyrosm` with ``network_type="walking"``.
    Kept for backwards compatibility.
    """
    return read_osm_network_pyrosm(pbf_path, network_type="walking", bbox=bbox)


def bbox_from_home_locations(home_xy_m: np.ndarray, margin_m: float = 5000.0) -> dict:
    """Derive an axis-aligned bounding box from home coordinates in EPSG:25832.

    Parameters
    ----------
    home_xy_m : np.ndarray, shape (N, 2)
        Home point coordinates in EPSG:25832 metres.
    margin_m : float
        Extra margin to add on each side (default 5000 m = 5 km).

    Returns
    -------
    dict with keys:
      ``metric``  – (minx, miny, maxx, maxy) in EPSG:25832
      ``wgs84``   – [minx, miny, maxx, maxy] in EPSG:4326 (for pyrosm bounding_box)
    """
    import pyproj
    xs = home_xy_m[:, 0]
    ys = home_xy_m[:, 1]
    minx = float(xs.min()) - margin_m
    miny = float(ys.min()) - margin_m
    maxx = float(xs.max()) + margin_m
    maxy = float(ys.max()) + margin_m
    # Convert corners to WGS84 for pyrosm bounding_box.
    transformer = pyproj.Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    lon_min, lat_min = transformer.transform(minx, miny)
    lon_max, lat_max = transformer.transform(maxx, maxy)
    return {
        "metric": (minx, miny, maxx, maxy),
        "wgs84": [lon_min, lat_min, lon_max, lat_max],
    }


def clip_od_to_bbox(
    origins_xy: np.ndarray,
    dests_xy: np.ndarray,
    commune_ids: np.ndarray,
    bbox_m: tuple,
    node_xy: np.ndarray,
    max_snap_m: float = 500.0,
    label: str = "",
) -> tuple:
    """Drop OD pairs whose endpoints lie outside the metric bbox or snap too far.

    Parameters
    ----------
    origins_xy : np.ndarray, shape (N, 2)
    dests_xy   : np.ndarray, shape (N, 2)
    commune_ids: np.ndarray, shape (N,), dtype object
    bbox_m     : (minx, miny, maxx, maxy) in EPSG:25832
    node_xy    : np.ndarray, shape (M, 2)  — graph node coordinates (EPSG:25832)
    max_snap_m : float  — maximum allowed snap distance in metres (default 500)
    label      : str    — network label used in log messages

    Returns
    -------
    origins_xy_clipped, dests_xy_clipped, commune_ids_clipped  (same dtype)
    """
    from scipy.spatial import cKDTree
    minx, miny, maxx, maxy = bbox_m
    n_total = len(origins_xy)

    # --- Spatial bbox filter ---
    ox, oy = origins_xy[:, 0], origins_xy[:, 1]
    dx, dy = dests_xy[:, 0], dests_xy[:, 1]
    in_bbox = (
        (ox >= minx) & (ox <= maxx) & (oy >= miny) & (oy <= maxy) &
        (dx >= minx) & (dx <= maxx) & (dy >= miny) & (dy <= maxy)
    )
    n_outside = int((~in_bbox).sum())

    # --- Snap-distance filter (against the graph nodes) ---
    tree = cKDTree(node_xy)
    snap_o, _ = tree.query(origins_xy[in_bbox])
    snap_d, _ = tree.query(dests_xy[in_bbox])
    snap_ok = (snap_o <= max_snap_m) & (snap_d <= max_snap_m)
    n_snap_fail = int((~snap_ok).sum())

    keep = np.where(in_bbox)[0][snap_ok]
    n_kept = len(keep)
    n_dropped = n_total - n_kept

    import logging as _logging
    _log = _logging.getLogger(__name__)
    _log.info(
        "[circuity][%s] OD clip: kept %d/%d (%.1f%%), "
        "dropped %d outside bbox, %d snap>%.0fm",
        label, n_kept, n_total, 100.0 * n_kept / max(n_total, 1),
        n_outside, n_snap_fail, max_snap_m,
    )
    if n_kept == 0:
        _log.warning(
            "[circuity][%s] ALL OD pairs dropped after bbox+snap clip (n_total=%d). "
            "Check that bbox_m covers the study area and that --max-snap-m is large enough.",
            label, n_total,
        )

    return (
        origins_xy[keep],
        dests_xy[keep],
        commune_ids[keep],
    )


def route_lengths_km(csr, node_xy, origins_xy, dests_xy):
    """Network shortest-path length (km) for paired origin/dest coords.

    Snaps each endpoint to the nearest graph node (cKDTree). Pairs with no path
    (disconnected) are flagged in the returned boolean mask and carry NaN.
    """
    tree = cKDTree(node_xy)
    _, o_idx = tree.query(np.asarray(origins_xy, dtype=float))
    _, d_idx = tree.query(np.asarray(dests_xy, dtype=float))
    routed_km = np.full(len(o_idx), np.nan, dtype=float)
    fail = np.zeros(len(o_idx), dtype=bool)
    for src in np.unique(o_idx):
        members = np.where(o_idx == src)[0]
        dist = dijkstra(csr, directed=False, indices=int(src))
        for m in members:
            dm = dist[d_idx[m]]
            if np.isinf(dm):
                fail[m] = True
            else:
                routed_km[m] = dm / 1000.0
    return routed_km, fail


# ---------------------------------------------------------------------------
# Accepted-index accumulator helper
# ---------------------------------------------------------------------------

def accumulate_accepted_indices(cum_pool_indices: list, batch_idx: np.ndarray,
                                keep_mask: np.ndarray) -> None:
    """Append the pool indices of accepted pairs to the running accumulator list.

    Given `batch_idx` (indices into the full OD pool for a routing batch) and
    `keep_mask` (boolean mask of length len(batch_idx); True = accepted pair),
    appends `batch_idx[keep_mask]` to `cum_pool_indices` in place.

    Maintains the invariant:
      pool_euclidean_km[cum_pool_indices] == cum_euclidean_km  (element-wise)

    so the accepted sample can always be traced back to its origin commune_id
    (and hence its origin RS7 class) via ``commune_ids[cum_pool_indices]``.
    """
    accepted = batch_idx[keep_mask]
    cum_pool_indices.extend(accepted.tolist())


# ---------------------------------------------------------------------------
# Distance-stratified OD sampling
# ---------------------------------------------------------------------------

STRATA_EDGES_KM = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, float("inf"))


def stratified_sample(origins_xy, dests_xy, n_target, rng, edges_km=STRATA_EDGES_KM):
    """Draw ~n_target row indices spread evenly across euclidean-distance strata.

    Each non-empty stratum gets an equal target share; strata with fewer members
    than their share contribute all of theirs (no oversampling beyond available).
    """
    o = np.asarray(origins_xy, dtype=float)
    d = np.asarray(dests_xy, dtype=float)
    dist_km = np.linalg.norm(d - o, axis=1) / 1000.0
    inner = np.asarray(edges_km[1:-1], dtype=float)
    strata = np.digitize(dist_km, inner)
    present = [s for s in np.unique(strata)]
    per = max(1, n_target // max(len(present), 1))
    picks = []
    for s in present:
        members = np.where(strata == s)[0]
        take = min(per, members.size)
        picks.append(rng.choice(members, size=take, replace=False))
    return np.concatenate(picks) if picks else np.array([], dtype=int)
