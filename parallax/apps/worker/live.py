"""PARALLAX live paper loop — real Delta Exchange data, paper fills, Telegram.

For each symbol it fetches live candles, runs the full executive (perception ->
context -> hypotheses -> decision -> risk -> paper execution), and posts
TRADE / EXIT / ABORT decisions plus a periodic market read to Telegram.

Paper mode only: the order goes to the PaperBroker, never to the exchange.
"""
from __future__ import annotations

import time

from parallax.contracts import (
    DecisionClass, ExecutionMode, InstrumentId, InstrumentType, RiskConfig,
    spec_for,
)
from parallax.core.decision import DecisionConfig
from parallax.executive import ParallaxExecutive
from parallax.adapters.env import env, mask
from parallax.adapters.market_data.delta_feed import DeltaFeed
from parallax.adapters.telegram import TelegramBot


def delta_symbol(px_symbol: str) -> str:
    return px_symbol + "USD"   # BTC -> BTCUSD, XAUT -> XAUTUSD, ETH -> ETHUSD


def build_executive(px_symbol: str, resolution: str) -> ParallaxExecutive:
    spec = spec_for(px_symbol)
    rc = RiskConfig(
        capital=spec.default_capital, point_value=spec.point_value,
        min_step=spec.min_step, fee_rate=spec.fee_rate, slippage=spec.slippage,
        data_staleness_tolerance_seconds=3600.0)
    dc = DecisionConfig(atr_stop_mult=spec.stop_mult)
    inst = InstrumentId(px_symbol, InstrumentType.CRYPTO_PERP, exchange="DELTA")
    return ParallaxExecutive(inst, risk_config=rc, decision_config=dc,
                             mode=ExecutionMode.PAPER, timeframe=resolution)


def run(symbols=("BTC", "XAUT"), resolution: str = "5m",
        interval_seconds: int = 60, lookback: int = 600,
        one_shot: bool = False) -> dict:
    feed = DeltaFeed()
    telegram = TelegramBot()
    execs = {s: build_executive(s, resolution) for s in symbols}

    line = ("PARALLAX paper-live ONLINE  "
            + ", ".join(delta_symbol(s) for s in symbols)
            + " @" + resolution + "  bot=" + mask(telegram.token))
    print(line)
    if telegram.configured:
        telegram.send(line)

    tick = 0
    while True:
        for s in symbols:
            ex = execs[s]
            dsym = delta_symbol(s)
            try:
                bars = feed.candles(dsym, resolution, limit=lookback + 50)
                if len(bars) < 200:
                    continue
                result = ex.evaluate(bars, resolution)
                if result is None:
                    continue
                acct = ex.broker.get_account()
                posn = ex.broker.get_positions()
                pnl = ("[" + dsym + "] PNL equity=" + str(round(acct.equity, 2))
                       + " realized=" + str(round(acct.realized_pnl, 2))
                       + " unrealized=" + str(round(acct.unrealized_pnl, 2))
                       + " open_pos=" + str(len(posn)))
                print(pnl)
                dc = result.decision.decision_class
                if dc == DecisionClass.TRADE:
                    prefix = "ENTRY " if result.executed else "SIGNAL(veto) "
                    msg = "[" + dsym + "] " + prefix + telegram.describe_decision(result.decision)
                    print(msg)
                    if telegram.configured:
                        telegram.send(msg)
                elif dc in (DecisionClass.EXIT, DecisionClass.ABORT):
                    msg = "[" + dsym + "] " + telegram.describe_decision(result.decision)
                    print(msg)
                    if telegram.configured:
                        telegram.send(msg)
                read = "[" + dsym + "] " + telegram.describe_market(result.state)
                print(read)
                if telegram.configured:
                    telegram.send(read)
            except Exception as e:
                print("[" + dsym + "] error: " + type(e).__name__ + " " + str(e)[:120])
        tick += 1
        if one_shot:
            break
        time.sleep(interval_seconds)
    return execs


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTC,XAUT")
    p.add_argument("--resolution", default="5m")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    run(symbols=tuple(a.symbols.split(",")), resolution=a.resolution,
        interval_seconds=a.interval, one_shot=a.once)
