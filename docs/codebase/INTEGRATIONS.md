# INTEGRATIONS

> Stub.

## Reference data files (read-only inputs)
- [data/census/braunschweig/5000H-2001_de_flat.csv](data/census/braunschweig/5000H-2001_de_flat.csv) — Zensus 2022 households
- BA Pendleratlas 2025 (path via `braunschweig.data.census.pendler` config) — [TODO] confirm exact path
- MiD 2023 ZGB CSVs in `data/hts/mid/` — loaded by `braunschweig.data.mid.references`

## External downstream
- MATSim Java runtime — consumes `population.xml.gz` produced by the pipeline. **Read-only** in this cycle.

## [TODO]
- INKAR file paths
- VG250 polygon source
