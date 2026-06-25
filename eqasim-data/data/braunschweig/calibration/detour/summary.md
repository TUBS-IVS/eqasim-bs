# Detour circuity fit summary

Random seed: 1234

## Fitted curve parameters

Model: c(d) = c_inf + a * exp(-d / tau_km)

| network | c_inf | a | tau_km | R2 | n_samples |
| --- | --- | --- | --- | --- | --- |
| car | 1.1879 | 0.5018 | 4.9547 | 0.2336 | 2345 |
| walk | 1.1988 | 0.4700 | 1.2249 | 0.1951 | 2202 |

## Per-RS7 diagnostic (car)

RS7 cells: 1 promote, 0 keep_global, 5 under_sampled.

```
 rs7  n_samples    c_inf        a  tau_km       r2  rs7_delta_emd       verdict
  72        873 1.188849 0.680495 3.33022 0.281583       0.003436       promote
  73        383      NaN      NaN     NaN      NaN            NaN under_sampled
  74        795      NaN      NaN     NaN      NaN            NaN under_sampled
  75         93      NaN      NaN     NaN      NaN            NaN under_sampled
  76         85      NaN      NaN     NaN      NaN            NaN under_sampled
  77        116      NaN      NaN     NaN      NaN            NaN under_sampled
```

## Band-shift impact vs constant 1.3

Maximum absolute EMD delta (fitted vs constant 1.3): 0.0029.
**Assessment: the distance-dependent circuity curve is NOT MATERIAL vs the legacy constant 1.3** (threshold: EMD delta > 0.01).

```
            trip_type         metric  emd_constant_1_3  emd_fitted_curve     delta                                                                                                                                               note
              commute emd_vs_p13_zgb          0.087773          0.084859 -0.002914                                                                               negative delta = fitted curve reduces EMD (closer to MiD P13 target)
secondary_walk_pooled     emd_vs_w12          0.071188          0.072947  0.001760 pooled secondary walk (no purpose split here; per-purpose EMD: scripts/validate_secondary_distances.py). negative delta = fitted curve reduces EMD
```

## Per-RS7 verdict summary

RS7-specific curves are RECOMMENDED (at least one RS7 cell promoted).

## Notes

- car graph: OSM PBF driving network via pyrosm (bbox-clipped to ZGB homes + margin), EPSG:25832
- walk graph: OSM PBF walking network via pyrosm (bbox-clipped to ZGB homes + margin), EPSG:25832
- OD pool: home->work (employed persons) + secondary (car/walk) + education legs
- pt row: NOT fitted here; carried over from existing seed file (UNVERIFIED placeholder)
- walk fit: real curve fitted from OSM walk network.
- REGENERATE: re-run scripts/calibrate_detour_circuity.py on the server after each synthesis update