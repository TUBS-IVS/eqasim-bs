"""Compare Zensus vs synth HH-size shares per Kreis."""
import json
from pathlib import Path
import pandas as pd

# Zensus per-Kreis
p = r'eqasim-data/data/braunschweig/5000H-2001_de_flat.csv'
df = pd.read_csv(p, sep=';', dtype=str)
df['ars5'] = df['1_variable_attribute_code'].str[:5]
zgb = ['03101','03102','03103','03151','03153','03154','03157','03158']
df = df[df['ars5'].isin(zgb)].copy()
df['count'] = pd.to_numeric(df['value'], errors='coerce').fillna(0.0)
agg = df[df['3_variable_attribute_code'].isna()
         & df['2_variable_attribute_code'].notna()
         & df['2_variable_attribute_label'].ne('Insgesamt')].copy()
sizemap = {'1 Person':'1','2 Personen':'2','3 Personen':'3','4 Personen':'4','5 Personen':'5','6 und mehr Personen':'6+'}
agg['bin'] = agg['2_variable_attribute_label'].map(sizemap)
zen = agg.groupby(['ars5','bin'])['count'].sum().unstack(fill_value=0)
zen_pct = zen.div(zen.sum(axis=1), axis=0) * 100

# Synth per-Kreis from validation report.json
d = json.loads(Path('eqasim-data/output_bs_10pct/validation/report.json').read_text(encoding='utf-8'))
syn_rows = d.get('hh_size_per_kreis', [])

# Reload from raw synth: use the same diagnostics function
from scripts.validate_bs_10pct import diagnostics, io
_, df_full = diagnostics.hh_size_fit_per_kreis()
print('\nSynth per-Kreis HH-size shares:')
syn_pivot = df_full.pivot_table(index='ars5', columns='size_bin', values='synth_share', aggfunc='first') * 100
print(syn_pivot.round(1).to_string())
print('\nZensus per-Kreis HH-size shares:')
zen_view = zen_pct[syn_pivot.columns.tolist()] if all(c in zen_pct.columns for c in syn_pivot.columns) else zen_pct
print(zen_view.round(1).to_string())
print('\n=== DELTA (synth - zensus) per-Kreis pp ===')
delta = (syn_pivot - zen_view).round(1)
print(delta.to_string())
