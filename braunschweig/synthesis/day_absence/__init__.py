"""braunschweig.synthesis.day_absence: the general-day-absence state model (ADR-0110, issue #370).

Independent of the register-vs-reporting-day commute model (ADR-0104,
:mod:`braunschweig.synthesis.commute_day`), some persons are simply away from home for the whole
reporting day for reasons that have nothing to do with commuting -- a family vacation, a hospital
stay, a business trip. This package gives EVERY synthetic person a ``day_absence_state`` in
{``present``, ``absent_household``, ``absent_individual``}, anchored to the SrV 2023 full-day
absence aggregates (``eqasim-data/data/braunschweig/srv/srv2023_absence_by_age_band.csv``,
``srv2023_absence_household_by_size.csv``).

Two-stage seeded draw (pure module :mod:`absence`, no synpp stage, no other file I/O apart from
the reference loader):

1. **Household stage.** With probability ``p_household(size_class)`` (SrV, by household size) the
   WHOLE household is away: ``absent_household``. This is what reproduces the observed clustering
   of absent persons into fully absent households.
2. **Individual residual stage.** Per age band, the SrV person-level rate minus what the household
   stage already realised in that band gives a residual probability; every still-present person is
   drawn ``absent_individual`` at that rate. In expectation the per-band absence rates match the
   SrV rates regardless of how much the household stage already contributed; an age band where the
   household stage alone overshoots the SrV rate gets a residual of 0 and a WARNING (never a
   silent negative probability).

Composition with the commute-day state (ADR-0104): the two mechanisms are independent and are
combined only in the REPORTING-DAY view of the day plan. A person is trip-less there when
``commute_day_state == "absent"`` OR ``day_absence_state != "present"`` (the union of commute
absence and general absence; a household on vacation removes its escorted children the same way a
solo day-absence does). This mirrors the two views ADR-0104 already established, so no new
dependency cycle is introduced:

* ``synthesis.population.trips`` / ``synthesis.population.activities`` (the pre-assignment view)
  are unaffected by this package and keep feeding location assignment.
* ``synthesis.population.trips.final`` / ``synthesis.population.activities.final`` (the
  reporting-day view) is where a person's commute-day state and general-day-absence state are
  composed into the trips that actually reach the MATSim population and synthesis output.

The synpp stage that reads ``synthesis.population.enriched``, calls :func:`absence.draw_absence`
and wires the result into the reporting-day view (plus the ``day_absence_enabled`` /
``day_absence_household_stage_enabled`` flags in ``configs/base_bs.yml``) is added in a later task
of issue #370; with the feature disabled every person stays ``present`` (``reason = "disabled"``)
and the reporting-day view is byte-identical to today.
"""
