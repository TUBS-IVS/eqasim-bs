"""Guard: the gravity stage must consume the ``data.census.filtered`` alias.

Staging ``braunschweig.ipf.attributed`` directly would pin the gravity weights
to one population producer, so the gravity flows could come from a DIFFERENT
population than the demand (the alias resolves to whichever producer the config
selects). The guard is a source check because the offending call is a stage
declaration, not a value a unit test can observe.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.gravity import model  # noqa: E402


def _gravity_package_source() -> str:
    """Concatenate ``model.py`` and every sibling module of its package.

    The guard is deliberately PACKAGE-wide rather than scoped to ``model.py``.
    The gravity stage is being split into sibling modules (issue #267), and a
    negative assertion scoped to a single file goes VACUOUS as soon as the code
    it guards can move: the forbidden ``context.stage`` call would simply live
    in a sibling, the assertion would still pass, and the guard would silently
    stop guarding. Reading the whole package keeps both assertions meaningful
    wherever the stage code lands. The package directory is derived from the
    imported module's ``__file__`` instead of a path literal so the test does
    not depend on the working directory or on where the package lives.
    """
    package_dir = pathlib.Path(model.__file__).resolve().parent
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(package_dir.glob("*.py"))
        if "__pycache__" not in path.parts
    )


def test_gravity_stages_census_filtered_not_ipf_directly():
    src = _gravity_package_source()
    assert 'context.stage("braunschweig.ipf.attributed")' not in src
    assert 'context.stage("data.census.filtered")' in src
