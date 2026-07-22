"""Guard the synpp per-stage config contract (issue #229) for the matsim.runtime
build stages: a stage's execute() may only read config options the SAME stage's
configure() declared. pt2matsim.execute reads git/maven/java helper options via
git.run/maven.run/java.run; eqasim.execute reads git/maven helper options. A
missing declaration crashes real runs ("Config option X is not requested") as
soon as the stage cache is devalidated -- see #222/#223 for the same class.
"""
import matsim.runtime.eqasim as eqasim
import matsim.runtime.pt2matsim as pt2matsim


class _RecordingContext:
    """Minimal configure()-time context: records declared config options."""

    def __init__(self):
        self.declared = set()

    def stage(self, name, *args, **kwargs):
        pass

    def config(self, name, *args, **kwargs):
        self.declared.add(name)


def test_pt2matsim_declares_all_helper_binary_options():
    ctx = _RecordingContext()
    pt2matsim.configure(ctx)
    required = {"git_binary",                                   # git.run
                "maven_binary", "maven_skip_tests", "java_home",  # maven.run
                "java_binary", "java_memory"}                   # java.run
    assert required <= ctx.declared, (
        f"pt2matsim.configure misses declares: {sorted(required - ctx.declared)}")


def test_eqasim_declares_git_and_maven_helper_options():
    ctx = _RecordingContext()
    eqasim.configure(ctx)
    required = {"git_binary", "maven_binary", "maven_skip_tests", "java_home"}
    assert required <= ctx.declared, (
        f"eqasim.configure misses declares: {sorted(required - ctx.declared)}")
