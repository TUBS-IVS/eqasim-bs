# Region scope: ZGB-8 (Großraum Braunschweig)

> Single source of truth for the Kreis (NUTS-3 / "Landkreis") codes that
> define the modelled region.  Every Python module that needs the list MUST
> import :data:`braunschweig.region.ZGB_KREIS_IDS` rather than redefining
> the literal — diverging definitions have caused silent scope mismatches
> in past refactors.

## Member Kreise (ARS-5)

| ARS-5 | Name                          | Type       |
|-------|-------------------------------|------------|
| 03101 | Stadt Braunschweig            | Kreisfreie Stadt |
| 03102 | Stadt Salzgitter              | Kreisfreie Stadt |
| 03103 | Stadt Wolfsburg               | Kreisfreie Stadt |
| 03151 | Landkreis Gifhorn             | Landkreis  |
| 03153 | Landkreis Goslar              | Landkreis  |
| 03154 | Landkreis Helmstedt           | Landkreis  |
| 03157 | Landkreis Peine               | Landkreis  |
| 03158 | Landkreis Wolfenbüttel        | Landkreis  |

The Verband ZGB itself comprises seven Kreise (the eighth, Goslar, joined
later); the modelled region is the **statistical** ZGB-8 used by Zensus and
BA Pendleratlas.  See
[`docs/codebase/INTEGRATIONS.md`](../docs/codebase/INTEGRATIONS.md) for the
data sources scoped to this list.

## Why the leading zero matters

ARS codes for Niedersachsen begin with `03`.  Casting to integer drops the
leading zero (`3101` instead of `03101`) and silently breaks every join
against Zensus, BA Pendleratlas, INKAR or BBSR data.  See
[`docs/codebase/CONCERNS.md`](../docs/codebase/CONCERNS.md) BUG-003 for the
recurring failure mode.  Always store ARS values as strings.

## Where this list is consumed

* IPF scoping in [`braunschweig/data/census/`](data/census/) — every loader
  filters its DataFrame to `kreis_id ∈ ZGB_KREIS_IDS` before aggregation.
* Gravity calibration in [`braunschweig/gravity/model.py`](gravity/model.py)
  — the OD matrix is restricted to intra-ZGB pairs plus the explicit
  external-commuter injection.
* Validation harness in [`scripts/validate_bs_10pct/`](../scripts/validate_bs_10pct/)
  — the report KPIs are computed per Kreis and aggregated with this list.

## How to extend

1. Add the new ARS-5 row to the table above.
2. Update `ZGB_KREIS_IDS` in [`braunschweig/region.py`](region.py) (created
   in Phase 2.6 once the IPF code lands).
3. Re-download the Zensus / BA / INKAR slices that cover the new Kreis;
   record them under the data download checklist in `README.md`.
4. Re-run the 1 % smoke pipeline; expect baseline counts to change — update
   [`plan/baselines/`](../plan/baselines/) accordingly **after** user
   confirmation (see Hard Rule 6 in [`AGENTS.md`](../AGENTS.md)).

Adding or removing a Kreis is a **breaking** scope change; never do it
silently.  Record the rationale in `plan/`.
