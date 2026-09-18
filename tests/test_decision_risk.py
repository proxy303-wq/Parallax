"""Decision-engine and risk-gate tests."""
from datetime import datetime, timezone

from parallax.contracts import (
    Action, DecisionClass, InstrumentId, InstrumentType, RiskConfig,
    RiskStatus, TradeDecision,
)
from parallax.core.decision import DecisionConfig, DecisionEngine
from parallax.core.hypotheses import HypothesisEngine
from parallax.core.risk import RiskContext, RiskEngine

from conftest import make_bars, make_state

BTC = InstrumentId("BTC", InstrumentType.CRYPTO_PERP)
NIFTY = InstrumentId("NIFTY", InstrumentType.INDEX_FUTURE)


def _trade(inst=BTC, drift=2.5, atr_mult=4.0):
    state = make_state(inst=inst, bars=make_bars(seed=3, drift=drift))
    hyps = HypothesisEngine().generate(state)
    dec = DecisionEngine(DecisionConfig(atr_stop_mult=atr_mult)).decide(state, hyps)
    return dec, state


def test_flat_market_waits():
    state = make_state(bars=make_bars(seed=9, drift=0.0))
    hyps = HypothesisEngine().generate(state)
    dec = DecisionEngine().decide(state, hyps)
    assert dec.decision_class == DecisionClass.WAIT


def test_strong_trend_trades_crypto():
    dec, _ = _trade(inst=BTC)
    assert dec.decision_class == DecisionClass.TRADE
    assert dec.action in (Action.BUY, Action.SELL)
    assert dec.risk_plan.stop_loss is not None
    assert dec.risk_plan.reward_risk >= 2.0


def test_nifty_requires_opening_sweep():
    # a monotonic drift never sweeps the opening range -> NIFTY stays out
    dec, _ = _trade(inst=NIFTY)
    assert dec.decision_class == DecisionClass.WAIT
    assert any("opening" in r or "confirmation" in r for r in dec.reasons)


def test_kill_switch_aborts():
    state = make_state(inst=BTC, bars=make_bars(seed=3, drift=2.5))
    hyps = HypothesisEngine().generate(state)
    dec = DecisionEngine().decide(state, hyps, kill_switch=True)
    assert dec.decision_class == DecisionClass.ABORT


def test_risk_rejects_non_trade():
    state = make_state()
    dec = TradeDecision(decision_id="d", timestamp=datetime.now(timezone.utc),
                        instrument="NIFTY", action=Action.WAIT,
                        decision_class=DecisionClass.WAIT)
    v = RiskEngine().evaluate(dec, state, RiskContext(equity=500000.0))
    assert v.status == RiskStatus.REJECTED
    assert any("not TRADE" in r for r in v.reasons)


def test_risk_kill_switch_vetoes():
    dec, state = _trade(inst=BTC)
    v = RiskEngine(RiskConfig(kill_switch=True)).evaluate(
        dec, state, RiskContext(equity=500000.0))
    assert v.status == RiskStatus.REJECTED
    assert any("kill" in r for r in v.reasons)


def test_risk_daily_loss_vetoes():
    dec, state = _trade(inst=BTC)
    v = RiskEngine().evaluate(dec, state, RiskContext(equity=500000.0,
                                                      daily_pnl=-10000.0))
    assert v.status == RiskStatus.REJECTED
    assert any("daily" in r for r in v.reasons)


def test_risk_approves_fractional_crypto_sizing():
    dec, state = _trade(inst=BTC)
    cfg = RiskConfig(capital=10_000.0, point_value=1.0, min_step=0.001)
    v = RiskEngine(cfg).evaluate(dec, state, RiskContext(equity=10_000.0))
    assert v.status == RiskStatus.APPROVED
    assert v.position_size > 0
    # fractional: a multiple of the 0.001 min step, not necessarily an integer
    assert abs(v.position_size / cfg.min_step - round(v.position_size / cfg.min_step)) < 1e-6


def test_risk_rejects_tight_stop_vs_cost():
    # tight ATRx2 stop + crypto taker costs -> cost exceeds the risk budget
    dec, state = _trade(inst=BTC, atr_mult=2.0)
    cfg = RiskConfig(capital=10_000.0, point_value=1.0, min_step=0.001,
                     fee_rate=0.0005, slippage=0.0005)
    v = RiskEngine(cfg).evaluate(dec, state, RiskContext(equity=10_000.0))
    assert v.status == RiskStatus.REJECTED
    assert any("too tight" in r for r in v.reasons)
