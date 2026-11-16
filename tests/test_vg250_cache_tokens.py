"""Every stage that reads the VG250-EW archive puts the archive's content digest into its token.

A stage whose ``validate()`` ignores the archive keeps serving its cached result after the
archive is replaced (a new Gebietsstand, a corrected download): the run reports success on stale
municipality data. One parametrized test drives the real ``validate()`` of every reader listed in
``READERS`` with a context that holds exactly the keys the stage's own ``configure()`` declared
(as synpp's validate context does: an undeclared key raises ``KeyError``). A second guard scans the
first-party roots for stage modules that NAME the archive, its config keys or the analysis loader
``braunschweig.analysis.spatial``, and fails when one is neither listed in ``READERS`` nor in a named
allowlist. It checks those names only: a stage that opens the archive under a name the scan does not
look for is not found by it.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
from dataclasses import dataclass, field

import pytest

from braunschweig import synpp_tokens
from braunschweig.synpp_fork.staleness import token_difference
from scripts.audit_synpp_helper_hash import ROOTS  # one owner of the first-party root list

REPO = pathlib.Path(__file__).resolve().parents[1]

VG250_ARCHIVE = "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
#: Dataset id of the archive: the key of its digest in the ``files`` part of a structured source token.
DATASET_ID = "vg250_ew"
#: The RegioStaR workbook the analysis loader ``braunschweig.analysis.spatial`` reads from the repository
#: root, and its dataset id (the second repository-anchored input of the analysis readers).
REGIOSTAR_SHEET = "regiostar/regiostar_referenzdateien_2024.xlsx"
REGIOSTAR_DATASET_ID = "regiostar"
#: Key of the digest in the plain dict tokens of the stages that hash no module source.
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
    #: Config variants under which ``execute`` never opens the archive (empty: it always does).
    off: tuple = ()
    #: Declared keys whose value is a data-relative file that ``validate`` checks or sizes.
    path_keys: tuple = ()
    #: True for a stage whose token is a structured source token (module digests under ``modules``).
    hashes_source: bool = False
    #: True for the analysis stages that read the archive through ``braunschweig.analysis.spatial``,
    #: whose path is anchored at the repository root (``spatial.VG250_ZIP``), not at ``data_path``.
    repo_anchored: bool = False
    #: True where ``execute`` tolerates a missing archive (it renders without the per-Kreis parts):
    #: validate must then warn and carry no digest instead of failing.
    tolerant: bool = False


_CORDON_ON = {"cordon_enabled": True}
READERS = (
    Reader("braunschweig.data.bbsr.regiostar", path_keys=("regiostar.path",)),
    Reader("braunschweig.data.external_workplaces", hashes_source=True),
    Reader("braunschweig.data.external_secondary_points", hashes_source=True),
    Reader("braunschweig.data.zensus_grid.population", path_keys=("zensus_grid.population_path",)),
    Reader("braunschweig.data.census.population",
           path_keys=("braunschweig.destatis_population_path", "braunschweig.urbistat_gemeinden_path")),
    Reader("eqasim_common.data.population.raw"),
    Reader("braunschweig.data.cordon_gemeinden", _CORDON_ON, off=({"cordon_enabled": False},),
           hashes_source=True),
    Reader("braunschweig.data.cordon_pt_gates", _CORDON_ON, off=({"cordon_enabled": False},),
           hashes_source=True),
    # The real-origin override is the only incommuters path that loads the archive.
    Reader("braunschweig.synthesis.incommuters",
           {**_CORDON_ON, "cordon_incommuter_real_origin": True},
           off=({"cordon_enabled": False},
                {"cordon_enabled": True, "cordon_incommuter_real_origin": False}),
           hashes_source=True),
    Reader("braunschweig.synthesis.student_incommuters", _CORDON_ON, off=({"cordon_enabled": False},),
           hashes_source=True),
    Reader("braunschweig.analysis.synthesis.commute_distance_by_kreis", hashes_source=True, repo_anchored=True),
    Reader("braunschweig.analysis.synthesis.work_participation_by_kreis", hashes_source=True,
           repo_anchored=True),
    Reader("braunschweig.analysis.synthesis.plan_structure_vs_srv", hashes_source=True, repo_anchored=True),
    Reader("braunschweig.analysis.analysis_suite", {"analysis_suite_enabled": True},
           off=({"analysis_suite_enabled": False},), hashes_source=True, repo_anchored=True, tolerant=True),
    Reader("braunschweig.analysis.simwrapper_export", {"simwrapper_export_enabled": True},
           off=({"simwrapper_export_enabled": False},), hashes_source=True, repo_anchored=True,
           tolerant=True),
)
SOURCE_TOKEN_READERS = tuple(reader for reader in READERS if reader.hashes_source)


def _prepare(reader, config, data_path, monkeypatch):
    """Validate context of the reader's module under ``config``, with the files its validate() checks.

    A repository-anchored reader is pointed at the archive under ``data_path`` (as the analysis
    tests of ``braunschweig.analysis.spatial`` do), so one test layout serves every reader and the
    real archive in the checkout is never read. The same holds for the RegioStaR sheet that module
    reads from the repository root (absent unless a test creates it)."""
    if reader.repo_anchored:
        from braunschweig.analysis import spatial
        monkeypatch.setattr(spatial, "VG250_ZIP", pathlib.Path(data_path) / VG250_ARCHIVE)
        monkeypatch.setattr(spatial, "REGIOSTAR_XLSX", pathlib.Path(data_path) / REGIOSTAR_SHEET)
    module_name = reader.module
    common = {
        "data_path": str(data_path), "random_seed": 0, "sampling_rate": 1.0,
        "braunschweig.political_prefix": ["03101"], "cordon_network_source_buffer_m": 45000.0,
        "processes": 1, "output_path": str(pathlib.Path(data_path) / "output"),
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


@pytest.fixture
def stub_module_digests(monkeypatch):
    """Replace the module digests by a fixed structured token. They are covered by the helper-hash
    tests and would cost seconds per stage here, where only the archive part of the token is under
    test; the stub keeps the token's real shape (``modules`` at the top level) so ``token_difference``
    and the flat-form assertion still see what a stage does with it."""
    monkeypatch.setattr(synpp_tokens, "source_token", lambda *_args, **_kwargs: {
        "format": 0, "python": "stub", "modules": {"stub.helper": "0"}})


def _vg250_part(token):
    """The archive digest inside a token of any of the three shapes in use."""
    if isinstance(token, dict) and "files" in token:  # structured source token
        return token["files"][DATASET_ID]
    if isinstance(token, dict):  # plain dict token
        return token[DIGEST_KEY]
    return token  # the digest alone


@pytest.mark.usefixtures("stub_module_digests")
@pytest.mark.parametrize("reader", READERS, ids=lambda reader: reader.module)
def test_token_follows_the_vg250_archive_content(reader, tmp_path, monkeypatch):
    module, values = _prepare(reader, reader.on, tmp_path, monkeypatch)
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
    assert (isinstance(first, dict) and "modules" in first) == reader.hashes_source
    if reader.hashes_source:
        # The staleness report must name the archive, not call the token opaque.
        assert token_difference(replaced, first) == ["files"]


@pytest.mark.parametrize("reader", SOURCE_TOKEN_READERS, ids=lambda reader: reader.module)
def test_a_helper_edit_still_names_the_edited_module(reader, tmp_path, monkeypatch):
    """The digest part must not hide the module digests: ``token_difference`` names the edited module.

    Real module digests, on purpose: the proof is that an edit of a real helper reaches the token of
    the real stage as a module name. That costs a few seconds for the stages with long helper lists."""
    module, values = _prepare(reader, reader.on, tmp_path, monkeypatch)
    _create(tmp_path, VG250_ARCHIVE, b"VG250 edition 2023")
    context = _ValidateContext(values)
    before = module.validate(context)
    probed = sorted(before["modules"])[0]
    getsource = inspect.getsource
    monkeypatch.setattr(inspect, "getsource", lambda target: getsource(target) + (
        "\nZ_PROBE = 1\n" if getattr(target, "__name__", None) == probed else ""))
    assert token_difference(module.validate(context), before) == [probed]


@pytest.mark.usefixtures("stub_module_digests")
@pytest.mark.parametrize("reader", [r for r in READERS if r.off], ids=lambda reader: reader.module)
def test_vg250_digest_is_skipped_where_execute_never_opens_the_archive(reader, tmp_path, monkeypatch):
    """No archive on disk: validate must neither fail nor carry a digest (the archive is optional)."""
    for off_config in reader.off:
        module, values = _prepare(reader, off_config, tmp_path, monkeypatch)
        token = module.validate(_ValidateContext(values))
        assert isinstance(token, dict) and _vg250_part(token) is None, (reader.module, off_config)


@pytest.mark.usefixtures("stub_module_digests")
@pytest.mark.parametrize("reader", [r for r in READERS if not r.tolerant], ids=lambda reader: reader.module)
def test_a_missing_vg250_archive_fails_validate_where_execute_needs_it(reader, tmp_path, monkeypatch):
    module, values = _prepare(reader, reader.on, tmp_path, monkeypatch)
    for key in reader.path_keys:
        _create(tmp_path, values[key])
    with pytest.raises(RuntimeError, match="VG250 archive"):
        module.validate(_ValidateContext(values))


@pytest.mark.usefixtures("stub_module_digests")
@pytest.mark.parametrize("reader", [r for r in READERS if r.tolerant], ids=lambda reader: reader.module)
def test_a_missing_vg250_archive_is_reported_where_execute_tolerates_it(reader, tmp_path, monkeypatch,
                                                                        caplog):
    """The tolerant stages run on without the archive, so validate must not fail; but the missing
    archive must be observable (a WARNING naming its path), never a silent ``None``."""
    module, values = _prepare(reader, reader.on, tmp_path, monkeypatch)
    with caplog.at_level("WARNING"):
        token = module.validate(_ValidateContext(values))
    assert _vg250_part(token) is None
    expected_path = str(pathlib.Path(tmp_path) / VG250_ARCHIVE)
    assert any(record.levelname == "WARNING" and expected_path in record.getMessage()
               for record in caplog.records), f"{reader.module}: a missing archive passed validate silently"


@pytest.mark.usefixtures("stub_module_digests")
@pytest.mark.parametrize("reader", [r for r in READERS if r.repo_anchored], ids=lambda reader: reader.module)
def test_token_follows_the_regiostar_sheet_the_analysis_loader_reads(reader, tmp_path, monkeypatch, caplog):
    """``spatial.load_regiostar`` reads the sheet from the repository root and tolerates its absence
    (RS7 columns stay NA): the token must change when the sheet appears or is replaced, and a missing
    sheet must be a WARNING naming its path, not a silent ``None``."""
    module, values = _prepare(reader, reader.on, tmp_path, monkeypatch)
    _create(tmp_path, VG250_ARCHIVE, b"VG250 edition 2023")
    context = _ValidateContext(values)

    with caplog.at_level("WARNING"):
        missing = module.validate(context)
    sheet = _create(tmp_path, REGIOSTAR_SHEET, b"RegioStaR 2023")
    present = module.validate(context)
    _create(tmp_path, REGIOSTAR_SHEET, b"RegioStaR 2024")
    replaced = module.validate(context)

    assert missing["files"][REGIOSTAR_DATASET_ID] is None
    assert any(record.levelname == "WARNING" and str(pathlib.Path(tmp_path) / REGIOSTAR_SHEET)
               in record.getMessage() for record in caplog.records), "a missing RegioStaR sheet passed silently"
    _create(tmp_path, REGIOSTAR_SHEET, b"RegioStaR 2023")
    assert present["files"][REGIOSTAR_DATASET_ID] == synpp_tokens.file_sha256(sheet)
    assert present != replaced and token_difference(replaced, present) == ["files"]


# ---------------------------------------------------------------------------- name scan

#: What the scan looks for in a stage module: the archive file name, the config keys that point at it
#: and the analysis loader (``braunschweig.analysis.spatial``) that opens it from the repository root.
_READER_MARKERS = (VG250_ARCHIVE.split("/")[1], "cordon_vg250_path", "germany.population_path",
                   "load_vg250_layer", "analysis.spatial", "analysis import spatial", "spatial_metrics")
#: Stage modules that name the archive without opening it. A new entry needs a stated reason.
_NAMES_ARCHIVE_WITHOUT_READING_IT = {
    "braunschweig.data.alkis": "prints the archive path in the 'run the preprocessing script' hint; reads the "
                               "preprocessed ALKIS file",
}


def _stage_modules_naming_the_vg250_archive():
    found = set()
    for root in ROOTS:
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


def test_every_stage_naming_the_vg250_archive_is_covered_or_allowlisted():
    found = _stage_modules_naming_the_vg250_archive()
    allowlisted = set(_NAMES_ARCHIVE_WITHOUT_READING_IT)
    assert allowlisted <= found, \
        f"an allowlisted module no longer names the archive: drop it from the allowlist ({allowlisted - found})"
    assert found - allowlisted == {reader.module for reader in READERS}
