"""Every stage that reads the VG250-EW archive puts the archive's content digest into its token.

A stage whose ``validate()`` ignores the archive keeps serving its cached result after the
archive is replaced (a new Gebietsstand, a corrected download): the run reports success on stale
municipality data. One parametrized test drives the real ``validate()`` of every reader with a
context that holds exactly the keys the stage's own ``configure()`` declared (as synpp's validate
context does: an undeclared key raises ``KeyError``). A second guard scans the first-party code so
that a NEW stage reading the archive cannot skip the token.
"""
from __future__ import annotations

import ast
import importlib
import pathlib
from dataclasses import dataclass, field

import pytest

from braunschweig import synpp_tokens

REPO = pathlib.Path(__file__).resolve().parents[1]

VG250_ARCHIVE = "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
DIGEST_KEY = "vg250_sha256"

_NO_DEFAULT = object()


class _ConfigureRecorder:
    """Stands in for synpp's configure context: records every declared key and its value."""

    def __init__(self, overrides):
        self.values = {}
        self._overrides = overrides

    def config(self, key, default=_NO_DEFAULT, **_options):
        if key in self._overrides:
            value = self._overrides[key]
        elif key in self.values:  # a repeated read after the declaration that carried the default
            value = self.values[key]
        elif default is not _NO_DEFAULT:
            value = default
        else:
            raise KeyError(f"{key!r} is declared without a default; give it in the test overrides")
        self.values.setdefault(key, value)
        return value

    def stage(self, *_args, **_kwargs):
        return None


class _ValidateContext:
    """synpp's validate context: ``config(key)`` takes ONE argument; an undeclared key raises."""

    def __init__(self, values):
        self._values = values

    def config(self, key):
        return self._values[key]


@dataclass(frozen=True)
class Reader:
    module: str
    #: Config values that switch every VG250 read ON (merged over the common values).
    on: dict = field(default_factory=dict)
    #: Config values under which ``execute`` never opens the archive (None: it always does).
    off: tuple = ()
    #: Declared keys whose value is a data-relative file that ``validate`` checks or sizes.
    path_keys: tuple = ()


_CORDON_ON = {"cordon_enabled": True}
READERS = (
    Reader("braunschweig.data.bbsr.regiostar", path_keys=("regiostar.path",)),
    Reader("braunschweig.data.external_workplaces"),
    Reader("braunschweig.data.external_secondary_points"),
    Reader("braunschweig.data.zensus_grid.population", path_keys=("zensus_grid.population_path",)),
    Reader("braunschweig.data.census.population",
           path_keys=("braunschweig.destatis_population_path", "braunschweig.urbistat_gemeinden_path")),
    Reader("eqasim_common.data.population.raw"),
    Reader("braunschweig.data.cordon_gemeinden", _CORDON_ON, off=({"cordon_enabled": False},)),
    Reader("braunschweig.data.cordon_pt_gates", _CORDON_ON, off=({"cordon_enabled": False},)),
    # The real-origin override is the only incommuters path that loads the archive.
    Reader("braunschweig.synthesis.incommuters",
           {**_CORDON_ON, "cordon_incommuter_real_origin": True},
           off=({"cordon_enabled": False},
                {"cordon_enabled": True, "cordon_incommuter_real_origin": False})),
    Reader("braunschweig.synthesis.student_incommuters", _CORDON_ON, off=({"cordon_enabled": False},)),
)


def _prepare(module_name, config, data_path):
    """Validate context of ``module`` under ``config``, with the files its validate() checks."""
    common = {
        "data_path": str(data_path), "random_seed": 0, "sampling_rate": 1.0,
        "braunschweig.political_prefix": ["03101"], "cordon_network_source_buffer_m": 45000.0,
        "processes": 1,
    }
    recorder = _ConfigureRecorder({**common, **config})
    module = importlib.import_module(module_name)
    module.configure(recorder)
    return module, recorder.values


def _create(data_path, relative, content=b"placeholder"):
    target = pathlib.Path(data_path) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _vg250_part(token):
    return token[DIGEST_KEY] if isinstance(token, dict) else token


