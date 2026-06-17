"""Type and capacitate a cell's ALKIS footprints, census-calibrated."""
from __future__ import annotations
import numpy as np, pandas as pd
from braunschweig.synthesis.locations.cell_building_signals import THREE_CLASSES


def assign_building_types(footprints: pd.DataFrame, geb_counts: dict, rng) -> pd.DataFrame:
    out = footprints.copy().reset_index(drop=True)
    k = len(out)
    if k == 0:
        out["btype"] = pd.Series(dtype=object)
        return out
    total = sum(max(0.0, float(geb_counts.get(c, 0.0))) for c in THREE_CLASSES)
    if total <= 0:
        out["btype"] = "efh_zfh"
        return out
    n_mfh = int(round(k * max(0.0, geb_counts.get("mfh", 0.0)) / total))
    n_sonst = int(round(k * max(0.0, geb_counts.get("sonst", 0.0)) / total))
    n_mfh = min(n_mfh, k)
    n_sonst = min(n_sonst, k - n_mfh)
    order = out["area_m2"].fillna(0).to_numpy().argsort()[::-1]  # largest first
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
    for cls in ("efh_zfh", "mfh", "sonst"):
        share = (max(0.0, whg_by_type.get(cls, 0.0)) / tot_whg) if tot_whg > 0 else 0.0
        n = int(round(occ * share))
        b = typed[typed["btype"] == cls]
        if n <= 0 or len(b) == 0:
            continue
        if cls == "efh_zfh":
            caps = np.ones(len(b), dtype=int)            # ~1 dwelling per EFH/ZFH building
        else:
            w = b["area_m2"].fillna(0).to_numpy().astype(float)
            w = w / w.sum() if w.sum() > 0 else np.ones(len(b)) / len(b)
            caps = np.maximum(1, np.round(w * n)).astype(int)
        # trim/extend caps to exactly n (largest-area buildings absorb the remainder)
        order = b["area_m2"].fillna(0).to_numpy().argsort()[::-1]
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
