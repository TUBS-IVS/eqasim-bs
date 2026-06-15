import logging

from braunschweig import logging_setup as ls


def test_want_color_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert ls._want_color("auto") is False
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert ls._want_color(True) is True
    assert ls._want_color(False) is False


def test_color_formatter_plain_and_coloured():
    rec = logging.LogRecord("braunschweig.popsim.stage", logging.INFO, __file__, 1,
                            "hello", None, None)
    plain = ls.ColorFormatter(color=False).format(rec)
    assert "│ INFO" in plain and "popsim.stage" in plain and "\x1b[" not in plain
    coloured = ls.ColorFormatter(color=True).format(rec)
    assert "\x1b[" in coloured and "hello" in coloured


def test_setup_logging_idempotent_and_writes_utf8_file(tmp_path):
    ls.setup_logging(level="INFO", color=False, log_dir=str(tmp_path), log_file="t.log")
    ls.setup_logging(level="INFO", color=False, log_dir=str(tmp_path), log_file="t.log")
    root = logging.getLogger()
    streams = [h for h in root.handlers if isinstance(h, logging.StreamHandler)
               and not isinstance(h, logging.FileHandler)]
    files = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert len(files) == 1 and len(streams) == 1  # no duplicate handlers on 2nd call
    logging.getLogger("braunschweig.x").info("ümlaut ✓")
    files[0].flush()
    text = (tmp_path / "t.log").read_text(encoding="utf-8")
    assert "ümlaut ✓" in text and "\x1b[" not in text  # file is plain UTF-8
    assert " INFO braunschweig.x " in text  # runtime-parser ISO format
