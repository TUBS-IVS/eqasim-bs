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


def read_walk_network_pyrosm(pbf_path, bbox=None):
    """Walking network from an OSM PBF via pyrosm -> (node_xy, edges, node_ids).

    Edge length is the geometry length in the project metric CRS (EPSG:25832).
    Import of pyrosm is deferred to keep module-level import light.
    """
    from pyrosm import OSM
    osm = OSM(pbf_path, bounding_box=bbox)
    nodes, edges_gdf = osm.get_network(network_type="walking", nodes=True)
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
