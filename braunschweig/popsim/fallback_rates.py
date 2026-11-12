"""Primary-vs-fallback rate logging for the PopulationSim stage family.

CLAUDE.md "Fallback transparency" requires every fallback to be observable: count the items
that took the PRIMARY method and the items that took the FALLBACK, log both as an explicit
rate, and log a WARNING when the fallback share exceeds a threshold, because a high rate
almost always means the primary method is broken (a format mismatch, an empty join, a wrong
key). This module holds the one threshold and the one log format the popsim sites share
(audit package D, 2026-11-12). It only logs and returns numbers; it never changes a value a
stage returns, so no synpp stage token needs to hash it.

Threshold choice (owner decision 2026-11-12): one module constant, no per-site config keys.
5 % is a review trigger, not a validated tolerance: a rate above it asks a human to look.
"""

from __future__ import annotations

import logging

_SYNPP_TOKEN_EXEMPTION = (
    "logging only: log_fallback_rate formats and logs counts the caller already computed and "
    "returns the fallback share; it never changes a value, a frame or a file a stage returns"
)

#: Fallback share above which :func:`log_fallback_rate` logs at WARNING instead of INFO.
#: ASSUMPTION (owner decision 2026-11-12): 5 % is a review trigger, not a measured tolerance.
FALLBACK_WARN_THRESHOLD: float = 0.05


def log_fallback_rate(
    logger: logging.Logger,
    stage: str,
    n_fallback: int,
    n_total: int,
    *,
    what: str = "",
    threshold: float = FALLBACK_WARN_THRESHOLD,
) -> float:
    """Log ``"[stage] primary P/N (p%), fallback F (f%)"`` and return the fallback share.

    Args:
        logger: the calling module's logger.
        stage: short site tag shown in brackets, e.g. ``"plan_validation.return_home_mode"``.
        n_fallback: items that took the fallback.
        n_total: all items the site decided on (primary + fallback).
        what: optional description of the fallback, appended after a colon.
        threshold: fallback share above which the line is a WARNING.

    Returns:
        The fallback share in [0, 1]; 0.0 when ``n_total`` is 0 (nothing decided, logged at INFO).
    """
    n_fallback = int(n_fallback)
    n_total = int(n_total)
    if n_fallback < 0 or n_fallback > n_total:
        raise ValueError(
            f"[{stage}] fallback count {n_fallback} outside [0, {n_total}]: the caller's counting is broken"
        )
    n_primary = n_total - n_fallback
    share = (n_fallback / n_total) if n_total else 0.0
    primary_pct = 100.0 * n_primary / n_total if n_total else 0.0
    message = (
        f"[{stage}] primary {n_primary}/{n_total} ({primary_pct:.1f}%), "
        f"fallback {n_fallback} ({100.0 * share:.1f}%)"
    )
    if what:
        message += f": {what}"
    if share > threshold:
        logger.warning("%s -- above the %.0f%% review threshold", message, 100.0 * threshold)
    else:
        logger.info(message)
    return share
