"""Type and capacitate a cell's ALKIS footprints, census-calibrated."""
from __future__ import annotations
import numpy as np, pandas as pd
from braunschweig.synthesis.locations.cell_building_signals import THREE_CLASSES

FLOOR_HEIGHT_M = 3.0
# In cells with NO census building-type counts (suppression), a footprint of at
# least this many floors is typed MFH from its LoD2 height alone. =4 floors (~10.5 m)
# is the classic MFH-block cutoff; it was the single-peaked optimum (type-fidelity max,
# over-capacity min) in the Salzgitter real-population sweep (scripts/lod2_height_sweep.py),
# beating both lower thresholds (which mislabel 2-storey EFH as MFH) and higher ones.
MFH_MIN_FLOORS = 4


def building_volume(area_m2, height_m, floor_height=FLOOR_HEIGHT_M):
    a = float(area_m2) if area_m2 == area_m2 else 0.0
    floors = 1
    if height_m is not None and height_m == height_m and height_m > 0:   # not None, not NaN, positive
        floors = max(1, int(round(float(height_m) / floor_height)))
    return a * floors


def assign_building_types(footprints: pd.DataFrame, geb_counts: dict, rng) -> pd.DataFrame:
    out = footprints.copy().reset_index(drop=True)
    k = len(out)
    if k == 0:
        out["btype"] = pd.Series(dtype=object)
        return out
    total = sum(max(0.0, float(geb_counts.get(c, 0.0))) for c in THREE_CLASSES)
    if total <= 0:
        # No census building-type counts for this cell (suppression). Fall back to
        # LoD2 height as the sole typing signal: a footprint of >= MFH_MIN_FLOORS
        # floors is typed MFH, everything else EFH. `sonst` is intentionally NOT
        # produced here -- without census signal there is nothing to distinguish a
        # "sonstiges" building from an MFH by geometry alone. When height is absent
        # (no column) or entirely missing (all-NaN), this reduces to the original
        # byte-identical all-EFH fallback, since building_volume(1.0, NaN/None) == 1.
        if "height_m" in out.columns and out["height_m"].notna().any():
            floors = np.array([building_volume(1.0, h) for h in out["height_m"].to_numpy()])
            out["btype"] = np.where(floors >= MFH_MIN_FLOORS, "mfh", "efh_zfh").astype(object)
        else:
            out["btype"] = "efh_zfh"
        return out
    n_mfh = int(round(k * max(0.0, geb_counts.get("mfh", 0.0)) / total))
    n_sonst = int(round(k * max(0.0, geb_counts.get("sonst", 0.0)) / total))
    n_mfh = min(n_mfh, k)
    n_sonst = min(n_sonst, k - n_mfh)
    if "area_m2" in out.columns and "height_m" in out.columns:
        key = np.array([building_volume(a, h) for a, h in zip(out["area_m2"].to_numpy(), out["height_m"].to_numpy())], float)
    else:
        key = out["area_m2"].fillna(0).to_numpy()
    order = key.argsort()[::-1]   # largest volume first -> MFH
    btype = np.array(["efh_zfh"] * k, dtype=object)
    btype[order[:n_mfh]] = "mfh"                 # largest -> MFH
    btype[order[k - n_sonst:]] = "sonst"         # smallest -> sonstiges
    out["btype"] = btype
    return out


def _expand_sizes(size_hist):
    sizes = []
    for mid, cnt in sorted(size_hist, reverse=True):  # largest first
        sizes.extend([float(mid)] * int(round(cnt)))
    return sizes


def build_slots(typed, whg_by_type, occupied, size_hist, rng):
    rows = []
    # target occupied dwellings per type = dwelling-mix share * occupied total
    tot_whg = sum(max(0.0, float(whg_by_type.get(c, 0.0))) for c in ("efh_zfh", "mfh", "sonst"))
    occ = float(occupied) if occupied and occupied > 0 else tot_whg
    # --- Hamilton (largest-remainder) apportionment ---
    # guarantees sum(n_i) == T = round(occ) regardless of share fractions
    CLASSES = ("efh_zfh", "mfh", "sonst")
    T = int(round(occ))
    if tot_whg > 0:
        shares = [max(0.0, float(whg_by_type.get(c, 0.0))) / tot_whg for c in CLASSES]
    elif len(typed) > 0:
        shares = [(typed["btype"] == c).sum() / len(typed) for c in CLASSES]
    else:
        shares = [0.0] * len(CLASSES)
    exacts = [occ * s for s in shares]
    floors = [int(e) for e in exacts]
    remainders = [e - f for e, f in zip(exacts, floors)]
    leftover = T - sum(floors)
    # distribute leftover to types with largest fractional remainders
    order_rem = sorted(range(len(CLASSES)), key=lambda i: remainders[i], reverse=True)
    ns = list(floors)
    for i in range(leftover):
        ns[order_rem[i % len(order_rem)]] += 1
    type_n = {cls: ns[idx] for idx, cls in enumerate(CLASSES)}
    for cls in CLASSES:
        n = type_n[cls]
        b = typed[typed["btype"] == cls]
        if n <= 0 or len(b) == 0:
            continue
        if "height_m" in b.columns:
            w = np.array([building_volume(ar, hm) for ar, hm in zip(b["area_m2"].to_numpy(), b["height_m"].to_numpy())], float)
        else:
            w = b["area_m2"].fillna(0).to_numpy().astype(float)
        w = w / w.sum() if w.sum() > 0 else np.ones(len(b)) / len(b)
        caps = np.maximum(1, np.round(w * n)).astype(int)
        # trim/extend caps to exactly n (largest-volume/area buildings absorb the remainder)
        order = w.argsort()[::-1]
        bid = b["building_id"].to_numpy()
        slot_bids = []
        for j in order:
            slot_bids.extend([bid[j]] * caps[j])
        slot_bids = slot_bids[:n] if len(slot_bids) >= n else slot_bids + [bid[order[0]]] * (n - len(slot_bids))
        for sb in slot_bids:
            rows.append({"building_id": sb, "btype": cls})
    slots = pd.DataFrame(rows)
    if slots.empty:
        return pd.DataFrame(columns=["slot_id", "building_id", "btype", "size"])
    # assortative size: largest dwellings -> EFH, then MFH, then sonst
    sizes = _expand_sizes(size_hist)
    order_cls = {"efh_zfh": 0, "mfh": 1, "sonst": 2}
    slots = slots.sort_values("btype", key=lambda s: s.map(order_cls)).reset_index(drop=True)
    if len(sizes) >= len(slots):
        slots["size"] = sizes[:len(slots)]
    else:
        slots["size"] = (sizes + [sizes[-1]] * (len(slots) - len(sizes))) if sizes else 0.0
    slots["slot_id"] = np.arange(len(slots))
    return slots[["slot_id", "building_id", "btype", "size"]]


