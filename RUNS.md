# Run ledger — moved

Runs are now recorded as **one structured manifest per run** under
[`docs/runs/`](docs/runs/) (`<run_id>.yml`; see
[`docs/runs/README.md`](docs/runs/README.md) for the rules). The browsable
table is generated to [`docs/generated/RUNS.md`](docs/generated/RUNS.md) by
`python -m braunschweig.documentation build`.

The old hand-edited ledger is archived verbatim at
[`docs/archive/RUNS_ledger_2026-08-13.md`](docs/archive/RUNS_ledger_2026-08-13.md)
(historical, not authoritative). Migration decision: ADR-0077.
