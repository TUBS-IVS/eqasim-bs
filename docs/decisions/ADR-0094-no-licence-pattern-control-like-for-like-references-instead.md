# ADR-0094 · 2026-08-20 · No driving-licence pattern control; commit the like-for-like sex x cohort references instead (issue #322)

- **Status:** accepted
- **Date:** 2026-08-20
- **Issues:** #322 (driving licence: pattern-only age x sex control), #307, #320
- **Supersedes / revises:** nothing. Confirms ADR-0079 item 6 (licence level not identifiable)
  and localises it to one cell.
- **Related:** ADR-0079 (licence level deliberately uncontrolled), ADR-0088 (fine teen age
  bands), ADR-0089 (PT three-group blend), ADR-0092 (RS7-conditioned national MiD as a
  regional prior), #329 (uncontrolled composition inside a controlled group)

## Context

Issue #322 asked for a "pattern-only" driving-licence control: reproduce the observed
male/female (and possibly age) gradient without moving the aggregate licence level, which
ADR-0079 deliberately left uncontrolled because the two surveys disagree on it by about 7pp
and KBA FE4 cannot arbitrate (post-1999 licences only).

Its headline evidence was that the synthetic population realises a male-female licence
difference of about 1pp where MiD reads 8pp. That figure came from comparing the population's
overall gap against `mid2023_P17_1_by_sex.csv`, a MARGINAL: the committed MiD reference tables
resolve sex and age separately, never jointly. The same applies to the age evidence, which
compared realised age bands against `mid2023_P17_1_by_age.csv` while the two sources' overall
levels differ by 7pp.

Before building anything, the premise was measured against sources that resolve both
dimensions. The SrV scientific-use microdata carries `V_GESCHLECHT`, `V_ALTER` and
`V_FUEHR_PKW`, and the MiD person microdata carries `HP_SEX`, `HP_ALTER` and `P_FSCHEIN`, so
both crosses are computable even though neither was committed.

## Measurement (2026-08-20, 100 % population `output_bs_100pct_allfeat_popsim_i240`)

Licence share per sex x cohort, 18+ base:

| cell | model | SrV (region) | MiD Niedersachsen | MiD national |
|---|---|---|---|---|
| 18-64 male | 91.67 % | 93.80 % | 92.21 % | 90.99 % |
| 18-64 female | 91.92 % | 92.99 % | 90.07 % | 89.38 % |
| 65+ male | 93.39 % | 96.40 % | 96.19 % | 96.14 % |
| 65+ female | 87.98 % | 89.72 % | 81.84 % | 82.96 % |

Resulting male-female gaps: 18-64 model −0.25pp against SrV +0.81pp and MiD +1.6 to +2.1pp;
65+ model +5.41pp against SrV +6.69pp and MiD +13.2 to +14.4pp.

Three findings follow.

1. **The 8pp premise is a composition artifact.** MiD's own microdata gives a +3.73pp gap on
   its 18+ marginal, while the committed ZGB 14+ marginal reads +8pp. The gap is concentrated
   in the 65+ cohort and nearly absent below it, so a marginal comparison largely measures the
   age composition of the survey sample rather than a structural property of the population.
2. **The model already reproduces the cohort shape**, qualitatively and (against the regional
   survey) quantitatively: small below 65, large at 65+. Against SrV the age profile deviates
   by about 1.5pp on average, and the "+11.8pp at 80+" reported in #322 against MiD is
   **+0.98pp against SrV** - that claim, too, was a level artifact.
3. **The residual splits into one agreed cell and one unidentifiable cell.** At 65+ male, all
   three references agree within 0.3pp (96.1-96.4 %) and the model is about 2.8pp low - a real
   but small, single-cell deviation. At 65+ female the two surveys disagree by about 8pp
   (SrV 89.7 % vs MiD-NDS 81.8 %), and the model sits near the SrV end.

MiD cannot be dismissed as "national only": the 65+ gap is +14.36pp in Niedersachsen and
between +8.98pp and +15.22pp in every single RegioStaR7 class. Nor can a MiD value for the
study region be obtained - the MiD microdata carries no Kreis or AGS key (only `BLAND` and the
`RegioStaR*` classes), and the committed ZGB tables come from the infas report, which publishes
marginals only.

## Decision