def build_slots_arrays(typed, whg_by_type, occupied, size_hist, rng):
    """Array-backed equivalent of :func:`build_slots` for the default-ON path.

    It preserves the three-class Hamilton allocation and slot order exactly while
    avoiding a DataFrame filter and sort for every building class.
    """
    classes = ("efh_zfh", "mfh", "sonst")
    total_dwellings = sum(max(0.0, float(whg_by_type.get(kind, 0.0))) for kind in classes)
    occupied_dwellings = float(occupied) if occupied and occupied > 0 else total_dwellings
    total_slots = int(round(occupied_dwellings))
    building_types = typed["btype"].to_numpy(dtype=object)
    positions_by_type = {kind: np.flatnonzero(building_types == kind) for kind in classes}
    if total_dwellings > 0:
        shares = [max(0.0, float(whg_by_type.get(kind, 0.0))) / total_dwellings for kind in classes]
    elif len(typed) > 0:
        shares = [len(positions_by_type[kind]) / len(typed) for kind in classes]
    else:
        shares = [0.0] * len(classes)
    exacts = [occupied_dwellings * share for share in shares]
    floors = [int(exact) for exact in exacts]
    remainders = [exact - floor for exact, floor in zip(exacts, floors)]
    leftover = total_slots - sum(floors)
    remainder_order = sorted(range(len(classes)), key=lambda index: remainders[index], reverse=True)
    type_slots = list(floors)
    for index in range(leftover):
        type_slots[remainder_order[index % len(remainder_order)]] += 1

    building_ids = typed["building_id"].to_numpy()
    areas = typed["area_m2"].to_numpy()
    heights = typed["height_m"].to_numpy() if "height_m" in typed.columns else None
    rows = []
    for class_index, kind in enumerate(classes):
        n_slots = type_slots[class_index]
        positions = positions_by_type[kind]
        if n_slots <= 0 or len(positions) == 0:
            continue
        if heights is not None:
            weights = np.array(
                [building_volume(areas[position], heights[position]) for position in positions], float
            )
        else:
            weights = pd.Series(areas[positions]).fillna(0).to_numpy().astype(float)
        weight_sum = weights.sum()
        weights = weights / weight_sum if weight_sum > 0 else np.ones(len(positions)) / len(positions)
        capacities = np.maximum(1, np.round(weights * n_slots)).astype(int)
        largest_first = weights.argsort()[::-1]
        ordered_buildings = []
        for relative_position in largest_first:
            ordered_buildings.extend(
                [building_ids[positions[relative_position]]] * capacities[relative_position]
            )
        if len(ordered_buildings) >= n_slots:
            ordered_buildings = ordered_buildings[:n_slots]
        else:
            ordered_buildings.extend(
                [building_ids[positions[largest_first[0]]]] * (n_slots - len(ordered_buildings))
            )
        rows.extend((building_id, kind) for building_id in ordered_buildings)

    slots = pd.DataFrame(rows, columns=["building_id", "btype"])
    if slots.empty:
        return pd.DataFrame(columns=["slot_id", "building_id", "btype", "size"])
    # Keep the legacy pandas quicksort permutation observable for equal mapped
    # class keys. Although rows are already grouped by class, omitting this sort
    # changes within-class slot order for larger frames and therefore assignments.
    class_order = {"efh_zfh": 0, "mfh": 1, "sonst": 2}
    slots = slots.sort_values(
        "btype", key=lambda series: series.map(class_order)
    ).reset_index(drop=True)
    sizes = _expand_sizes(size_hist)
    if len(sizes) >= len(slots):
        slot_sizes = sizes[:len(slots)]
    else:
        slot_sizes = sizes + [sizes[-1]] * (len(slots) - len(sizes)) if sizes else 0.0
    slots["size"] = slot_sizes
    slots["slot_id"] = np.arange(len(slots))
    return slots[["slot_id", "building_id", "btype", "size"]]
