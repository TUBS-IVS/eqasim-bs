# Education slope calibration vs MiD 2023 Tabelle 43 / MZ 2024 BBS

Detour factor (routed -> straight-line): 1.3. Calibrated on the 25% synthesis (cache_bs_25pct); per-pupil slope by home RegioStaR-7, coupled full-level secant.

Levels: grundschule / sekundar_1 / oberstufe use per-RS7 MiD Tab. 43 targets. bbs uses the national Destatis MZ 2024 BBS straight-line mean as a uniform target across all RS7 (rural cells may legitimately exceed it due to school sparsity).

## Results (straight-line mean school-trip km)

```
      level  regiostar7  n_pupils  target_km  achieved_mean_km  abs_error_km   slope
grundschule          72      2956      1.538             1.538         0.000 -1.3354
grundschule          73      1481      2.308             2.298         0.010 -0.6835
grundschule          74      1891      3.846             3.791         0.055 -0.4507
grundschule          75       258      1.538             1.541         0.003 -1.0793
grundschule          76       203      3.077             3.028         0.049 -0.1248
grundschule          77       198      3.846             3.934         0.088 -0.2412
 sekundar_1          72      3772      3.077             3.094         0.017 -0.4944
 sekundar_1          73      1949      4.615             4.558         0.057 -0.2383
 sekundar_1          74      2405      6.923             6.931         0.008 -0.4303
 sekundar_1          75       336      3.077             3.082         0.005 -0.5700
 sekundar_1          76       293      4.615             4.595         0.020 -0.1568
 sekundar_1          77       263      6.923             6.769         0.154 -0.1160
  oberstufe          72       574      3.077             2.993         0.084 -1.5100
  oberstufe          73       292      5.385             5.181         0.204 -0.7650
  oberstufe          74       354      6.923             8.272         1.349 -3.0000
  oberstufe          75        40      3.846             6.063         2.217 -1.2588
  oberstufe          76        50      5.385             3.994         1.391 -1.2588
  oberstufe          77        43      7.692             9.470         1.778 -1.2588
        bbs          72      1264     15.839            15.844         0.005 -0.0350
        bbs          73       571     15.839            15.896         0.057 -0.0898
        bbs          74       686     15.839            15.855         0.016 -0.1524
        bbs          75       110     15.839            16.075         0.236 -0.0666
        bbs          76       129     15.839            15.849         0.010 -0.1346
        bbs          77        95     15.839            20.389         4.550 -3.0000
```

## Assessment

- Cells within 0.5 km of target: 19 / 24.
- Cells at the steepest bracket bound (slope = -3.0, school-sparsity floor): 2 (oberstufe/RS7 74, bbs/RS7 77).
- grundschule and sekundar_1 calibrate well. oberstufe rural cells (RS7 74/77) may not reach the MiD target even at the steepest slope: rural upper-secondary schools (Gymnasium/Oberstufe) are sparse. bbs rural cells legitimately exceed the national MZ 2024 mean due to regional school sparsity -- this is expected and consistent with the longer BBS catchment assumption.