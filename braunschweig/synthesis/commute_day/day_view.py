"""Shared seam helpers for the reporting-day consumer overrides (ADR-0104, issue #244, Phase B).

ADR-0104's two-view trips architecture keeps the PRE-ASSIGNMENT day
(``synthesis.population.trips`` / ``...activities``) feeding the commute distances and the
primary location assignment, while everything that needs the FINISHED day reads the
REPORTING-DAY view (``synthesis.population.trips.final`` / ``...activities.final``).

Two consumers of that finished day are vendored eqasim modules that must not be edited
(``synthesis.population.spatial.locations`` and ``synthesis.output``). Each is therefore wrapped
by a thin Braunschweig override that runs the vendored ``configure`` / ``execute`` through the
two proxies below, so the eqasim logic itself is reused verbatim and can never drift from the
vendored copy:

* :class:`ConfigureDayViewContext` -- configure-time proxy that rewrites the two pre-assignment
  stage names to their reporting-day aliases as the vendored ``configure`` declares them. The
  override therefore declares EXACTLY the stages the vendored module reads, with the day view
  substituted, and stays in sync automatically when a future eqasim version adds a dependency.
* :class:`StageOverrideContext` -- execute-time proxy that answers a fixed set of stage names
  from frames the override already holds and delegates EVERYTHING else (other stages,
  ``config``, ``path``, ``progress``, ...) to the real synpp context, so an undeclared stage
  still fails in the real context rather than being silently answered here.
"""
from __future__ import annotations

#: Pre-assignment stage name -> reporting-day alias. The alias targets are
#: ``braunschweig.synthesis.commute_day.trips_day_stage`` /
#: ``...activities_day_stage`` (see ``configs/base_bs.yml``); with
#: ``commute_day_state_enabled`` false both are pass-throughs of the pre-assignment views, so
#: substituting them is a no-op on the OFF path.
DAY_STAGE_BY_NAME = {
    "synthesis.population.trips": "synthesis.population.trips.final",
    "synthesis.population.activities": "synthesis.population.activities.final",
}


class ConfigureDayViewContext:
    """synpp ``ConfigurationContext`` proxy that substitutes the reporting-day stage names.

    ``stage()`` maps every name in :data:`DAY_STAGE_BY_NAME` onto its ``.final`` alias and
    forwards everything else unchanged; ``config()`` and any other attribute are delegated
    verbatim, so the vendored ``configure`` keeps its own defaults and its own config-dependent
    branches (e.g. ``synthesis.output``'s ``mode_choice`` branch).
    """

    def __init__(self, context, stage_map=None):
        self._context = context
        self._stage_map = DAY_STAGE_BY_NAME if stage_map is None else stage_map

    def stage(self, descriptor, *args, **kwargs):
        if isinstance(descriptor, str) and descriptor in self._stage_map:
            descriptor = self._stage_map[descriptor]
        return self._context.stage(descriptor, *args, **kwargs)

    def __getattr__(self, name):
        # config(), stage_is_config_requested(), ... -- everything the vendored configure may
        # use stays the real context's own implementation.
        return getattr(self._context, name)


class StageOverrideContext:
    """synpp ``ExecuteContext`` proxy answering a fixed set of stage names from held frames.

    ``overrides`` maps a stage name the vendored ``execute`` reads onto the object to hand it
    (e.g. ``"synthesis.population.activities"`` -> the reporting-day activities frame). Any
    other stage name -- and every other context method (``config``, ``path``, ``progress``,
    ...) -- goes to the real context, which still refuses anything ``configure`` did not
    declare. The proxy never invents a value: a name absent from ``overrides`` is NOT silently
    answered with ``None``.
    """

    def __init__(self, context, overrides):
        self._context = context
        self._overrides = dict(overrides)

    def stage(self, name, *args, **kwargs):
        if name in self._overrides:
            return self._overrides[name]
        return self._context.stage(name, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._context, name)
