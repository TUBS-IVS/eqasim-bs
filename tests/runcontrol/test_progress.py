from braunschweig.runcontrol.collectors import matsim_progress, synpp_progress

SYNPP_LOG = """\
2026-07-08T10:00:00 INFO Executing stage data.osm.cleaned__abc
2026-07-08T10:05:00 INFO Finished running data.osm.cleaned__abc.
2026-07-08T10:05:01 INFO Executing stage synthesis.population.sampled__def
"""

RUNTIME_CSV = "stage,stage_short,start,end,duration_s\n" \
    "a__1,data.osm.cleaned,2026-07-01T00:00:00,2026-07-01T00:05:00,300.0\n" \
    "b__2,synthesis.population.sampled,2026-07-01T00:05:00,2026-07-01T00:20:00,900.0\n"

MATSIM_LOG = """\
2026-07-08T12:00:00 INFO Controler ### ITERATION 0 BEGINS
2026-07-08T12:10:00 INFO Controler ### ITERATION 1 BEGINS
2026-07-08T12:20:00 INFO Controler ### ITERATION 2 BEGINS
"""


def test_synpp_done_and_active_stage():
    p = synpp_progress.parse(SYNPP_LOG, expected=None)
    assert [d["stage_short"] for d in p.done] == ["data.osm.cleaned"]
    assert p.done[0]["duration_s"] == 300.0
    assert p.active == "synthesis.population.sampled"
    assert p.active_since_iso == "2026-07-08T10:05:01"


def test_synpp_expected_weights_from_csv_vs_equal_fallback():
    expected = synpp_progress.expected_from_runtime_csv(RUNTIME_CSV)
    assert expected == [("data.osm.cleaned", 300.0), ("synthesis.population.sampled", 900.0)]
    p = synpp_progress.parse(SYNPP_LOG, expected=expected)
    assert p.weights_source == "runtime_csv"
    assert [e["weight"] for e in p.expected] == [300.0, 900.0]
    # no CSV -> equal widths, and the degradation is explicit, not silent
    q = synpp_progress.parse(SYNPP_LOG, expected=None)
    assert q.weights_source == "equal_fallback"
    assert all(e["weight"] == 1.0 for e in q.expected)


def test_matsim_iteration_and_estimated_eta():
    p = matsim_progress.parse(MATSIM_LOG, last_iteration=9)
    assert p.iteration == 2 and p.last_iteration == 9
    assert p.iteration_seconds_avg == 600.0
    assert p.eta_seconds == 600.0 * 7            # remaining 3..9
    assert p.estimated is True


def test_matsim_no_iterations_yet_yields_none_not_guess():
    p = matsim_progress.parse("no matsim yet", last_iteration=9)
    assert p.iteration is None and p.eta_seconds is None