1. **Do not add a licence pattern control.** No `KreisAttributeControl` entry, no sex x cohort
   control columns, no change to any production code path. The licence attribute keeps being
   steered exactly as it is today (`attributes.map_has_license` plus the donor weights).
2. **Commit the two missing like-for-like references** so that no future comparison repeats the
   marginal mistake: `srv2023_car_license_by_sex_cohort_18plus_by_kreis.csv` (regional, real
   Kreis resolution, no Wolfsburg) and `mid2023_license_by_sex_cohort.csv` (larger n, national /
   Niedersachsen / per-RS7 readings, no Kreis resolution possible). Both are validation
   references, explicitly NOT control targets.
3. **Narrow #322** to the one cell where the sources agree (65+ male, about 2.8pp low). If it
   is ever built, it is a one-cell nudge, not a grid.

## Rationale

A control pins the SUM of its categories, so a sex x cohort licence control would pin the
licence LEVEL per cell - the quantity ADR-0079 refused to fix. That price would buy a
correction of about 1pp below 65 and about 1.3pp at 65+ measured against the regional survey,
i.e. deviations inside survey noise, while its largest target cell (65+ female) would have to
pick a winner between two surveys that differ by 8pp.

Blending the two sources by effective sample size was considered explicitly, and the machinery
exists (`braunschweig/popsim/blended_targets.py`, precision blend with shrink-on-disagreement,
used for the PT groups in ADR-0089). It was rejected here because blending is the right tool
for **sampling** disagreement and the wrong tool for **measurement** disagreement: SrV's
licence share exceeds MiD's in every cell, most strongly where the true rate is furthest from
the ceiling, which is equally consistent with a genuine regional effect and with SrV's known
upward level bias. A precision blend would emit a confident-looking number that hides an 8pp
identification problem - exactly the "no invented reference values" failure mode the project
rules forbid. Nor is SrV simply the bigger survey here: for the decisive 65+ cells it carries
about 4,479 respondents against 4,912 in MiD Niedersachsen and 55,297 nationally.

There is also a displacement cost. Eight further KREIS control columns compete with the ten
existing controls in the same balancer; the 2026-08-20 run showed `household_size` degrading
from 1.56pp to 1.82pp mean deviation after nine ownership-grid controls were added. Paying that
for a ~1pp structure gain is a bad trade.

## Consequences

- The licence structure stays as measured: within about 1-3pp of the regional survey in every
  sex x cohort cell, with a documented 2.8pp shortfall for 65+ men and a documented
  8pp source disagreement for 65+ women.
- Every future licence comparison has a committed like-for-like reference; comparing against
  the one-dimensional marginals is now a documented error, not an easy mistake.
- ADR-0079's refusal to fix the licence level stands and is now localised: the level is
  unidentifiable specifically in the 65+ female cell, not diffusely.
- The simulation stake is small and unchanged: per ADR-0079 item 4 the licence only opens `car`
  on top of `car_passenger` in `BraunschweigModeAvailability`.
- Zero risk to existing results: this ADR adds two reference tables, one extraction script, one
  extension to an existing extraction script and tests. No production code path changes, so no
  output can move.

## Rejected alternatives

- **Sex-only marginal control (2 categories per Kreis).** Cheapest, and it would close the
  marginal gap - but the measurement shows the gap is a 65+ phenomenon, so a marginal control
  would be free to place the difference in cohorts where neither survey sees one. That is the
  #329 defect class (a controlled group total with an uncontrolled composition inside) applied
  deliberately, which is not defensible.
- **Full sex x fine-age-band cross (9 bands x 2).** Maximum structure, but per-Kreis cells
  become thin on both survey sides and the exercise would calibrate survey noise - precisely
  what ADR-0079 warned against.
- **Sex x coarse-cohort control with a precision blend (the design originally approved).**
  Rejected on the measurement above: see the Rationale. This is the option that the evidence
  removed, and it is recorded here rather than in a chat log so the reasoning survives.
- **Post-hoc swap of licences between men and women, preserving the total.** Would deliver
  "pattern only" exactly, and is ruled out by ADR-0090: the licence is jointly observed in the
  donor household, so it is steered through the weights, never by rewriting the attribute.
- **Adopting MiD's per-Kreis licence levels.** Rejected earlier in #322 and unchanged: MiD's
  per-Kreis shares span 74.7-94.0 % at n about 800-1750, and Wolfsburg alone would move
  −7.4pp on the thinnest cell in the table.
