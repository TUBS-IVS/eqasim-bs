# Education slope calibration vs MiD 2023 Tabelle 43

Detour factor (routed -> straight-line): 1.3. Calibrated on the 25% synthesis (cache_bs_25pct); per-pupil slope by home RegioStaR-7, coupled full-level secant.

## Results (straight-line mean school-trip km)

```
      level  regiostar7  n_pupils  target_km  achieved_mean_km  abs_error_km   slope
grundschule          72      2956      1.538             1.528         0.010 -1.3446
grundschule          73      1481      2.308             2.300         0.008 -0.6841
grundschule          74      1891      3.846             3.834         0.012 -0.4415
grundschule          75       258      1.538             1.558         0.020 -1.0202
grundschule          76       203      3.077             3.108         0.031 -0.1253
grundschule          77       198      3.846             3.811         0.035 -0.2583
 sekundar_1          72      3772      3.077             3.108         0.031 -0.4969
 sekundar_1          73      1949      4.615             4.564         0.051 -0.2368
 sekundar_1          74      2405      6.923             6.761         0.162 -0.4523
 sekundar_1          75       336      3.077             3.053         0.024 -0.5546
 sekundar_1          76       293      4.615             3.569         1.046 -0.2213
 sekundar_1          77       263      6.923             6.700         0.223 -0.0996
 sekundar_2          72      1838      3.077             3.091         0.014 -2.0755
 sekundar_2          73       863      5.385             5.436         0.051 -1.9817
 sekundar_2          74      1040      6.923            10.025         3.102 -3.0000
 sekundar_2          75       150      3.846             2.624         1.222 -2.1791
 sekundar_2          76       179      5.385             5.773         0.388 -3.0000
 sekundar_2          77       138      7.692            13.395         5.703 -3.0000
```

## Assessment

- Cells within 0.5 km of target: 14 / 18.
- Cells at the steepest bracket bound (slope = -3.0, school-sparsity floor): 3 (sekundar_2/RS7 74, sekundar_2/RS7 76, sekundar_2/RS7 77).
- grundschule and sekundar_1 calibrate well. sekundar_2 rural cells (RS7 74/77) cannot reach the MiD target even at the steepest slope: rural upper-secondary schools (Oberstufe/BBS) are sparse, so the nearest school is already far. The MiD 14-17 age band also mixes in Sek-I pupils (ages 14-15, nearer schools) that our 16-19 sekundar_2 band excludes, biasing the target short.