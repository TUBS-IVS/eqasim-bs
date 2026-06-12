"""Validate and locate the german-wide-freight v3 raw inputs (local-only).

The two files (100% Germany-wide freight plans + the Germany-Europe routing
network) are NOT committed; they live under
``<data_path>/braunschweig/freight/german-wide-freight-v3/`` and are fetched by
``scripts/download_german_wide_freight.py``. This stage fails early with that
exact command when they are missing (no silent fallback).

Provenance: Lu, Martins-Turner, Nagel (2022), doi:10.1016/j.procs.2022.03.080;
VSP public SVN german-wide-freight/v3.
"""
import os

DEFAULT_PLANS_PATH = "braunschweig/freight/german-wide-freight-v3/german_freight.100pct.plans.xml.gz"
DEFAULT_NETWORK_PATH = "braunschweig/freight/german-wide-freight-v3/germany-europe-network.xml.gz"

GZIP_MAGIC = b"\x1f\x8b"


def configure(context):
    context.config("data_path")
    context.config("freight_plans_path", DEFAULT_PLANS_PATH)
    context.config("freight_network_path", DEFAULT_NETWORK_PATH)


def _resolve(context, key):
    path = context.config(key)
    if not os.path.isabs(path):
        path = os.path.join(context.config("data_path"), path)
    return path


def _validate_gzip(path):
    if not os.path.exists(path):
        raise RuntimeError(
            "german-wide-freight input missing: %s\n"
            "Run: python scripts/download_german_wide_freight.py\n"
            "(or disable the feature with freight_enabled: false)" % path)
    with open(path, "rb") as f:
        if f.read(2) != GZIP_MAGIC:
            raise RuntimeError("not a valid gzip file (corrupt download?): %s" % path)


def execute(context):
    plans_path = _resolve(context, "freight_plans_path")
    network_path = _resolve(context, "freight_network_path")
    _validate_gzip(plans_path)
    _validate_gzip(network_path)
    return dict(plans_path=plans_path, network_path=network_path)


def validate(context):
    # Cache key: input file sizes, so a re-download invalidates downstream stages.
    total = 0
    for key in ("freight_plans_path", "freight_network_path"):
        path = _resolve(context, key)
        if os.path.exists(path):
            total += os.path.getsize(path)
    return total
