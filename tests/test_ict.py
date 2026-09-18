"""ICT/SMC engine tests — deterministic signal structure."""
from parallax.contracts import InstrumentId, InstrumentType
from parallax.core.ict import ICTConfig, ICTSignal, detect

from conftest import make_bars, make_state


def test_ict_signal_contract():
    s = ICTSignal(direction="long", bias="bullish", sweep_level=100.0,
                  sweep_extreme=99.0, fvg_top=101.0, fvg_bottom=100.0,
                  entry=100.5, stop=99.0, target=103.0)
    assert s.reward_risk > 1.0
    d = s.as_dict()
    assert d["direction"] == "long" and d["rr"] > 1.0


def test_ict_detect_runs_without_crash():
    state = make_state(inst=InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE),
                       bars=make_bars(seed=3, drift=2.5))
    bars = state.timeframes["5m"].bars
    sig = detect(state, bars, ICTConfig())
    # either None or a valid signal (long/short, rr >= min_rr)
    if sig is not None:
        assert sig.direction in ("long", "short")
        assert sig.reward_risk >= 1.5


def test_ict_config_defaults():
    c = ICTConfig()
    assert c.min_rr == 1.5
    assert 0 < c.ote_low < c.ote_high < 1.0
