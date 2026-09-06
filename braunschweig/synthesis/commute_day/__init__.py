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

Pure modules (no file I/O, no synpp stage; each documents its own rules):

* :mod:`state` -- distance classes, keep probability and the seeded state draw.
* :mod:`donor_pool` -- the MiD home-office-day donor pool (attributes + trip chains).
* :mod:`matching` -- the donor for every person drawn to ``home``.
* :mod:`plan_replacement` -- the reporting-day trips table built from a draw and a match.

synpp stages wiring those into the pipeline (see ADR-0104 "Decision" and "Consequences"):

* :mod:`home_office_donors_stage` -- reads the raw MiD delivery and builds the donor pool.
* :mod:`state_stage` -- the state draw plus the donor matching, one row per worker.
* :mod:`trips_day_stage` -- aliased to ``synthesis.population.trips.final``.
* :mod:`activities_day_stage` -- aliased to ``synthesis.population.activities.final``.

Both aliases live in ``configs/base_bs.yml`` alongside the model's flags
(``commute_day_state_enabled``, ``commute_day_far_threshold_km``,
``commute_day_absent_share_far``, ``commute_day_max_not_replaceable_share``). With the model
disabled both ``.final`` stages are pass-throughs of the pre-assignment views. The consumers that
read the ``.final`` view (secondary locations, the MATSim population, synthesis output) are wired
in a later task.
"""
