# `braunschweig/analysis/`

All population-quality analysis for the Braunschweig synthetic population
lives here.  Inspired by
[`activitysim/populationsim`](https://github.com/ActivitySim/populationsim)
result notebooks (target-vs-synth marginals, control fit, SRMSE), but
adapted to the eqasim pipeline outputs.

## Layout

```
braunschweig/analysis/
├── README.md                       <- this file
├── population_fit.ipynb            <- populationsim-style fit notebook
├── validation_mid2023.ipynb        <- legacy MiD 2023 deep-dive notebook
└── results/
    ├── 10pct/                      <- artefacts from 10 % run
    │   ├── report.html             <- print-ready HTML report
    │   ├── report.json             <- machine-readable KPI dump
    │   └── 01..21_*.png            <- 21 publication-quality plots
    └── 25pct/                      <- same artefacts from 25 % run
```

## Re-running the analysis

```powershell
& "$env:LOCALAPPDATA\miniforge3\shell\condabin\conda-hook.ps1"
conda activate eqasim

# 1. Generate / refresh the 10 % synthesis (already cached if present)
python -m synpp config_local_braunschweig_10pct.yml

# 2. Generate / refresh the 25 % synthesis
python -m synpp config_local_braunschweig_25pct.yml

# 3. Run the validation harness against each rate
python -m scripts.validate_bs_10pct                       # 10 % (legacy entry)
python -m scripts.run_bs_validation --rate 10
python -m scripts.run_bs_validation --rate 25

# 4. Copy the validation outputs into this folder
Copy-Item eqasim-data\output_bs_10pct\validation\* braunschweig\analysis\results\10pct\ -Force
Copy-Item eqasim-data\output_bs_25pct\validation\* braunschweig\analysis\results\25pct\ -Force

# 5. Re-execute the notebook (writes outputs in-place)
jupyter nbconvert --to notebook --execute braunschweig\analysis\population_fit.ipynb --inplace
```

`scripts/run_bs_validation.py` is a thin driver around the
`scripts.validate_bs_10pct` package that monkey-patches the rate before
the submodules are imported, so the same harness works for the 1 %, 10 %,
and 25 % runs.

## What the notebook covers

1. **Population control** — Zensus 2022 per Kreis (target vs expanded synth).
2. **Household-size control** — TVD + χ² per Kreis vs Zensus 5000H-2001.
3. **OD fit** — synth vs BA Pendleratlas top-200 Kreis-pairs (R², RMSE).
4. **Mode share** — vs MiD 2023 regional sample.
5. **Activity-purpose mix** — synth (remapped) vs MiD 2023.
6. **Trip distance / duration / departure profile** — vs MiD P13.
7. **SRMSE / MAE summary** across every control.
8. **Regression guard** — pass / fail per the harness thresholds.
9. **Headline KPIs** as plain-text summary.

## Reference notebook in `bavaria/analysis/`

The upstream Bavaria fork ships three notebooks
(`Calibration.ipynb`, `MiD Comparison.ipynb`, `Transit Comparison.ipynb`)
that operate on Bavaria-specific data. They are kept under
[`bavaria/analysis/`](../../bavaria/analysis/) for traceability but are
**not** part of the BS validation flow.
