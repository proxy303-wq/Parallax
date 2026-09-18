"""Full PARALLAX system status — NIFTY futures + NIFTY/FINNIFTY options + crypto,
one combined Telegram feed.

Fast preview (no full replay): reads the live Dhan wallet, resolves the NIFTY
future, prices the current options recommendation (Black-Scholes fallback until
the Dhan web token arrives), reads the Delta account + BTC/XAUT quotes, and
posts one 'describe_portfolio' message to the dedicated PARALLAX bot.
"""
from __future__ import annotations

import math

from parallax.contracts import InstrumentId, InstrumentType
from parallax.adapters.broker.dhan import DhanBroker
from parallax.adapters.broker.delta import DeltaBroker
from parallax.adapters.market_data.csv_loader import load_ohlcv
from parallax.adapters.telegram import TelegramBot
from parallax.config.live import dhan_futures_risk_config
from parallax.core.options import ChainContext, OptionsConfig, OptionsEngine

NIFTY_CSV = r"C:\PrOxyTradingTerminal\data\NIFTY_5m.csv"


def _realized_vol(bars, n=100) -> float:
    """Annualised 5m realised vol from the last n closes (decimal)."""
    closes = [b.close for b in bars[-n:]]
    if len(closes) < 30:
        return 0.13
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(75 * 252)   # 5m bars -> annual


def full_status() -> list[dict]:
    rows: list[dict] = []

    # 1) Dhan / NIFTY futures
    dhan = DhanBroker(dry_run=True)
    acct = dhan.get_account()
    try:
        sid, sym, expiry, lot = dhan.resolve_contract()
        fut_note = f"{sym} (lot {lot:.0f})"
    except Exception as e:
        fut_note = f"contract: {e}"
    rows.append({
        "label": "NIFTY FUT", "mode": "PAPER",
        "equity": round(acct.equity, 0), "daily_pnl": 0, "open_positions": 0,
        "note": f"{fut_note} | wallet Rs{acct.equity:,.0f} | 3L config ready",
    })

    # 2) NIFTY options recommendation (BS fallback)
    try:
        bars = load_ohlcv(NIFTY_CSV, "5m")
        spot = bars[-1].close
        sigma = _realized_vol(bars)
        dte = 7
        eng = OptionsEngine(OptionsConfig())
        ctx = ChainContext(spot=spot, sigma=sigma, dte=dte, strike_step=50,
                           expiry="next", symbol="NIFTY", lot_size=65)
        sell = eng.sell_strangle(ctx)
        opt_note = (f"IV~{sigma:.0%} | sell {sell.name} credit Rs{sell.net_premium:,.0f} "
                    f"maxloss Rs{sell.max_loss:,.0f} p~{sell.prob_profit:.0%}")
    except Exception as e:
        opt_note = f"options preview: {e}"
    rows.append({
        "label": "NIFTY OPT", "mode": "PAPER",
        "equity": 0, "daily_pnl": 0, "open_positions": 0, "note": opt_note,
    })

    # 3) Delta / crypto
    delta = DeltaBroker(dry_run=True)
    dacct = delta.get_account()
    q = delta.get_quote("DELTA:BTC")
    rows.append({
        "label": "CRYPTO", "mode": "PAPER",
        "equity": round(dacct.equity, 2), "daily_pnl": 0, "open_positions": 0,
        "note": f"BTC {q.last:,.0f} (bid {q.bid:,.0f} / ask {q.ask:,.0f})",
    })

    return rows


def run() -> None:
    tg = TelegramBot()
    msg = tg.describe_portfolio(full_status())
    print(msg)
    if tg.configured:
        ok = tg.send(msg)
        print("telegram sent:", ok)
    else:
        print("telegram not configured (need PARALLAX_TELEGRAM_BOT_TOKEN/CHAT_ID)")


if __name__ == "__main__":
    run()
