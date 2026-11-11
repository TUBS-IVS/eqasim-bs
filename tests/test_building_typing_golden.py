"""Golden test: the new slot apportionment equals the pre-audit one where it was sound.

``_old_build_slots`` is a verbatim copy of ``build_slots`` as of origin/main before the
repository-audit B4 fix (F1). The two differ only for cells in which a census class with
dwellings has no typed footprint; on every other cell all slot frames must be identical.
"""
import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.locations import building_typing as bt
from braunschweig.synthesis.locations.building_typing import _expand_sizes, building_volume


def _old_build_slots(typed, whg_by_type, occupied, size_hist, rng):
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


def _random_sound_cell(rng):
    """A random cell where every census class with dwellings has a typed footprint."""
    n_fp = int(rng.randint(1, 9))
    btype = rng.choice(bt.SLOT_CLASSES, size=n_fp)
    typed = pd.DataFrame({
        "building_id": np.arange(100, 100 + n_fp),
        "area_m2": rng.uniform(40, 900, n_fp).round(1),
        "height_m": np.where(rng.rand(n_fp) < 0.2, np.nan, rng.uniform(3, 30, n_fp)),
        "btype": btype})
    present = set(btype)
    whg = {c: (float(rng.randint(1, 30)) if c in present and rng.rand() < 0.85 else 0.0)
           for c in bt.SLOT_CLASSES}
    occupied = float(rng.choice([0.0, rng.randint(1, 40), rng.randint(1, 40) + 0.5]))
    size_hist = [(float(m), float(rng.randint(1, 8))) for m in rng.choice(
        [35, 55, 75, 95, 115], size=int(rng.randint(0, 4)), replace=False)]
    return typed, whg, occupied, size_hist


@pytest.mark.parametrize("seed", range(5))
def test_new_apportionment_matches_old_where_every_dwelling_class_has_a_footprint(seed):
    rng = np.random.RandomState(seed)
    for _ in range(60):
        typed, whg, occupied, size_hist = _random_sound_cell(rng)
        old = _old_build_slots(typed, whg, occupied, size_hist, None)
        for builder in (bt.build_slots, bt.build_slots_arrays):
            new = builder(typed, whg, occupied, size_hist, None)
            pd.testing.assert_frame_equal(new, old, check_dtype=False)
        per_class = old["btype"].value_counts().reindex(list(bt.SLOT_CLASSES), fill_value=0)
        counted = bt.slot_apportionment(typed, whg, occupied)
        assert list(per_class) == list(counted.type_slots)
        assert not counted.reapportioned
