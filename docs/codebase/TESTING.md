# TESTING

> Stub.

## Existing tests
- [tests/test_braunschweig_data.py](tests/test_braunschweig_data.py)
- [tests/test_determinism.py](tests/test_determinism.py)
- [tests/test_pipeline.py](tests/test_pipeline.py)
- [tests/test_simulation.py](tests/test_simulation.py)

## Validation harness (acts as a regression test)
- `python -m scripts.validate_bs_10pct` — produces 17 plots + HTML + JSON. Will be extended (TASK-301..305) with: OD scatter, OD flowmap, per-Kreis HH χ², KPI regression guard.

## [TODO]
- coverage targets
- CI integration
