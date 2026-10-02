"""The 4-leg screen structure: the arithmetic the probe rests on."""
from __future__ import annotations

from parallax.apps.ops.box_probe import (ATM_STRADDLE_RATIO, DOC_OFFSETS, DOC_SIGMA,
                                         FIXED_STRIKES, credit_on, expected_move,
                                         intrinsic, legs, payoff_bounds, snap)

SCREEN = {"c21550": 1181.35, "p21550": 4.30, "p21800": 8.00, "c22000": 472.50}


def screen_credit() -> float:
    return SCREEN["c21550"] + SCREEN["p21800"] - SCREEN["c22000"] - SCREEN["p21550"]


def test_screen_credit_is_712_55():
    assert abs(screen_credit() - 712.55) < 1e-9


def test_legs_are_hedge_first():
    book = legs(FIXED_STRIKES)
    assert book == [(("S"), 21550.0, "CE"), ("B", 22000.0, "CE"),
                    ("B", 21550.0, "PE"), ("S", 21800.0, "PE")]
    assert book[0][0] == "S"


def test_intrinsic_has_the_two_plateaus():
    book = legs(FIXED_STRIKES)
    c = screen_credit()
    # flat ceiling below the short put strike
    for s in (19000.0, 21000.0, 21550.0, 21800.0):
        assert abs(c + intrinsic(s, book) - 462.55) < 1e-9, s
    # flat floor above the long call strike
    for s in (22000.0, 22421.95, 24000.0, 26000.0):
        assert abs(c + intrinsic(s, book) - 262.55) < 1e-9, s
    # the ramp is exactly one rupee per point
    assert abs((c + intrinsic(21900.0, book)) - 362.55) < 1e-9


def test_ceiling_is_462_55_not_the_quoted_credit():
    """The screen's 712.55 is never reachable - the short call is always live."""
    book = legs(FIXED_STRIKES)
    b = payoff_bounds(book, screen_credit())
    assert abs(b["floor"] - 262.55) < 1e-9
    assert abs(b["ceiling"] - 462.55) < 1e-9
    assert b["ceiling"] < screen_credit()


def test_offsets_reproduce_the_screen_strikes():
    atm = 22400.0
    got = {k: atm + off * 50.0 for k, off in DOC_OFFSETS.items()}
    assert got == FIXED_STRIKES


def _rows(quote_map):
    rows = {}
    for (strike, otype), (ltp, bid, ask) in quote_map.items():
        rows[(float(strike), otype)] = {"ltp": ltp, "bid": bid, "ask": ask,
                                        "volume": 0, "oi": 0, "strike": float(strike),
                                        "option_type": otype}
    return rows


#: The 2026-10-01 close, as the venue published it.
LIVE = _rows({(21550, "CE"): (1181.35, 845.10, 1146.30),
              (21550, "PE"): (4.30, 4.05, 4.35),
              (21800, "PE"): (8.00, 7.80, 8.00),
              (22000, "CE"): (472.50, 466.30, 471.80)})


def test_three_price_bases_diverge():
    book = legs(FIXED_STRIKES)
    ltp = credit_on(LIVE, book, "ltp")
    mid = credit_on(LIVE, book, "mid")
    exe = credit_on(LIVE, book, "executable")
    assert abs(ltp - 712.55) < 1e-9
    assert abs(mid - 530.35) < 1e-9
    assert abs(exe - 376.75) < 1e-9
    assert exe < mid < ltp


def test_executable_floor_is_negative_on_real_quotes():
    """The whole point: the honest floor is a loss, not a profit."""
    book = legs(FIXED_STRIKES)
    for basis, want in (("ltp", 262.55), ("mid", 80.35), ("executable", -73.25)):
        b = payoff_bounds(book, credit_on(LIVE, book, basis))
        assert abs(b["floor"] - want) < 1e-9, basis
    assert payoff_bounds(book, credit_on(LIVE, book, "executable"))["floor"] < 0


def test_expected_move_is_the_straddle_over_0_798():
    rows = _rows({(22400, "CE"): (200.0, 199.0, 201.0), (22400, "PE"): (60.0, 59.0, 61.0)})
    sig = expected_move(rows, 22400.0)
    assert abs(sig - 260.0 / ATM_STRADDLE_RATIO) < 1e-9
    assert abs(sig - 325.86) < 0.01


def test_an_atm_straddle_really_costs_0_7979_sigma():
    """Why the probe divides by 0.7979 and not 0.85.

    A straddle IS 0.7979 sigma (2*phi(0)*sigma*sqrt(T)), so the popular
    "0.85 x straddle" shortcut lands on 0.68 sigma - near the MEDIAN move
    (0.674 sigma), not one standard deviation.  Two different quantities
    share the name "expected move", and on NIFTY 6 Oct they differ by 1.47x
    (326.5 vs 221.4 points), which moves the doc's strikes two steps.
    """
    import math

    def norm(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    spot = 22421.95
    for sigma_sqrt_t in (0.005, 0.014561, 0.03):
        v = sigma_sqrt_t
        call = spot * norm(v / 2.0) - spot * norm(-v / 2.0)
        straddle = 2.0 * call
        assert abs(straddle - 0.79788 * spot * v) / straddle < 0.01
    # and the shortcut really is the smaller number
    assert 0.85 < 1.0 / 0.79788
    assert abs(0.85 * 0.79788 - 0.678) < 0.001   # ~ the median move


def test_expected_move_is_zero_without_a_straddle():
    assert expected_move(_rows({}), 22400.0) == 0.0


def test_sigmas_reproduce_the_screen_strikes():
    """The screen was 1.3 to 2.7 sigma BELOW spot - not the money at all."""
    sig = 260.52 / ATM_STRADDLE_RATIO
    got = {k: snap(22400.0 + m * sig) for k, m in DOC_SIGMA.items()}
    assert got == FIXED_STRIKES, got
    assert all(m < 0 for m in DOC_SIGMA.values())
    assert DOC_SIGMA["call_short"] < -2.5 and DOC_SIGMA["call_long"] > -1.5


def test_the_arbitrage_needs_a_credit_above_450():
    """Floor > 0 only when the call spread is sold for more than its width."""
    book = legs(FIXED_STRIKES)
    assert payoff_bounds(book, 450.0)["floor"] == 0.0
    assert payoff_bounds(book, 450.01)["floor"] > 0
    assert payoff_bounds(book, 449.99)["floor"] < 0
