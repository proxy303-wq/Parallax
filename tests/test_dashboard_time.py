"""Dashboard timestamps are IST, not UTC.

The journal stores UTC.  The dashboard used to render the raw ISO string, so a
09:42 IST fill appeared as "04:11" - which on a trading screen reads as a
pre-open trade, and made it look as though nothing had entered during the
session when in fact both condors had.
"""
import importlib

web = importlib.import_module("parallax.web.app")


def test_a_utc_journal_stamp_renders_as_ist():
    assert web._ist_stamp("2026-09-29T04:11:59.591990+00:00") == "2026-09-29 09:41"


def test_the_time_only_form_is_ist_too():
    assert web._ist_stamp("2026-09-29T04:11:59.591990+00:00",
                          with_date=False) == "09:41:59"


def test_a_naive_stamp_is_read_as_utc():
    assert web._ist_stamp("2026-09-29T04:11:59") == "2026-09-29 09:41"


def test_junk_does_not_explode():
    assert web._ist_stamp(None) == ""
    assert web._ist_stamp("not a date") == "not a date"
