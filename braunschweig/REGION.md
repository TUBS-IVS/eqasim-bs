# Region scope: ZGB-8 (Großraum Braunschweig)

> The Kreis (NUTS-3 / "Landkreis") codes that define the modelled region.
> The machine-readable owner is the `braunschweig.political_prefix` list in
> [`configs/base_bs.yml`](../configs/base_bs.yml) (section "Braunschweig region
> scope (ZGB-8)"); this file only explains it. There is no
> `braunschweig.region` module with a shared constant.

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
against Zensus, BA Pendleratlas, INKAR or BBSR data.  Always store ARS values as strings.

## Where this list is consumed

Keep consumers pointed at the config key; the modules below are examples, not
an exhaustive or checked list.

* IPF scoping in [`braunschweig/data/census/`](data/census/).
* Gravity calibration in [`braunschweig/gravity/model.py`](gravity/model.py).
* Validation harness `scripts/validate_bs_10pct/` and the analysis code in
  [`braunschweig/analysis/`](analysis/); some of it still carries its own
  Kreis-name table (for example `analysis/dashboard/mid_reference.py`).

## How to extend

1. Add the new ARS-5 row to the table above.
2. Update `braunschweig.political_prefix` in `configs/base_bs.yml`.
3. Re-download the Zensus / BA / INKAR slices that cover the new Kreis;
   record them in the Data Registry (`docs/registry/`) and README.
4. Re-run the smoke pipeline; expect baseline counts to change.

Adding or removing a Kreis is a **breaking** scope change; never do it
silently. Record the rationale in an ADR (`docs/decisions/`).
