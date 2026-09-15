# ADR-0121 · 2026-09-15 · Separate MiD passenger access from driver access

- **Status:** active

Accepted for implementation in [issue #398](https://github.com/TUBS-IVS/eqasim-bs/issues/398).
The feature requires coordinated Python and Java changes. ADR-0119 and ADR-0120
are already allocated on the passive-escort and in-commuter branches respectively.

## Context

The Bavaria-derived `BraunschweigModeAvailability.getAvailableModes` admits
`car_passenger` only inside the household-car-availability branch. Python's MiD
producer derives that availability from `H_ANZAUTO` relative to the number of
adults. It describes household cars, not every opportunity to ride in somebody
else's car. A technical `default_car` is a routing resource and does not resolve
this distinction.

The MiD 2023 B1 v1.1 codebook (sheet `Personen`) defines `P_VAUTO` as personal
availability as a driver **or passenger**, including carsharing, independently
of the diary day. Codes 1/2/3 mean anytime/occasionally/never. Code 9 is item
nonresponse, 206 a proxy interview, and 402 a child under 14 who was not asked.
The [MiD data-use handbook, page 33](https://www.mobilitaet-in-deutschland.de/pdf/infas_HandbuchZurDatennutzung_MiD2023_7555.pdf#page=33)
documents the construct and missing categories. The source is already delivered
in the existing restricted MiD person CSV; no additional dataset is needed.

France uses observed passenger status in
[`IDFModeAvailability`](https://github.com/eqasim-org/eqasim-java/blob/fc8569353b4aa5124e3254ed97aec2a853fed026/ile_de_france/src/main/java/org/eqasim/ile_de_france/mode_choice/IDFModeAvailability.java)
together with a
[`PassengerConstraint`](https://github.com/eqasim-org/eqasim-java/blob/fc8569353b4aa5124e3254ed97aec2a853fed026/core/src/main/java/org/eqasim/core/simulation/mode_choice/constraints/PassengerConstraint.java)
fixing the initial passenger mode. The pinned comparison uses France's configured
Java revision, not an assumed match to Java's moving main branch. Braunschweig's
Bavaria-derived `RunAdaptConfig` deliberately removes that constraint and supplies
its own passenger utility. Neither method alone guarantees a matching driver,
compatible schedule or free seat. The maintainer chose to retain the endogenous
Bavaria mode-choice approach and improve its availability input.

## Decision

Use a distinct `car_passenger_availability` person attribute, exported to MATSim
as the String `carPassengerAvailability`. Keep `car_availability`, licence
requirements, utility coefficients and technical/physical vehicle assignment
unchanged. The new attribute has `none`, `some` and `all` values; the existing
mode-choice interface admits both `some` and `all` and excludes `none`.

For persons aged 14 or older, preserve their valid `P_VAUTO` category. Estimate
missing responses from valid respondents using reproducible empirical draws and
record which values were estimated. Matching uses available age-group,
household-car-presence and donor-region information; group fallback remains
observable. Repeated copies of the same own MiD person receive the same resolved
answer. The loader protects the original attribute respondent's identity before
household-member completion. Completion copies that identity, while subsequent
diary matching changes only the plan-source identity. Empirical pools count an
original attribute respondent once and use that respondent's household covariates,
including when the person appears only as a copied household member. Adult copies
are labelled `member_completion_borrowed_response` or
`member_completion_borrowed_imputation`, never `own_response`. Raw attribute-source
identities remain internal and are excluded from public CSV/XML outputs.
Canonical ordering by original respondent identity prevents earlier copied rows
from changing the empirical draw order or the original respondents' estimates.
Never translate a missing response into
`none` merely because no answer exists. Malformed enabled input and a missing
required source column fail clearly.

For children under 14, use the following explicit **ASSUMPTION**, not an observed
MiD answer: a household car with a licensed adult, positive adult access in the
same household, or a reported child passenger trip provides evidence of potential
passenger access. Such derived access is `some`, not `all`. Household membership
does not assert a parent-child relationship. Missing household evidence requires
an observable empirical fallback; it is distinct from resolved negative evidence.
The field `passenger_availability_source` records the route used. These rules
describe eligibility, not a promise that an adult can chauffeur every child trip.
For the empirical child fallback, the sampling pool consists only of distinct
valid original age-14+ responses, mapped to `some` or `none`. Imputed adult
responses are excluded from this pool so estimated values do not become extra
observations.

The feature flag `mid_passenger_availability` defaults ON and applies only to
`popsim_mid`. Its OFF path preserves legacy output and random streams. Open
workflows retain their existing behavior. Injected agents without this MiD
attribute retain the legacy passenger-availability rule, with compatibility use
reported rather than concealed.

The Java consumer applies this attribute only to `car_passenger`. A positive
`P_VAUTO` can reflect riding or carsharing, so it must never independently enable
driving. No `PassengerConstraint` is added. An absent new attribute invokes the
legacy rule; an invalid supplied attribute raises an error.

Python's enabled MiD preparation invokes the new Java entry point
`org.eqasim.braunschweig.scenario.RunAdaptPassengerAvailabilityConfig`, which uses
the same regional configuration adaptation. The separate name is a capability
check: an old JAR fails visibly instead of silently ignoring the new attribute.
OFF/open workflows invoke the existing `RunAdaptConfig` entry point.

## Alternatives considered

- **Keep household ownership as the passenger gate:** excludes possible access
  through other households despite a positive personal MiD response.
- **Replace driver availability with P_VAUTO:** conflates driver, passenger and
  carsharing access and could create unsupported driver options.
- **Fix all original passenger trips as in France:** removes the endogenous
  passenger choice the maintainer wants to retain. It is a different model
  decision, not required for this availability correction.
- **Permit passenger mode for everyone:** supplies no empirical restriction on
  people reporting no access.
- **Copy an adult's 'anytime' category directly onto children:** overstates what
  household information proves about a child's chauffeuring opportunities.
- **Explicit driver/passenger matching:** potentially useful for joint-trip or
  ridesharing policy studies, but outside this bounded extension.

## Consequences and evidence

Passenger eligibility and the resulting simulated modal split can change. The
change does not establish behavioral calibration. The existing passenger utility
must still be assessed against observed mode shares and child travel patterns.
The categories do not yet create different time-dependent opportunities for
`some` versus `all`.

General-access answers and reported-day behavior can disagree: a valid `P_VAUTO=3`
may coexist with a reported passenger trip. Such cases must be counted and
reported. The primary age-14+ rule preserves the valid survey answer; it does
not silently rewrite it using the diary. Consequently the mode-choice gate can
exclude a passenger option present in the initial diary. This is a limitation
of using the general-access question as an eligibility rule, and should be
examined in the subsequent behavioral comparison.

Implementation, OFF-path test evidence, measured derivation rates and executed
verification belong to the [feature record](../registry/features/mid_passenger_availability.yml)
and its linked run manifest. Raw survey records and raw donor IDs remain local.
