"""Within-Kreis spatial income tilt from per-cell net cold rent (GAMMA layer).

Literature-grounded (see docs/superpowers/specs/2026-06-14-nettokaltmiete-income-tilt-design.md):
rent carries a weak income signal (area->individual Spearman ~0.32) and housing demand is
income-inelastic, so the rent->income map is concave (beta<1) and bounded. The index is
normalized to a household-weighted mean of 1 within each Kreis, so applying it preserves the
per-Kreis income mean exactly (pure within-Kreis redistribution).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_BETA = 0.3
DEFAULT_CLIP = 0.30


def _normalize_mean_one(index: pd.Series, kreis: pd.Series, weight: pd.Series) -> pd.Series:
    """Scale ``index`` within each Kreis so the weighted mean is exactly 1.0.

    Degenerate Kreise (total household weight == 0) are left unscaled (scale 1.0)
    and a warning is emitted; this is the single, explicit handling point for that case.
    """
    idx_vals = index.to_numpy(float)
    kreis_vals = kreis.to_numpy()
    weight_vals = weight.to_numpy(float)

    # Vectorized per-Kreis household-weighted mean via transform.
    s_kreis = pd.Series(kreis_vals)
    s_weight = pd.Series(weight_vals)
    s_index = pd.Series(idx_vals)

    wsum = s_weight.groupby(s_kreis).transform("sum").to_numpy()
    wmean = (
        (s_index * s_weight).groupby(s_kreis).transform("sum").to_numpy() / np.where(wsum > 0, wsum, 1.0)
    )

    # Identify degenerate Kreise (zero total weight) and warn once.
    zero_mask = wsum == 0
    if zero_mask.any():
        n_cells = int(zero_mask.sum())
        bad_kreise = pd.unique(s_kreis.to_numpy()[zero_mask])
        logger.warning(
            "[income_spatial_tilt] %d cell(s) in %d Kreis(e) have zero total household weight "
            "-> skipping normalization (scale 1.0) for: %s",
            n_cells, len(bad_kreise), bad_kreise,
        )
        wmean = np.where(zero_mask, 1.0, wmean)

    scale = np.where(wmean == 0, 1.0, wmean)
    return pd.Series(idx_vals / scale, index=index.index)


def build_renter_rent_index(cells: pd.DataFrame, *, rent_col: str, kreis_col: str,
                            weight_col: str, beta: float = DEFAULT_BETA) -> pd.DataFrame:
    """Per-cell renter income index = (rent/median_Kreis(rent))**beta, Kreis-mean-1 normalized.

    Cells with missing/non-positive rent get a neutral raw index of 1.0 (no tilt). Note that
    1.0 is the *raw* pre-normalization value; after Kreis-mean-1 normalization a neutral cell
    in a mixed Kreis (containing cells with real rent data) is rescaled slightly away from 1.0
    — this is correct and preserves the per-Kreis household-weighted-mean == 1 invariant.
    The returned frame is ``cells`` plus a ``renter_income_index`` column.
    """
    out = cells.copy()
    rent = pd.to_numeric(out[rent_col], errors="coerce")
    valid = rent > 0
    # Kreis median over valid rents only.
    med = (rent.where(valid).groupby(out[kreis_col]).transform("median"))
    raw = np.where(valid & (med > 0), (rent / med) ** float(beta), 1.0)
    raw = pd.Series(raw, index=out.index).fillna(1.0)
    missing_rate = float((~valid).mean())
    if missing_rate > 0:
        logger.info("[income_spatial_tilt] renter rent missing/zero in %.1f%% of cells "
                    "-> neutral index", 100 * missing_rate)
    out["renter_income_index"] = _normalize_mean_one(raw, out[kreis_col], out[weight_col])
    return out


def build_owner_income_index(cells: pd.DataFrame, *, quote_col: str, kreis_col: str,
                             weight_col: str, beta: float = DEFAULT_BETA) -> pd.DataFrame:
    """Per-cell owner income index = (quote/median_Kreis(quote))**beta, Kreis-mean-1 normalized.

    Cells with missing/non-positive Eigentuemerquote get a neutral raw index of 1.0 (no tilt).
    Note that 1.0 is the *raw* pre-normalization value; after Kreis-mean-1 normalization a
    neutral cell in a mixed Kreis is rescaled slightly away from 1.0 — this preserves the
    per-Kreis household-weighted-mean == 1 invariant. Higher ownership share -> more affluent
    area -> index > 1. The returned frame is ``cells`` plus an ``owner_income_index`` column.
    """
    out = cells.copy()
    q = pd.to_numeric(out[quote_col], errors="coerce")
    valid = q > 0
    med = q.where(valid).groupby(out[kreis_col]).transform("median")
    raw = np.where(valid & (med > 0), (q / med) ** float(beta), 1.0)
    raw = pd.Series(raw, index=out.index).fillna(1.0)
    out["owner_income_index"] = _normalize_mean_one(raw, out[kreis_col], out[weight_col])
    return out


def apply_spatial_income_tilt(frame: pd.DataFrame, cell_index: pd.DataFrame, *,
                               cell_col: str, kreis_col: str, tenure_col: str,
                               income_col: str = "household_income_eur",
                               clip: float = DEFAULT_CLIP) -> tuple[pd.DataFrame, dict]:
    """Apply the per-cell income index to ``frame`` (renter vs owner index by tenure),
    clipped to [1-clip, 1+clip], then re-normalized within each Kreis so the weighted mean
    income per Kreis is exactly unchanged. Returns (tilted_frame, diagnostics).

    The ``clip`` parameter bounds the factor BEFORE per-Kreis re-normalization. After
    re-normalization (which guarantees exact per-Kreis mean preservation), each Kreis is
    scaled by ``scale = base_sum / tilt_sum``. When a Kreis's clipped index mean departs
    from 1.0 (e.g. because many cells were clipped in the same direction), this scalar
    can push the realized per-household effective multiplier (``clipped * scale``) outside
    the nominal [1-clip, 1+clip] band. Re-clipping after re-normalization would break
    the mean-preservation guarantee, so instead the worst realized deviation from 1.0
    is reported as ``diag["max_effective_dev"]`` and must be monitored by the caller.
    For well-normalized Task-1 inputs (Kreis-weighted index mean == 1) this deviation is
    negligible, but the diagnostic makes any unexpected drift observable."""
    out = frame.copy()
    idx = cell_index.set_index(cell_col)
    renter_idx = out[cell_col].map(idx["renter_income_index"]).fillna(1.0).to_numpy()
    owner_idx = out[cell_col].map(idx["owner_income_index"]).fillna(1.0).to_numpy()
    is_owner = (out[tenure_col].astype(str) == "owner").to_numpy()
    factor = np.where(is_owner, owner_idx, renter_idx)
    clipped = np.clip(factor, 1.0 - clip, 1.0 + clip)
    clipped_fraction = float(np.mean(clipped != factor))
    base = pd.to_numeric(out[income_col], errors="coerce").to_numpy()
    tilted = base * clipped
    # Re-normalize within Kreis to restore the exact pre-tilt weighted mean (weight = 1/hh).
    df = pd.DataFrame({"k": out[kreis_col].to_numpy(), "base": base, "tilted": tilted})
    sums = df.groupby("k").agg(base_sum=("base", "sum"), tilt_sum=("tilted", "sum"))
    scale = df["k"].map((sums["base_sum"] / sums["tilt_sum"]).replace([np.inf, -np.inf], 1.0)).fillna(1.0).to_numpy()
    out[income_col] = tilted * scale
    # Realized effective multiplier per household = clipped factor * per-Kreis renorm scalar.
    # This can exceed the nominal [1-clip, 1+clip] band when the Kreis clipped-index mean
    # departs from 1.0; we report the worst deviation but do NOT re-clip (see docstring).
    eff = clipped * scale
    max_effective_dev = float(np.nanmax(np.abs(eff - 1.0)))
    # Per-Kreis mean-preservation check.
    # The re-normalization restores per-Kreis SUMS exactly, so the residual between
    # mean_tilted_k and mean_base_k is ~machine-epsilon-scaled (~1e-9 or smaller).
    # We use a RELATIVE tolerance (max |dev| / max(|base_mean_k|, 1.0) < 1e-9) so
    # the check scales gracefully with income magnitude and stays honest even when
    # one Kreis drifts up while another drifts down by the same amount (which a
    # global-mean check would falsely pass).
    tilted_final = pd.to_numeric(out[income_col], errors="coerce").to_numpy()
    df_check = pd.DataFrame({"k": out[kreis_col].to_numpy(), "base": base, "tilted": tilted_final})
    per_k = df_check.groupby("k").agg(base_mean=("base", "mean"), tilted_mean=("tilted", "mean"))
    abs_devs = (per_k["tilted_mean"] - per_k["base_mean"]).abs()
    rel_devs = abs_devs / per_k["base_mean"].abs().clip(lower=1.0)
    max_kreis_mean_abs_dev = float(abs_devs.max())
    max_kreis_mean_rel_dev = float(rel_devs.max())
    kreis_mean_preserved = bool(max_kreis_mean_rel_dev < 1e-9)
    diag = {
        "clipped_fraction": clipped_fraction,
        "kreis_mean_preserved": kreis_mean_preserved,
        "max_kreis_mean_abs_dev": max_kreis_mean_abs_dev,
        "beta_clip": clip,
        "max_effective_dev": max_effective_dev,
    }
    logger.info("[income_spatial_tilt] applied: clipped=%.1f%%, kreis_mean_preserved=%s, "
                "max_kreis_mean_abs_dev=%.2e, max_effective_dev=%.4f",
                100 * clipped_fraction, kreis_mean_preserved, max_kreis_mean_abs_dev,
                max_effective_dev)
    return out, diag
