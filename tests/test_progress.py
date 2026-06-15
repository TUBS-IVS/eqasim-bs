from braunschweig import progress as P


def test_format_duration():
    assert P.format_duration(65) == "1:05"
    assert P.format_duration(3725) == "1:02:05"
    assert P.format_duration(0) == "0:00"


def test_progress_iter_yields_unchanged_and_prints(monkeypatch, capsys):
    monkeypatch.setattr(P, "_stdout_is_tty", lambda: False)  # force plain branch
    out = list(P.progress_iter(range(5), "demo", total=5, min_interval=0.0))
    assert out == [0, 1, 2, 3, 4]
    printed = capsys.readouterr().out
    assert "[demo]" in printed and "100%" in printed and "(5/5)" in printed


def test_progress_iter_without_total(monkeypatch, capsys):
    monkeypatch.setattr(P, "_stdout_is_tty", lambda: False)
    out = list(P.progress_iter(iter([10, 20]), "noseq", min_interval=0.0))
    assert out == [10, 20]
    assert "2 items" in capsys.readouterr().out
