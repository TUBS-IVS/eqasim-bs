# STRUCTURE

> Focus areas only. Other paths marked `[TODO]`.

## Pipeline entry points
- [config_local_braunschweig_10pct.yml](config_local_braunschweig_10pct.yml) — 10 % run config (current baseline).
- [config_local_braunschweig.yml](config_local_braunschweig.yml) — 1 % dev config.
- Run: `python -m synpp config_local_braunschweig_10pct.yml`.

## Calibration-relevant directories

```
bavaria/                      [READ-ONLY, CON-001]
├── ipf/
│   ├── prepare.py            scope/sex/age/license harmonization
│   ├── attributed.py         person-level IPF → ipf.attributed (used by gravity & HH-size)
│   └── model.py              core IPF iteration
├── gravity/
│   ├── distance_matrix.py    Gemeinde×Gemeinde Luftlinien
│   └── model.py              gravity iteration; SLOPE=-0.2, CONSTANT=-2.4, DIAGONAL=1.0 (IDF-derived)
└── synthesis/
    └── population/enriched.py  HH-size / car / bike / pt-sub IPF on synth pop

braunschweig/                 [WRITE-ALLOWED]
├── data/
│   ├── census/
│   │   ├── pendler.py            BA Pendleratlas Kreis-pair flows
│   │   ├── employment.py         SvB am Wohnort (Kreis)
│   │   ├── household_size.py     Zensus 2022 5000H-2001 (HH × Gemeinde × Größe)
│   │   ├── household_income.py   MiD-derived income×size
│   │   └── population.py         Zensus 2022 population
│   ├── external_workplaces.py    BA outbound ≥50 SvB → external Kreis centroids
│   └── mid/references.py         MiD ZGB region CSV loader
├── gravity/
│   └── model.py              wraps bavaria.gravity.model + IPF-calibrates on BA Pendler
├── locations/
│   └── work.py               extends bavaria.locations.work with EXT workplaces
└── synthesis/
    └── spatial/
        └── commute_distance.py   MiD P13 commute-distance override

scripts/
└── validate_bs_10pct/        [WRITE-ALLOWED]
    ├── __main__.py           CLI orchestrator
    ├── config.py             ZGB8 dict, MID_BASELINE, thresholds
    ├── io.py                 cache loaders + MATSim XML mode extractor
    ├── references.py         Zensus / BA / MiD reference loaders
    ├── metrics.py            KPI computations
    ├── style.py              palettes
    ├── plots.py              17 plot functions
    └── report.py             HTML + JSON output
```

## Cache layout (already populated)
- `eqasim-data/cache_bs_10pct/` — 10 % cache (113,973 persons, 353,694 trips)
- `eqasim-data/output_bs_10pct/` — homes.gpkg, commutes.gpkg, persons.csv, households.csv, MATSim XML

## Evidence
- File listings via `list_dir`; configs at workspace root.
