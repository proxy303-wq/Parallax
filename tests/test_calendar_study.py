"""The calendar harness, checked against a hand-computed scenario.

Waiting 75 minutes on a fetch only to find an accounting bug would be silly.
Everything here is synthetic and the arithmetic is done by hand.
"""
import datetime

from parallax.apps.research.calendar_study import run_calendar, _intrinsic
from parallax.config.indices import spec

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
STEP = 50.0
FRI = datetime.date(2026, 9, 11)     # entry, 2 trading days before expiry
MON = datetime.date(2026, 9, 14)
TUE = datetime.date(2026, 9, 15)     # NIFTY weekly expiry
SPOT = 23000.0
ATM = 23000.0


def ts(day, hh, mm):
    return int(datetime.datetime(day.year, day.month, day.day, hh, mm,
                                 tzinfo=IST).timestamp())


def bars(day, near_put_at, next_put_at):
    """Five bars for one day.  near/next are {offset_pts: price} for PUT."""
    out = []
    for i, (hh, mm) in enumerate(((9, 15), (9, 20), (9, 25), (12, 0), (15, 25))):
        out.append((ts(day, hh, mm), near_put_at[i], next_put_at[i]))
    return out


# near-expiry put 2 strikes out (22500... no: ATM-100 = 22900) decays 50 -> 0
near_days = {
    FRI: bars(FRI, [50.0]*5, [50.0]*5),
    MON: bars(MON, [30.0]*5, [45.0]*5),
    TUE: bars(TUE, [10.0, 10.0, 10.0, 5.0, 0.0], [40.0, 40.0, 40.0, 35.0, 30.0]),
}
next_days = {
    FRI: bars(FRI, [50.0]*5, [50.0]*5),
    MON: bars(MON, [30.0]*5, [45.0]*5),
    TUE: bars(TUE, [10.0, 10.0, 10.0, 5.0, 0.0], [40.0, 40.0, 40.0, 35.0, 30.0]),
}


def build(days, put_offsets):
    byts = {}
    for day, rows in days.items():
        for tv, p_short, p_long in rows:
            row = {"spot": SPOT, "CALL": {}, "PUT": {}}
            for off in put_offsets:
                # the near put at -100 is the one that decays; others flat
                row["PUT"][off] = (p_short if off == -100 else p_long, 0.0, 0.0)
            byts[tv] = row
    return byts


offs = [-100] + [-50 * b for b in range(3, 11)]
near = build(near_days, offs)
nxt = build(next_days, offs)
idx = spec("NIFTY")


def test_it_matches_hand_arithmetic():
    rows, skipped, unmatched = run_calendar(idx, near, nxt, side="PUT", short_off=2,
                                            entry_days_before=2, entry_bar=1, lots=7)
    assert len(rows) == 1, (len(rows), skipped, unmatched)
    r = rows[0]
    # sold 22900 PE (ATM-100) at 50; bought the next-expiry strike whose premium
    # is closest to 50 -> every further strike is quoted 50 on the entry day
    assert r["short_strike"] == 22900.0
    assert r["credit"] == 0.0, r["credit"]        # 50 sold - 50 bought
    assert r["long_off"] == 3, r["long_off"]      # first candidate beats ties
    # expires at 23000: the 22900 put is worthless, the long leg is sold at 30
    assert r["pnl"] == 30.0 * idx.lot * 7, r["pnl"]


def test_a_move_against_the_calendar_can_still_pay():
    """Katwal's whole point: the near leg dies, the far leg keeps its value.
    Here the index falls 100 points - the near put is ITM at expiry, the far
    leg is still worth something, and the net is what we are measuring."""
    assert _intrinsic(22900.0, 22800.0, "PUT") == 100.0
    assert _intrinsic(22900.0, 23000.0, "PUT") == 0.0
    assert _intrinsic(23100.0, 23000.0, "CALL") == 0.0


def test_it_skips_when_the_entry_day_is_missing():
    """entry_days_before=5 with only 3 days of bars must skip, not crash."""
    rows, skipped, unmatched = run_calendar(idx, near, nxt, side="PUT", short_off=2,
                                            entry_days_before=5, entry_bar=1, lots=7)
    assert rows == [] and skipped == 1


def build_calls(days):
    """Same prices, but a CALL calendar: the short sits ABOVE the ATM at +100
    and the further-strike candidates run upward."""
    byts = {}
    for day, rows in days.items():
        for tv, p_short, p_long in rows:
            row = {"spot": SPOT, "CALL": {}, "PUT": {}}
            for b in range(3, 11):
                row["CALL"][50 * b] = (p_long, 0.0, 0.0)
            row["CALL"][100] = (p_short, 0.0, 0.0)
            byts[tv] = row
    return byts


def test_a_call_calendar_mirrors_the_put_one():
    rows, skipped, unmatched = run_calendar(
        idx, build_calls(near_days), build_calls(next_days), side="CALL",
        short_off=2, entry_days_before=2, entry_bar=1, lots=7)
    assert len(rows) == 1, (skipped, unmatched)
    assert rows[0]["short_strike"] == 23100.0
    assert rows[0]["credit"] == 0.0
    assert rows[0]["pnl"] == 30.0 * idx.lot * 7
