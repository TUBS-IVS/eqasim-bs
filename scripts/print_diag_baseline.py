"""Print baseline diagnostics from the latest validation report.json."""
import json
from pathlib import Path

p = Path("eqasim-data/output_bs_10pct/validation/report.json")
d = json.loads(p.read_text(encoding="utf-8"))

print("=== OD fit (top-200 Kreis-pairs) ===")
for k, v in d["od_fit"].items():
    print(f"  {k:12s} {v}")

print("\n=== Regression guard ===")
for r in d["regression_guard"]:
    print(f"  {r['status'].upper():4s}  {r['description']:55s}  val={r['value']:.3f}  tol={r['tolerance']:.2f}")

print("\n=== HH-size fit per Kreis ===")
for r in d["hh_size_per_kreis"]:
    print(f"  {r['kreis_name']:20s}  chi2={r['chi2']:>12,.0f}   TVD={r['tvd_pp']:>5.2f} pp  n={r['n_synth_hh']:,}")

print("\n=== Purpose mix RAW (every leg, including return-home) ===")
for r in d.get("purpose_mix_raw", d["purpose_mix"]):
    mid = r.get("mid_share") or 0
    print(f"  {r['purpose']:12s}  synth={r['synth_share']:.3f}  mid={mid:.3f}  delta={r['deviation_pp']:+6.2f}pp")

print("\n=== Purpose mix REMAPPED (R-D: home->preceding_purpose) ===")
for r in d["purpose_mix"]:
    mid = r.get("mid_share") or 0
    print(f"  {r['purpose']:12s}  synth={r['synth_share']:.3f}  mid={mid:.3f}  delta={r['deviation_pp']:+6.2f}pp")
