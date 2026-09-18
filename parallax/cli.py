"""PARALLAX command-line interface."""
from __future__ import annotations

import argparse
import json
import sys

from parallax.contracts import InstrumentId, InstrumentType, Market, RiskConfig
from parallax.core.decision import DecisionConfig
from parallax.adapters.market_data import MarketDataGateway
from parallax.apps.research import BacktestEngine, walk_forward
from parallax.apps.worker import replay_bars
from parallax.adapters.telegram import TelegramBot


def _instrument(name: str) -> InstrumentId:
    sym = name.upper()
    kind = InstrumentType.CRYPTO_PERP if sym in ("BTC", "ETH", "SOL", "XRP", "XAUT")         else InstrumentType.INDEX_FUTURE
    return InstrumentId(sym, kind, exchange="DELTA" if kind == InstrumentType.CRYPTO_PERP else "NSE")


def _risk(capital: float) -> RiskConfig:
    return RiskConfig(capital=capital)


def cmd_run(args) -> None:
    gateway = MarketDataGateway()
    inst = _instrument(args.instrument)
    bars = gateway.load_csv(inst.symbol, args.timeframe, args.csv)
    print(f"loaded {len(bars)} bars for {inst}")
    tg = TelegramBot(args.telegram_token, args.telegram_chat) if args.telegram_token else None
    ex = replay_bars(inst, bars, args.timeframe, _risk(args.capital),
                     DecisionConfig(), telegram=tg)
    print(json.dumps(ex.summary(), indent=2))


def cmd_backtest(args) -> None:
    inst = _instrument(args.instrument)
    bars = MarketDataGateway().load_csv(inst.symbol, args.timeframe, args.csv)
    if args.max_bars:
        bars = bars[-args.max_bars:]
    eng = BacktestEngine(inst, _risk(args.capital),
                         fee_rate=args.fee, slippage=args.slippage,
                         warmup=args.warmup)
    result = eng.run(bars, args.timeframe)
    print(f"instrument={inst} bars={len(bars)} timeframe={args.timeframe}")
    print(f"trades={result.metrics.get('trades')} "
          f"win_rate={result.metrics.get('win_rate')} "
          f"PF={result.metrics.get('profit_factor')} "
          f"pnl={result.metrics.get('total_pnl')} "
          f"return%={result.metrics.get('return_pct')} "
          f"maxDD%={result.metrics.get('max_drawdown_pct')}")
    print(f"final_equity={round(result.final_equity, 2)}")


def cmd_walkforward(args) -> None:
    inst = _instrument(args.instrument)
    bars = MarketDataGateway().load_csv(inst.symbol, args.timeframe, args.csv)
    if args.max_bars:
        bars = bars[-args.max_bars:]
    windows = walk_forward(inst, bars, args.timeframe, args.train, args.test,
                           _risk(args.capital))
    for w in windows:
        m = w["metrics"]
        print(f"window {w['window']:2d} [{w['start'][:10]}..{w['end'][:10]}] "
              f"trades={m['trades']} win={m['win_rate']} pnl={m['total_pnl']}")


def cmd_describe(args) -> None:
    inst = _instrument(args.instrument)
    bars = MarketDataGateway().load_csv(inst.symbol, args.timeframe, args.csv)
    from parallax.core.perception import build_market_state
    from parallax.core.context import build_context
    ms = build_market_state(inst, bars, args.timeframe, lookback=600)
    state = build_context({args.timeframe: ms}, args.timeframe)
    print(TelegramBot().describe_market(state))


def cmd_diagnose(args) -> None:
    """Run a backtest and print the measured 'why' (excursion + exit attribution)."""
    from parallax.apps.research import print_report
    from parallax.core.execution import ExitConfig
    inst = _instrument(args.instrument)
    bars = MarketDataGateway().load_csv(inst.symbol, args.timeframe, args.csv)
    if args.max_bars:
        bars = bars[-args.max_bars:]
    cfg = ExitConfig(lock_r=args.lock_r, trail_r=args.trail_r, target_r=args.target_r)
    eng = BacktestEngine(inst, exit_config=cfg, warmup=args.warmup)
    result = eng.run(bars, args.timeframe)
    print("instrument=" + str(inst) + " bars=" + str(len(bars))
          + " timeframe=" + args.timeframe)
    print_report(result)


def cmd_brain(args) -> None:
    """Ask the DeepSeek brain to reason over the latest market state."""
    from parallax.core.perception import build_market_state
    from parallax.core.context import build_context
    from parallax.core.hypotheses import HypothesisEngine
    from parallax.core.brain import Brain, TypeSafeSystemOne
    from parallax.core.brain.brain import build_state_context, combine_assessments
    inst = _instrument(args.instrument)
    bars = MarketDataGateway().load_csv(inst.symbol, args.timeframe, args.csv)
    ms = build_market_state(inst, bars, args.timeframe, lookback=600)
    state = build_context({args.timeframe: ms}, args.timeframe)
    hyps = HypothesisEngine().generate(state)
    brain = Brain()
    typesafe = TypeSafeSystemOne()
    print("providers: " + json.dumps(brain.providers()
                                     + ([typesafe.masked()] if typesafe.enabled else [])))
    assessments = [brain.think(state, hyps)]
    if typesafe.enabled:
        first = assessments[0]
        d = first.direction if first.direction in ("long", "short") else "flat"
        t = typesafe.judge(build_state_context(state, hyps, None),
                           proposed_direction=d if d != "flat" else "long")
        if t is not None:
            assessments.append(t)
    a = combine_assessments(assessments) if len(assessments) > 1 else assessments[0]
    print()
    print("=== BRAIN ASSESSMENT ===")
    print("direction   : " + a.direction)
    print("confidence  : " + str(a.confidence))
    print("verdict     : " + a.verdict + (" (LLM)" if a.llm_used else " (deterministic fallback)"))
    print("thesis      : " + a.thesis)
    print("counter     : " + a.counter_argument)
    print("uncertainty : " + a.uncertainty)
    if a.reasons:
        print("reasons     : " + "; ".join(a.reasons))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="parallax",
                                description="PARALLAX autonomous trading system")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--csv", required=True)
    common.add_argument("--instrument", default="NIFTY")
    common.add_argument("--timeframe", default="5m")
    common.add_argument("--capital", type=float, default=500_000.0)
    common.add_argument("--max-bars", type=int, default=None)

    r = sub.add_parser("run", parents=[common])
    r.add_argument("--telegram-token", default=None)
    r.add_argument("--telegram-chat", default=None)
    r.set_defaults(func=cmd_run)

    b = sub.add_parser("backtest", parents=[common])
    b.add_argument("--fee", type=float, default=0.0002)
    b.add_argument("--slippage", type=float, default=0.0002)
    b.add_argument("--warmup", type=int, default=100)
    b.set_defaults(func=cmd_backtest)

    wf = sub.add_parser("walkforward", parents=[common])
    wf.add_argument("--train", type=int, default=500)
    wf.add_argument("--test", type=int, default=300)
    wf.set_defaults(func=cmd_walkforward)

    d = sub.add_parser("describe", parents=[common])
    d.set_defaults(func=cmd_describe)

    diag = sub.add_parser("diagnose", parents=[common])
    diag.add_argument("--lock-r", type=float, default=0.5)
    diag.add_argument("--trail-r", type=float, default=0.5)
    diag.add_argument("--target-r", type=float, default=2.0)
    diag.add_argument("--warmup", type=int, default=100)
    diag.set_defaults(func=cmd_diagnose)

    b = sub.add_parser("brain", parents=[common])
    b.set_defaults(func=cmd_brain)

    args = p.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
