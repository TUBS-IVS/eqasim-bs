"""Cell lookup over the committed SrV mode-share table (eqasim-bs#453)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_mode_by_distance_band_table import MODES, TABLE_COLUMNS, load_table
from braunschweig.synthesis.outside_tour_modes.config_keys import ALWAYS_AVAILABLE_MODES


class ModeShareTable:
    """Per purpose group, the sorted bands ``(lower_km, upper_km, label, {mode: share})``."""

    def __init__(self, bands_by_group: dict):
        self._bands = bands_by_group

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> "ModeShareTable":
        missing = [column for column in TABLE_COLUMNS if column not in frame.columns]
        if missing:
            raise ValueError(f"[outside_tour_modes] mode-share table lacks the columns {missing}")
        bands_by_group = {}
        for group, rows in frame.groupby("purpose_group", sort=True):
            bands = []
            for (lower, upper, label), cell in rows.groupby(["band_lower_km", "band_upper_km", "band"], sort=True):
                shares = {mode: 0.0 for mode in MODES}
                shares.update({str(mode): float(share) for mode, share in zip(cell["mode"], cell["share"])})
                bands.append((float(lower), float(upper), str(label), shares))
            bands.sort(key=lambda band: band[0])
            bands_by_group[str(group)] = bands
        return cls(bands_by_group)

    @classmethod
    def from_directory(cls, srv_dir) -> "ModeShareTable":
        return cls.from_frame(load_table(srv_dir))

    @property
    def groups(self) -> tuple:
        return tuple(sorted(self._bands))

    def cell(self, group: str, km: float) -> tuple:
        if group not in self._bands:
            raise KeyError(f"[outside_tour_modes] purpose group {group!r} is not in the mode-share table "
                           f"(groups: {self.groups})")
        if not np.isfinite(km) or km < 0.0:
            raise ValueError(f"[outside_tour_modes] distance {km!r} km is not a non-negative finite number")
        for lower, upper, label, shares in self._bands[group]:
            if lower <= km < upper:
                return label, shares
        raise ValueError(f"[outside_tour_modes] distance {km} km falls in no band of group {group!r}")

    def top_cell(self, group: str) -> tuple:
        if group not in self._bands:
            raise KeyError(f"[outside_tour_modes] purpose group {group!r} is not in the mode-share table")
        _, _, label, shares = self._bands[group][-1]
        return label, shares

    @staticmethod
    def plausible_modes(shares: dict, min_share: float) -> tuple:
        return tuple(mode for mode in MODES if shares.get(mode, 0.0) >= min_share)

    def assert_always_available_mode_plausible(self, min_share: float) -> None:
        """Fail early: a cell where neither pt nor walk is plausible could leave a person with no candidate."""
        offending = [f"{group}/{label}" for group, bands in self._bands.items()
                     for _, _, label, shares in bands
                     if not any(shares.get(mode, 0.0) >= min_share for mode in ALWAYS_AVAILABLE_MODES)]
        if offending:
            raise ValueError(f"[outside_tour_modes] cells without a plausible always-available mode "
                             f"({ALWAYS_AVAILABLE_MODES}) at min_share {min_share}: {offending}")
