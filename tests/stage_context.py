"""Shared synpp context stand-ins for the popsim stage tests.

Seven test modules had defined the SAME KREIS-toggle context fake, byte-identical in
behaviour but not in signature: some declared ``__init__(self, values)`` and some
``__init__(self, values=None)``, so ``_FakeContext()`` worked in five modules and raised
``TypeError`` in the other two. #327 recorded the duplication; the signature divergence is
what actually costs time, because a test copied from one module into another fails on the
constructor rather than on the behaviour under test.

Only the KREIS-toggle family lives here. Stage-specific fakes with a different contract --
``test_completed_donor_stage``'s, which also serves ``path()`` and ``set_info()``, or
``test_matsim_id_types``' local one -- are deliberately NOT folded in: they stand in for a
different surface, and merging them would produce one fake that is a superset of every
stage's contract and therefore proves less about each.
"""
from __future__ import annotations


class KreisToggleContext:
    """Minimal synpp ExecuteContext stand-in for the KREIS attribute-control toggles.

    Mirrors the REAL execute-time contract: ``config(key)`` takes NO default argument.
    synpp's ``ExecuteContext.config`` raises ``TypeError`` on a positional default -- the
    exact bug the 2026-07-08 server smoke caught -- so a fake that accepted one would let
    a stage pass its tests and fail at run time.

    Values resolve exactly as ``configure()`` declares them: an explicit value in
    ``values`` wins, otherwise the per-entry default from
    ``stage._KREIS_CONTROL_DEFAULT`` (the same map ``configure()`` declares from). A key
    that is neither raises ``KeyError`` rather than returning ``None``, so a test cannot
    accidentally assert against an undeclared key.
    """

    def __init__(self, values=None):
        self._values = dict(values or {})

    def config(self, key):
        if key in self._values:
            return self._values[key]
        from braunschweig.popsim import stage
        for name, toggle_key in stage._KREIS_CONTROL_TOGGLE_KEY.items():
            if key == toggle_key:
                return stage._KREIS_CONTROL_DEFAULT[name]
        raise KeyError(f"_FakeContext: no value or declared default for config key {key!r}")


def prepare_reach_defaults() -> dict:
    """The production defaults of the mode choice reach keys that ``matsim.simulation.prepare.execute`` reads (#678).

    ``execute`` always reads these four keys (``mode_choice_reach.loop_settings`` / ``initial_settings``), so every
    context stand-in that runs ``execute`` must serve them; a fake without them fails with a KeyError or a "got None"
    ValueError that has nothing to do with the behaviour under test. The values come from ``mode_choice_reach`` itself,
    so a changed default reaches every stand-in.
    """
    from braunschweig.matsim.simulation import mode_choice_reach as R
    return {R.KEY_LOOP_ISOLATE: R.DEFAULT_LOOP_ISOLATE, R.KEY_LOOP_MAX_TOUR_LENGTH: R.DEFAULT_LOOP_MAX_TOUR_LENGTH,
            R.KEY_INITIAL_ISOLATE: R.DEFAULT_INITIAL_ISOLATE,
            R.KEY_INITIAL_MAX_TOUR_LENGTH: R.DEFAULT_INITIAL_MAX_TOUR_LENGTH}


#: The mode choice module of a prepared MATSim config, holding the defaults of ``prepare_reach_defaults``: the reach
#: edit of ``prepare.execute`` (``mode_choice_reach.apply``) needs ``tourConstraints`` and leaves this module
#: byte-identical, so a test of another prepare step sees only its own change. Kept realistic (the stock eqasim
#: constraints): ``apply`` raises loudly on a config without them, and that guard stays.
PREPARED_DMC_MODULE = ('\t<module name="DiscreteModeChoice">\n'
                       '\t\t<param name="tourFinder" value="ActivityBased" />\n'
                       '\t\t<param name="tourConstraints" value="FromTripBased, VehicleContinuity" />\n'
                       '\t\t<parameterset type="tourFilter:TourLength">\n'
                       '\t\t\t<param name="maximumLength" value="11" />\n'
                       '\t\t</parameterset>\n'
                       '\t</module>\n')

#: The smallest prepared config ``prepare.execute`` accepts: only the mode choice module above.
PREPARED_MINIMAL_CONFIG = '<?xml version="1.0" encoding="utf-8"?>\n<config>\n' + PREPARED_DMC_MODULE + '</config>\n'
