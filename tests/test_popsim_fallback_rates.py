"""Tests for the shared popsim fallback-rate logger (CLAUDE.md "Fallback transparency")."""

import logging

import pytest

from braunschweig.popsim.fallback_rates import FALLBACK_WARN_THRESHOLD, log_fallback_rate

LOGGER = logging.getLogger("test_popsim_fallback_rates")


def test_low_fallback_rate_logs_info_with_primary_and_fallback_counts(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        share = log_fallback_rate(LOGGER, "site", 158, 10000, what="nearest fill")
    assert share == pytest.approx(0.0158)
    (record,) = caplog.records
    assert record.levelno == logging.INFO
    assert record.getMessage() == "[site] primary 9842/10000 (98.4%), fallback 158 (1.6%): nearest fill"


def test_fallback_rate_above_threshold_logs_warning(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        share = log_fallback_rate(LOGGER, "site", 6, 100)
    assert share > FALLBACK_WARN_THRESHOLD
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage().startswith("[site] primary 94/100 (94.0%), fallback 6 (6.0%)")


def test_rate_at_threshold_stays_info(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        log_fallback_rate(LOGGER, "site", 5, 100)
    assert caplog.records[0].levelno == logging.INFO


def test_empty_site_returns_zero_share(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        assert log_fallback_rate(LOGGER, "site", 0, 0) == 0.0
    assert caplog.records[0].levelno == logging.INFO


@pytest.mark.parametrize("n_fallback,n_total", [(-1, 10), (11, 10)])
def test_impossible_counts_raise(n_fallback, n_total):
    with pytest.raises(ValueError, match="outside"):
        log_fallback_rate(LOGGER, "site", n_fallback, n_total)
