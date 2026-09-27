"""The morning report's shape, without touching the network."""
import datetime

from parallax.apps.ops.morning_report import build_report

D = datetime.date(2026, 9, 29)          # a last-Tuesday: BANKNIFTY, uncalibrated


def _bands(step):
    return {"calibrated": True, "step": step,
            "range_bands": {10: 118, 50: 237, 90: 547},
            "move_bands": {10: 14, 50: 108, 90: 338}}


def _fake(sym):
    if sym == "BANKNIFTY":      # no table, but it IS what trades today
        return {"spot": 55580.4, "straddle": 555.9, "step": 100.0,
                "calibrated": False}
    if sym == "BANKEX":
        return None             # chain down
    return dict({"spot": 23140.5, "straddle": 338.1, "dte": 7}, **_bands(50.0))


LIVE = {"BANKNIFTY": {"lots": 8, "short_off": 5}, "NIFTY": {"lots": 7, "short_off": 4}}


def test_it_names_the_index_that_trades():
    txt = build_report(today=D, pause=0, forecast_fn=_fake, live=LIVE)
    assert "BANKNIFTY 0DTE" in txt
    assert "enter 09:30" in txt and "settle 15:30" in txt
    assert "trades today" in txt


def test_an_uncalibrated_index_still_gets_the_straddle_and_its_book():
    """Tuesday's trade is BANKNIFTY and it has no table -- it must not go blank."""
    txt = build_report(today=D, pause=0, forecast_fn=_fake, live=LIVE)
    line = [l for l in txt.splitlines() if l.startswith("BANKNIFTY")][0]
    assert "556" in line and "no table" in line
    book = [l for l in txt.splitlines() if "book" in l][0]
    assert "8 lots" in book and "500 pts" in book and "straddle 556" in book


def test_it_reports_bands_not_a_win_rate():
    """The win-rate claim failed walk-forward, so it must not appear here."""
    txt = build_report(today=D, pause=0, forecast_fn=_fake, live=LIVE)
    assert "80%" in txt
    assert "profit" not in txt.lower()
    assert "probab" not in txt.lower()


def test_it_handles_a_dead_chain():
    txt = build_report(today=D, pause=0, forecast_fn=_fake, live=LIVE)
    dead = [l for l in txt.splitlines() if l.startswith("BANKEX")][0]
    assert "no chain available" in dead


def test_the_book_line_is_omitted_when_the_units_are_unreadable():
    txt = build_report(today=D, pause=0, forecast_fn=_fake, live={})
    assert "book" not in txt
    assert "trades today" in txt          # the marker survives


def test_only_the_trading_index_gets_a_book_line():
    txt = build_report(today=D, pause=0, forecast_fn=_fake, live=LIVE)
    assert txt.count("book") == 1         # NIFTY is configured but not trading


def test_a_futures_day_says_so():
    txt = build_report(today=datetime.date(2026, 9, 30), pause=0,
                       forecast_fn=_fake, live=LIVE)
    assert "no options" in txt
    assert "trades today" not in txt


def test_it_never_raises_on_a_broken_forecast():
    def boom(sym):
        raise RuntimeError("dhan 810")
    txt = build_report(today=D, pause=0, forecast_fn=boom)
    assert "chain error" in txt
