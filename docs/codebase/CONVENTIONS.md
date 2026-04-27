# CONVENTIONS

> Stub for focus-area mode. Fill on demand.

## synpp stage shape (verified)
- Each module exposes `configure(context)` and `execute(context)`.
- `configure` registers `context.stage(name)` and `context.config(key[, default])`.
- BS overrides keep the same return-frame schema as the Bavaria stage they replace.

## Naming
- BS stages live under `braunschweig.*`, mirror the Bavaria namespace.
- Reference-data loaders go under `braunschweig.data.*`.

## [TODO]
- formatting / linting rules
- import ordering
- error handling

## Evidence
- [braunschweig/gravity/model.py](braunschweig/gravity/model.py), [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py) — exemplars.
