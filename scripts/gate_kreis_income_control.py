"""Measure-gain gate for the Kreis-Income-Control (off vs on, multi-Kreis).

Decides KEEP_DEFAULT_ON vs FLIP_DEFAULT_OFF from two persons-frame summaries
(Salzgitter 03102 + Wolfsburg 03103). decide_gate is the pure, tested core; the
__main__ harness loads two cached popsim runs and prints the recommendation.

A Kreis-Income-Control is a BETWEEN-Kreis feature: run the gate on >=2 Kreise
(single Kreis is a no-op). Fail-open: absent diagnostics never force a flip.
"""
from __future__ import annotations

# Realism (KS to MiD) must not worsen by more than this; coherence must not worsen.
KS_TOLERANCE = 0.02
SALZGITTER = "03102"
WOLFSBURG = "03103"


def decide_gate(off_summary: dict, on_summary: dict) -> tuple[str, int]:
    """Return (recommendation, exit_code). KEEP unless a measured KPI clearly worsens."""
    flip_reasons = []

    ks_off = off_summary.get("ks_to_mid")
    ks_on = on_summary.get("ks_to_mid")
    if ks_off is not None and ks_on is not None:
        if ks_on > ks_off + KS_TOLERANCE:
            flip_reasons.append(f"income realism worsened (KS {ks_off:.3f} -> {ks_on:.3f})")

    coh_off = off_summary.get("incoherent_fraction")
    coh_on = on_summary.get("incoherent_fraction")
    if coh_off is not None and coh_on is not None:
        if coh_on > coh_off + 1e-9:
            flip_reasons.append(f"label-value coherence worsened ({coh_off:.3f} -> {coh_on:.3f})")

    means_on = on_summary.get("kreis_mean") or {}
    sz, wob = means_on.get(SALZGITTER), means_on.get(WOLFSBURG)
    if sz is not None and wob is not None:
        if not (sz < wob):
            flip_reasons.append(f"between-Kreis order wrong (SZ {sz:.0f} !< WOB {wob:.0f})")

    if flip_reasons:
        print("FLIP_DEFAULT_OFF:", "; ".join(flip_reasons))
        return "FLIP_DEFAULT_OFF", 1
    print("KEEP_DEFAULT_ON: Kreis-Income-Control improves realism/coherence; order correct.")
    return "KEEP_DEFAULT_ON", 0


def summarize(persons, mid_reference_eur) -> dict:
    """Build a gate summary from a popsim persons frame + a MiD reference EUR sample.

    ks_to_mid: KS distance of de-duplicated household_income_eur to mid_reference_eur.
    incoherent_fraction: share of households whose household_income label band does
    not contain household_income_eur. kreis_mean: per-Kreis household-mean EUR."""
    import numpy as np
    from braunschweig.popsim.income_kreis_control import build_class_midpoint_eur, income_class_from_eur

    hh = persons.sort_values("household_id").groupby("household_id").first().reset_index()
    eur = hh["household_income_eur"].to_numpy(dtype=float)

    # KS distance (two-sample, no scipy dependency).
    a = np.sort(eur[~np.isnan(eur)])
    b = np.sort(np.asarray(mid_reference_eur, dtype=float))
    grid = np.concatenate([a, b])
    cdf_a = np.searchsorted(a, grid, side="right") / max(len(a), 1)
    cdf_b = np.searchsorted(b, grid, side="right") / max(len(b), 1)
    ks = float(np.max(np.abs(cdf_a - cdf_b))) if len(a) and len(b) else None

    table = build_class_midpoint_eur()
    expected_label = income_class_from_eur(eur, table)
    incoherent = float(np.mean(expected_label != hh["household_income"].to_numpy())) if len(hh) else None

    kreis_mean = {k: float(g["household_income_eur"].mean())
                  for k, g in hh.groupby(hh["departement_id"].astype(str))}
    return {"ks_to_mid": ks, "incoherent_fraction": incoherent, "kreis_mean": kreis_mean}


if __name__ == "__main__":  # pragma: no cover
    import argparse
    import pandas as pd

    ap = argparse.ArgumentParser()
    ap.add_argument("--off", required=True, help="OFF-run persons parquet")
    ap.add_argument("--on", required=True, help="ON-run persons parquet")
    ap.add_argument("--mid-ref", required=True, help="MiD reference household_income_eur parquet")
    args = ap.parse_args()

    ref = pd.read_parquet(args.mid_ref)["household_income_eur"].to_numpy()
    off = summarize(pd.read_parquet(args.off), ref)
    on = summarize(pd.read_parquet(args.on), ref)
    print("OFF:", off)
    print("ON :", on)
    _, code = decide_gate(off, on)
    raise SystemExit(code)
