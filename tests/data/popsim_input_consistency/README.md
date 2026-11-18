# Golden control tables of the PopulationSim control build

`control_totals_{ZENSUS100m,ZENSUS1km,STAAT,WELT}.csv` are the four control-total tables
that the control-total path of the `data.census.filtered` stage produces for the SYNTHETIC
cell frame of `tests/popsim_control_build_fixture.py` (mid seed, catalog controls, tiers 0 and
2). The values are test data, not Zensus figures. They were frozen BEFORE any change of issue
#657, so that a later config key at its legacy value can be proven to reproduce today's tables
value for value (`tests/test_popsim_control_build_golden.py`).

Regenerate (only for a deliberate, documented change of the expected tables), from the
repository root, in the `eqasim` environment:

    python -m tests.popsim_control_build_fixture

Base commit of the frozen tables: `2935a9fb` (branch `fix/i657-popsim-tenure-dwellings`).
