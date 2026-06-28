"""Function-aware secondary `other` potential (Part A of the smart-other design).

`other` (MiD W_ZWECK 5 private Erledigungen / 6 Bringen-Holen / 10 anderer Zweck)
is ~46% errand (service buildings) + ~54% broad (escort + catch-all). The raw
`potential_generic = volume_m3 x class_weight` is function-blind and dominated by
industrial-volume giants (VW-Werk 26.7M). This derives a capped, whitelist-boosted
`potential_other` per building. See
docs/superpowers/specs/2026-06-28-smart-other-potential-design.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def derive_other_potential(df_buildings, mapping, *, broad_share, errand_share,
                           min_volume_m3, cap_percentile, class_col="bosserhof_class_clean"):
    """Return (potential_other Series aligned to df_buildings, stats dict).

    potential_other = min(potential_generic, cap) * (broad_share + errand_share * 1(whitelist)),
    zeroed where volume_m3 < min_volume_m3. cap = the cap_percentile quantile of
    potential_generic over whitelist buildings, applied to all buildings.
    """
    generic = pd.to_numeric(df_buildings["potential_generic"], errors="coerce").astype(float).to_numpy()
    volume = pd.to_numeric(df_buildings["volume_m3"], errors="coerce").astype(float).to_numpy()
    classes = df_buildings[class_col].astype(str)

    known = set(mapping["bosserhof_class"])
    whitelist = set(mapping.loc[mapping["other_destination"], "bosserhof_class"])
    is_white = classes.isin(whitelist).to_numpy()
    is_known = classes.isin(known).to_numpy()

    # cap from whitelist generic (fall back to all-building quantile if no whitelist).
    white_generic = generic[is_white]
    if white_generic.size:
        cap = float(np.nanquantile(white_generic, cap_percentile))
    else:
        cap = float(np.nanquantile(generic, cap_percentile))
        print("WARNING: [secondary_other_potential] no whitelist buildings; "
              "cap derived from all-building generic quantile")

    # Compute potential per building:
    # - whitelist buildings: min(generic, cap) * 1.0
    #   They participate proportionally up to the cap (prevents giant whitelist buildings
    #   from dominating).
    # - non-whitelist known buildings: cap * broad_share
    #   They always participate at cap level downweighted by broad_share; their raw generic
    #   is irrelevant because their primary function is not errand-oriented.
    # - unknown class buildings: min(generic, cap) * broad_share
    #   Treated conservatively like broad (no errand boost), but capped.
    is_nonwhitelist_known = (~is_white) & is_known
    is_unknown = ~is_known

    capped_generic = np.minimum(generic, cap)
    pot = np.where(is_white,
                   capped_generic * 1.0,
                   np.where(is_nonwhitelist_known,
                            cap * broad_share,
                            capped_generic * broad_share))

    tiny = volume < float(min_volume_m3)
    pot[tiny] = 0.0

    stats = {
        "cap_value": cap,
        "n_whitelist": int(is_white.sum()),
        "n_nonwhitelist": int(is_nonwhitelist_known.sum()),
        "n_unknown_class": int(is_unknown.sum()),
        "n_tiny": int(tiny.sum()),
    }
    return pd.Series(pot, index=df_buildings.index, name="potential_other"), stats
