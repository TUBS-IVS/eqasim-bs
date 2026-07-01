"""Post-synthesis validator: realised fleet marginals vs the EFFECTIVE fed-in targets.

The fleet is drawn from targets that are deliberately transformed (per-Kreis
rake, income-age tilt, euro-age consistency projection). This compares the
realised marginals to those EFFECTIVE targets (NOT the raw KBA tables) so the
residual collapses to Monte-Carlo sampling error on a healthy model and still
catches implementation bugs (e.g. the 4-yr age offset). Logging-only; no
silent drift. See docs/superpowers/specs/2026-07-01-fleet-model-improvements-design.md.
"""
from __future__ import annotations
import logging
import numpy as np

LOGGER = logging.getLogger("braunschweig.synthesis.vehicles.fleet_validation")


def _shares(series):
    vc = series.value_counts(dropna=False)
    n = float(vc.sum())
    return {str(k): float(v) / n for k, v in vc.items()}, int(n)


def validate_realised_margins(df_spec, expected, sample_rate: float = 1.0,
                              tol_sigma: float = 4.0) -> dict:
    """Compare realised marginals in df_spec to the effective target PMFs.

    A dimension is flagged when the max absolute per-label deviation exceeds a
    Monte-Carlo band: tol_sigma * sqrt(p*(1-p)/N_eff) (in pp), N_eff scaled by
    sample_rate so a 1% sample is not falsely flagged. tol_sigma default 4.
    """
    out = {"dimensions": {}, "any_flagged": False}
    for dim, exp in expected.items():
        if dim not in df_spec.columns:
            continue
        realised, n = _shares(df_spec[dim])
        n_eff = max(1.0, n * float(sample_rate))
        labels = set(realised) | set(exp)
        max_pp = 0.0
        band_pp = 0.0
        for lab in labels:
            r = realised.get(lab, 0.0); e = float(exp.get(lab, 0.0))
            max_pp = max(max_pp, abs(r - e) * 100.0)
            band_pp = max(band_pp, tol_sigma * np.sqrt(max(e * (1 - e), 1e-9) / n_eff) * 100.0)
        flagged = bool(max_pp > band_pp)
        out["dimensions"][dim] = {"realised": realised, "expected": exp,
                                  "max_abs_pp": round(max_pp, 3),
                                  "band_pp": round(band_pp, 3), "flagged": flagged}
        out["any_flagged"] = out["any_flagged"] or flagged
        (LOGGER.warning if flagged else LOGGER.info)(
            "[fleet_validation] %s: max dev %.2fpp (band %.2fpp) -> %s",
            dim, max_pp, band_pp, "DRIFT" if flagged else "ok")
    return out
