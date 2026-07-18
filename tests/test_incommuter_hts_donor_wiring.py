"""Test that both in-commuter stages stage the MiD donor (Task 2)."""
from braunschweig.synthesis import incommuters, student_incommuters


class _Rec:
    """Recording context stub: captures stage() declarations and returns harmless
    config values so configure() runs without KeyError."""
    def __init__(self):
        self.staged = []

    def config(self, k, d=None):
        # cordon_enabled must return True so both configure()s don't exit early.
        if k == "cordon_enabled":
            return True
        return d if d is not None else 0

    def stage(self, descriptor, **kw):
        self.staged.append((descriptor, kw))


def test_svb_incommuters_use_mid_donor():
    """SvB (data.cordon.demand) in-commuter stage must stage the MiD donor."""
    ctx = _Rec()
    incommuters.configure(ctx)
    assert ("braunschweig.data.hts.mid_donor", {"alias": "hts"}) in ctx.staged
    assert ("data.hts.selected", {"alias": "hts"}) not in ctx.staged


def test_student_incommuters_use_mid_donor():
    """Student in-commuter stage must stage the MiD donor."""
    ctx = _Rec()
    student_incommuters.configure(ctx)
    assert ("braunschweig.data.hts.mid_donor", {"alias": "hts"}) in ctx.staged
    assert ("data.hts.selected", {"alias": "hts"}) not in ctx.staged
