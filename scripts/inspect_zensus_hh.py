"""Quick inspection of Zensus 5000H-2001 (HH-Größe × Gemeinde × HH-Typ)."""
import pandas as pd
p = r'eqasim-data/data/braunschweig/5000H-2001_de_flat.csv'
df = pd.read_csv(p, sep=';', dtype=str)
print('total rows:', len(df))
print('columns:', df.columns.tolist())
df['ars5'] = df['1_variable_attribute_code'].str[:5]
zgb = ['03101','03102','03103','03151','03153','03154','03157','03158']
df = df[df['ars5'].isin(zgb)].copy()
print('ZGB rows:', len(df))
df['count'] = pd.to_numeric(df['value'], errors='coerce').fillna(0.0)

print('\n=== HH-size shares per Kreis (type aggregated) ===')
agg = df[df['3_variable_attribute_code'].isna()
         & df['2_variable_attribute_code'].notna()
         & df['2_variable_attribute_label'].ne('Insgesamt')].copy()
piv = agg.groupby(['ars5','2_variable_attribute_label'])['count'].sum().unstack(fill_value=0)
piv['total'] = piv.sum(axis=1)
for c in piv.columns:
    if c != 'total':
        piv[c] = (piv[c]/piv['total']*100).round(1)
print(piv.to_string())

print('\n=== HH-types available ===')
print(df['3_variable_attribute_label'].value_counts(dropna=False).head(20))

print('\n=== Cross-tab size x type for SK Braunschweig (03101) ===')
bs = df[df['ars5']=='03101'].copy()
bs = bs[bs['2_variable_attribute_label'].ne('Insgesamt')
        & bs['3_variable_attribute_label'].ne('Insgesamt')
        & bs['2_variable_attribute_code'].notna()
        & bs['3_variable_attribute_code'].notna()]
ct = bs.groupby(['2_variable_attribute_label','3_variable_attribute_label'])['count'].sum().unstack(fill_value=0)
print(ct.to_string())
