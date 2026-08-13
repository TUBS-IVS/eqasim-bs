# Project status — moved (retired document)

The hand-maintained status dashboard is retired (2026-08-13, ADR-0077).

- **What is active in the current production model:**
  [`docs/generated/STATUS.md`](docs/generated/STATUS.md) — generated from the
  Feature/Stage/Data registries and the **resolved** canonical production
  config (`configs/base_bs.yml` + `configs/overlays/test_100pct.yml`); rebuild
  with `python -m braunschweig.documentation build`.
- **Feature evidence:** [`docs/generated/FEATURES.md`](docs/generated/FEATURES.md)
- **Branches/PRs:** GitHub (`gh pr list`, `git branch --no-merged main`).
- **Open work:** [GitHub issues](https://github.com/TUBS-IVS/eqasim-bs/issues).
- Ownership model: [`docs/DOCUMENTATION_GOVERNANCE.md`](docs/DOCUMENTATION_GOVERNANCE.md).

The last hand-maintained version is archived (historical, partly stale) at
[`docs/archive/PROJECT_STATUS_2026-08-13.md`](docs/archive/PROJECT_STATUS_2026-08-13.md).
