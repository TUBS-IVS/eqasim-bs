"""Print deviation summary across all metrics from report.json."""
import json
from pathlib import Path
d = json.loads(Path('eqasim-data/output_bs_10pct/validation/report.json').read_text(encoding='utf-8'))

print('=== Mode mix vs MiD (Wegezweck-Anteil) ===')
for r in d.get('mode_mix', []):
    mid = r.get('mid_share') or 0
    print(f"  {r['mode']:8s} synth={r['synth_share']:.3f} mid={mid:.3f} delta={r['deviation_pp']:+6.2f}pp")

print()
print('=== Trips per person ===')
tpp = d.get('trips_per_person', {})
for k, v in tpp.items():
    print(f"  {k}: {v}")

print()
print('=== Trip distance summary ===')
tds = d.get('trip_distance_summary', {})
for k, v in tds.items():
    print(f"  {k}: {v}")

print()
print('=== ZGB-8 Population vs Zensus ===')
for r in d.get('zgb_population', []):
    print(f"  {r['kreis_name']:22s} synth={r['synth']:>9,} zensus={r['zensus']:>9,} dev={r['deviation_pct']:+5.2f}%")

print()
print('=== Available report.json sections ===')
for k in d.keys():
    print(f"  - {k}")
