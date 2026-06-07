"""KBA / MiD fleet reference-table readers.

This package provides schema-validated loaders for the committed derived CSVs
under ``<data_path>/braunschweig/kba/derived/`` (produced by
``scripts/extract_kba_fleet.py`` from the local-only raw KBA / MiD xlsx).

Each loader returns a tidy :class:`pandas.DataFrame`, validates its columns,
dtypes and label vocabularies against the canonical sets, and raises a clear
``RuntimeError`` on schema drift -- mirroring the
``braunschweig.data.mid.reference_tables`` pattern. See ``fleet_tables`` for the
individual loaders and the canonical label constants.
"""

from braunschweig.data.kba import fleet_tables  # noqa: F401
