import pandas as pd

from braunschweig.analysis.freight_filter import drop_freight_agents


def test_drops_only_freight_prefixed_persons():
    df = pd.DataFrame({
        "person_id": ["1", "freight_12", "2", "freight_9"],
        "mode": ["car", "truck", "pt", "truck"],
    })
    result = drop_freight_agents(df)
    assert list(result["person_id"]) == ["1", "2"]


def test_noop_without_freight_agents():
    df = pd.DataFrame({"person_id": ["1", "2"], "mode": ["car", "pt"]})
    result = drop_freight_agents(df)
    assert len(result) == 2


def test_logs_excluded_count(caplog):
    import logging
    df = pd.DataFrame({"person_id": ["freight_1", "2"]})
    with caplog.at_level(logging.INFO):
        drop_freight_agents(df, label="test")
    assert any("excluded 1 rows of 1 freight agents" in r.message for r in caplog.records)


def test_logs_the_rows_and_the_distinct_agents_it_excludes(caplog):
    # the filter counts ROWS (trips, activities) and says so: three rows of two distinct freight agents, 75 % of the rows
    import logging
    df = pd.DataFrame({"person_id": ["freight_1", "freight_1", "freight_2", "3"]})
    with caplog.at_level(logging.INFO):
        result = drop_freight_agents(df, label="test")
    assert list(result["person_id"]) == ["3"]
    messages = [r.message for r in caplog.records]
    assert any("[test] excluded 3 rows of 2 freight agents (75.00% of the rows)" in m for m in messages)


def test_warns_when_person_column_missing(caplog):
    import logging
    df = pd.DataFrame({"agent": ["freight_1", "2"]})
    with caplog.at_level(logging.WARNING):
        result = drop_freight_agents(df, label="test")
    assert len(result) == 2
    assert any("NOT filtered" in r.message for r in caplog.records)
