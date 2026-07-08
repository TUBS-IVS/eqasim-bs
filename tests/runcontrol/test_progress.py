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

# Live coloured-console format from braunschweig.logging_setup.ColorFormatter, as
# tee'd into logs/rc_<id>.log by run_pipeline.sh on a real run (HH:MM:SS only,
# U+2502 box-drawing separators, padded fields).
SYNPP_CONSOLE_LOG = (
    "13:47:16 │ INFO    │ synpp            │ "
    "Executing stage matsim.runtime.java__33163fea50c0df3e4\n"
    "13:52:16 │ INFO    │ synpp            │ "
    "Finished running matsim.runtime.java__33163fea50c0df3e\n"
    "13:52:17 │ INFO    │ synpp            │ "
    "Executing stage synthesis.population.sampled__def\n"
)

SYNPP_CONSOLE_MIDNIGHT_LOG = (
    "23:59:30 │ INFO    │ synpp            │ "
    "Executing stage matsim.runtime.java__33163fea50c0df3e4\n"
    "00:00:30 │ INFO    │ synpp            │ "
    "Finished running matsim.runtime.java__33163fea50c0df3e\n"
)
# NOTE: the exec/finish hash suffixes above intentionally differ by one hex
# digit, mirroring the real console output pair reported in issue #119; this
# is harmless because _HASH_RE strips the trailing "__<hex>" from both sides
# before stages are matched by their short name.

# Legacy plain default logging format (no timestamp at all), seen in older run logs.
SYNPP_BARE_LOG = (
    "INFO:synpp:Executing stage braunschweig.synthesis.something"
    "__34b450886790162340ff1eeb03f35ffd\n"
    "INFO:synpp:Finished running braunschweig.synthesis.something"
    "__34b450886790162340ff1eeb03f35ffd.\n"
    "INFO:synpp:Executing stage synthesis.population.sampled__def\n"
)


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


def test_synpp_console_format_done_active_and_duration():
    p = synpp_progress.parse(SYNPP_CONSOLE_LOG, expected=None)
    assert [d["stage_short"] for d in p.done] == ["matsim.runtime.java"]
    assert p.done[0]["duration_s"] == 300.0
    assert p.active == "synthesis.population.sampled"
    assert p.active_since_iso == "13:52:17"
    assert p.log_format == "console"


def test_synpp_console_format_midnight_wrap():
    p = synpp_progress.parse(SYNPP_CONSOLE_MIDNIGHT_LOG, expected=None)
    assert [d["stage_short"] for d in p.done] == ["matsim.runtime.java"]
    assert p.done[0]["duration_s"] == 60.0
    assert p.log_format == "console"


def test_synpp_bare_format_no_timestamps():
    p = synpp_progress.parse(SYNPP_BARE_LOG, expected=None)
    assert [d["stage_short"] for d in p.done] == ["braunschweig.synthesis.something"]
    assert p.done[0]["duration_s"] is None
    assert p.active == "synthesis.population.sampled"
    assert p.active_since_iso is None
    assert p.log_format == "bare"
