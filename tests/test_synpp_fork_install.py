"""Install contract of the synpp fork: patched names, SHA guard, semantic module hash."""
import importlib
import inspect
import sys
import textwrap

import synpp
import synpp.pipeline as upstream


def test_upstream_pipeline_sha_is_pinned():
    from braunschweig import synpp_fork
    assert synpp_fork.upstream_pipeline_sha256() == synpp_fork.UPSTREAM_PIPELINE_SHA256


def test_install_patches_run_and_get_stage_hash():
    from braunschweig import synpp_fork
    from braunschweig.synpp_fork import pipeline as fork
    synpp_fork.install()
    assert upstream.run is fork.run
    assert synpp.run is fork.run            # the package re-export, used by tests and notebooks
    assert upstream.get_stage_hash is fork.get_stage_hash
    assert upstream.process_stages.__module__ == "braunschweig.synpp_deterministic"


def test_patches_are_named_and_marked_in_the_source():
    from braunschweig import synpp_fork
    from braunschweig.synpp_fork import pipeline as fork
    source = inspect.getsource(fork)
    for name in synpp_fork.PATCHES:
        assert ("# PATCH " + name) in source


def test_class_descriptor_hash_ignores_comment_edit(tmp_path, monkeypatch):
    import importlib.util
    from braunschweig.synpp_fork import pipeline as fork
    classes = []
    for index, comment in enumerate(("", "        # a comment that does not change behaviour")):
        path = tmp_path / ("class_probe_%d.py" % index)
        lines = ["class Stage:", "    def f(self):", comment, "        return 1", ""]
        path.write_bytes(chr(10).join(lines).encode("utf-8"))
        spec = importlib.util.spec_from_file_location("class_probe_%d" % index, str(path))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)  # inspect resolves classes via sys.modules
        spec.loader.exec_module(module)
        classes.append(module.Stage)
    assert fork.get_stage_hash(classes[0]) == fork.get_stage_hash(classes[1])


def test_a_comment_edit_to_a_stage_module_does_not_devalidate_it(tmp_path, monkeypatch):
    from braunschweig import synpp_fork
    synpp_fork.install()
    module = "fork_probe_stage"
    body = textwrap.dedent("""
        def configure(context):
            context.config("trace")
        def execute(context):
            with open(context.config("trace"), "a") as f:
                f.write("executed\\n")
            return 1
    """)
    (tmp_path / (module + ".py")).write_text(body, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    trace = tmp_path / "trace.txt"
    config = {"trace": str(trace)}
    working = tmp_path / "cache"
    working.mkdir()

    def run():
        monkeypatch.delitem(sys.modules, module, raising=False)
        importlib.invalidate_caches()
        synpp.run([{"descriptor": module}], config, working_directory=str(working), rerun_required=False)

    run()
    (tmp_path / (module + ".py")).write_text("# a new comment\n" + body + "\n# trailing comment\n", encoding="utf-8")
    run()
    assert trace.read_text().splitlines() == ["executed"]
    (tmp_path / (module + ".py")).write_text(body.replace("return 1", "return 2"), encoding="utf-8")
    run()
    assert trace.read_text().splitlines() == ["executed", "executed"]