@pytest.fixture(autouse=True)
def _cheap_helper_digests(monkeypatch):
    """The module digests are covered by the helper-hash tests; here they would only cost seconds
    and blur which part of the token moved."""
    monkeypatch.setattr(synpp_tokens, "source_token", lambda *_args, **_kwargs: {"helpers": "stub"})


@pytest.mark.parametrize("reader", READERS, ids=lambda reader: reader.module)
def test_token_follows_the_vg250_archive_content(reader, tmp_path):
    module, values = _prepare(reader.module, reader.on, tmp_path)
    for key in reader.path_keys:
        _create(tmp_path, values[key])
    context = _ValidateContext(values)

    # Equal length on purpose: a size-only token cannot tell the two editions apart.
    archive = _create(tmp_path, VG250_ARCHIVE, b"VG250 edition 2023")
    first = module.validate(context)
    _create(tmp_path, VG250_ARCHIVE, b"VG250 edition 2024")
    replaced = module.validate(context)
    _create(tmp_path, VG250_ARCHIVE, b"VG250 edition 2023")
    restored = module.validate(context)

    assert first != replaced, f"{reader.module}: a replaced VG250 archive keeps the cache token"
    assert restored == first, f"{reader.module}: the token must depend on nothing but the inputs"
    assert _vg250_part(first) == synpp_tokens.file_sha256(archive)


@pytest.mark.parametrize("reader", [r for r in READERS if r.off], ids=lambda reader: reader.module)
def test_vg250_digest_is_skipped_where_execute_never_opens_the_archive(reader, tmp_path):
    """No archive on disk: validate must neither fail nor carry a digest (the archive is optional)."""
    for off_config in reader.off:
        module, values = _prepare(reader.module, off_config, tmp_path)
        token = module.validate(_ValidateContext(values))
        assert isinstance(token, dict) and token[DIGEST_KEY] is None, (reader.module, off_config)


@pytest.mark.parametrize("reader", READERS, ids=lambda reader: reader.module)
def test_a_missing_vg250_archive_fails_validate_where_execute_needs_it(reader, tmp_path):
    module, values = _prepare(reader.module, reader.on, tmp_path)
    for key in reader.path_keys:
        _create(tmp_path, values[key])
    with pytest.raises((RuntimeError, FileNotFoundError)):
        module.validate(_ValidateContext(values))


# ---------------------------------------------------------------------------- completeness guard

_FIRST_PARTY_ROOTS = ("braunschweig", "data", "documentation", "eqasim_common", "matsim", "synthesis")
_READER_MARKERS = (VG250_ARCHIVE.split("/")[1], "cordon_vg250_path", "germany.population_path")
#: Stage modules that name the archive without opening it. A new entry needs a stated reason.
_NAMES_ARCHIVE_WITHOUT_READING_IT = {
    "braunschweig.data.alkis": "prints the archive path in the 'run the preprocessing script' hint; reads the "
                               "preprocessed ALKIS file",
}


def _stage_modules_naming_the_vg250_archive():
    found = set()
    for root in _FIRST_PARTY_ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not any(marker in source for marker in _READER_MARKERS):
                continue
            tree = ast.parse(source)
            if any(isinstance(node, ast.FunctionDef) and node.name == "execute" for node in tree.body):
                parts = list(path.relative_to(REPO).with_suffix("").parts)
                found.add(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return found


def test_the_scan_finds_the_reference_reader():
    """Positive control: a scan that finds nothing would pass the guard below vacuously."""
    assert "braunschweig.data.bbsr.regiostar" in _stage_modules_naming_the_vg250_archive()


def test_every_stage_reading_the_vg250_archive_is_covered_by_the_token_test():
    found = _stage_modules_naming_the_vg250_archive()
    assert set(_NAMES_ARCHIVE_WITHOUT_READING_IT) <= found, \
        "an allowlisted module no longer names the archive: drop it from the allowlist"
    covered = {reader.module for reader in READERS}
    assert found - set(_NAMES_ARCHIVE_WITHOUT_READING_IT) == covered
