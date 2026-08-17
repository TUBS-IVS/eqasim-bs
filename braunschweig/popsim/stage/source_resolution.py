"""Donor-source resolution and KREIS attribute-control activation for popsim_mid.

- :func:`_resolve_source` -- thin factory wrapper around
  :func:`braunschweig.popsim.sources.get_source`, factored out of ``execute``
  so the donor-source lookup can be called and tested independently of
  running PopulationSim.
- :func:`active_kreis_entries` -- return the KREIS attribute-control REGISTRY
  entries (``braunschweig.popsim.kreis_attribute_control.REGISTRY``) active
  for a given run: an entry is active when its per-attribute config toggle
  resolves to "on" AND the donor source is MiD (all KREIS attribute controls
  are MiD-only).

``_KREIS_CONTROL_TOGGLE_KEY`` (the per-entry config-key lookup table
``active_kreis_entries`` reads) lives in the sibling leaf submodule
``braunschweig.popsim.stage.config_keys`` alongside every ``KEY_*`` config-key
constant ``configure`` reads directly; this module imports it from there.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from braunschweig.popsim import sources

from .config_keys import _KREIS_CONTROL_TOGGLE_KEY


def _resolve_source(source_name: str) -> sources.PopsimSource:
    """Return a PopsimSource adapter for the given source name.

    This thin helper is factored out of ``execute`` so it can be called and
    tested independently without running PopulationSim.

    Parameters
    ----------
    source_name:
        Short lowercase source identifier, e.g. ``"mid"``.  Passed through to
        :func:`braunschweig.popsim.sources.get_source`.

    Returns
    -------
    PopsimSource
        A fresh adapter instance for ``source_name``.

    Raises
    ------
    NotImplementedError
        If ``source_name`` is planned-but-not-yet-implemented (e.g. ``"entd"``).
    ValueError
        If ``source_name`` is not a known or planned source name.
    """
    return sources.get_source(source_name)


def active_kreis_entries(context, source_name):
    """Return the KREIS attribute-control REGISTRY entries active for this run.

    An entry is active when its per-attribute toggle resolves to "on" AND the donor
    source is MiD. All KREIS attribute controls are MiD-only (their seed columns have no
    ENTD pendant), so the list is empty for any non-"mid" source. Each toggle defaults per
    ``_KREIS_CONTROL_DEFAULT`` (project rule: new features default on) -- all nine
    entries (economic_status, number_of_cars, number_of_bicycles, has_ebike, trip_class,
    employment_status, work_participation, leisure_participation, education_participation)
    default "on". The has_ebike source column (H_ANZPED) was server-verified 2026-07-08
    (issue #116). trip_class (2026-07-08 follow-on), employment_status (feature #172 task
    4), and work_participation / leisure_participation / education_participation (feature
    #224 tasks 4-5) are PERSON-level entries; each is wired on both seed paths (its
    per-Kreis target partitions the PERSON total, not the household total -- see the
    KREIS block in execute()). employment_status additionally restricts that PERSON
    total to age >= 14 (its REGISTRY entry's min_age), see person_total_by_kreis_min_age.

    Called at EXECUTE time: synpp's ``ExecuteContext.config(key)`` takes NO default
    argument (a positional default raises ``TypeError``; the same pitfall was fixed for
    home_cell's ``KEY_HOME_MATCHING`` before). The per-entry defaults are therefore
    declared once in :func:`configure` (``context.config(KEY, default)`` on the
    ConfigContext) and this function reads the RESOLVED value by key only.

    Returns the entries in REGISTRY order (economic_status first), so downstream
    catalog rendering and count-table merges are deterministic.
    """
    from braunschweig.popsim import kreis_attribute_control as _kac
    if source_name != "mid":
        return []
    active = []
    for entry in _kac.REGISTRY:
        toggle_key = _KREIS_CONTROL_TOGGLE_KEY.get(entry.name)
        if toggle_key is None:
            raise ValueError(
                f"active_kreis_entries: no config toggle registered for REGISTRY entry "
                f"{entry.name!r}; add it to _KREIS_CONTROL_TOGGLE_KEY.")
        if str(context.config(toggle_key)).strip().lower() == "on":
            active.append(entry)
    return active
