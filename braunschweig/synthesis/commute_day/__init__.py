"""braunschweig.synthesis.commute_day: the commute-day-state model (ADR-0104, issue #244).

Register-vs-reporting-day gap: the model's work origin-destination is anchored to the BA
Pendleratlas register (who works where for all socially insured employees, no commuting
frequency), while the references it is judged against (SrV 2023, MiD 2023) are reporting-day
surveys (who travelled where on ONE day). This package gives every employed person with an
assigned workplace a ``commute_day_state`` in {``at_workplace``, ``home``, ``absent``} so the
model's reporting day can be compared against those surveys on the same universe.

Two views of the day feed different consumers, kept deliberately separate so that the day
state -- which depends on the ASSIGNED distance -- does not create a dependency cycle with the
location assignment that produces that distance:

* ``synthesis.population.trips`` / ``synthesis.population.activities`` (the pre-assignment
  view) stay unchanged and keep feeding commute distances, primary candidates and primary
  locations.
* ``synthesis.population.trips.final`` / ``synthesis.population.activities.final`` (the
  reporting-day view) feed everything that needs the finished day: secondary chainsolvers, the
  MATSim population, and synthesis output.

Stage modules planned for this package (see the Phase B plan,
``docs/superpowers/plans/2026-09-05-commute-day-state-phase-b.md``, and ADR-0104):
``home_office_donors_stage`` (MiD home-office-day donor pool from the raw MiD delivery),
``state_stage`` (the state draw itself), ``trips_day_stage`` / ``activities_day_stage`` (the
reporting-day trips/activities aliased above), ``spatial_locations_day`` / ``output_day``
(consumers reading the ``.final`` view). This module (Task 1 of the Phase B plan) provides only
the pure state-model core in :mod:`state`: distance classes, keep probability, and the seeded
state draw; the stage modules and the donor pool / matching / plan-replacement logic are added
by later tasks.
"""
