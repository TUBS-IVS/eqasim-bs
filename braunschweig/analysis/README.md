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

# The notebook compares results/10pct with results/25pct, so refresh BOTH rates
# from the same workflow and code state; refreshing only one rate mixes workflows
# and shows workflow differences as rate effects. The committed artefacts come from
# the legacy IPF fixtures (configs/fixtures/config_local_braunschweig_10pct.yml and
# _25pct.yml, the legacy layout of scripts/run_bs_validation.py).

# 1. Generate / refresh the synthesis at both rates. Either the legacy fixtures, or the
#    canonical composed config (base + scale overlay, README "Running the model"); the
#    composed set has no committed 10 % overlay, so that route needs a local overlay
#    setting sampling_rate / output_path for 10 %.
python scripts/run_synpp.py configs/base_bs.yml configs/overlays/test_25pct.yml

# 2. Run the validation harness for each rate (see its --help for --layout / --out)
python -m scripts.run_bs_validation --rate 10
python -m scripts.run_bs_validation --rate 25

# 3. Copy the validation outputs of each run into this folder
#    (<output_path> = the output_path of the config used for that rate in step 1)
Copy-Item <output_path_10pct>\validation\* braunschweig\analysis\results\10pct\ -Force
Copy-Item <output_path_25pct>\validation\* braunschweig\analysis\results\25pct\ -Force

# 4. Re-execute the notebook (writes outputs in-place)
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

## Upstream Bavaria notebooks

The upstream Bavaria fork's notebooks (`Calibration.ipynb`, `MiD Comparison.ipynb`,
`Transit Comparison.ipynb`) are not part of this repository and not part of the BS
validation flow.
