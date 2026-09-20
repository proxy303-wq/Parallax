"""Hermetic smoke test of the crypto worker for a given symbol.

Uses a THROWAWAY journal db and a stubbed Telegram bot, so it can be run any time without
touching the real journal or messaging the channel.  It does hit Delta's public candle
endpoint -- that is the point: it proves the symbol actually resolves on the venue and
that a full tick produces a state snapshot.

run from the repo root:  python <this> BTCUSD ETHUSD
"""
from __future__ import annotations

import os
import sys
import tempfile

# this script lives outside the repo, so make the package importable
sys.path.insert(0, os.environ.get("PARALLAX_ROOT", r"C:\Parallax"))

# throwaway journal store -- must be set before parallax.web.store is imported
os.environ["PARALLAX_DB"] = os.path.join(tempfile.mkdtemp(prefix="smoke_"), "j.db")

from parallax.apps.worker.crypto_smc import CryptoWorker   # noqa: E402
from parallax.web.store import JournalStore                # noqa: E402


class SilentTelegram:
    """Stands in for TelegramBot: records instead of sending."""
    configured = False

    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


def main(symbols):
    store = JournalStore()
    for sym in symbols:
        w = CryptoWorker(store=store, symbol=sym)
        w.tg = SilentTelegram()
        print("== %s ==" % sym)
        print("   state_key        :", w.state_key)
        print("   contract_value   :", w.prod.contract_value, " tick", w.prod.tick_size)
        print("   mode             :", w.mode(), "| equity Rs%.0f" % w.equity())
        try:
            decisions = w.tick()
        except Exception as e:
            print("   TICK FAILED: %s: %s" % (type(e).__name__, e))
            continue
        print("   bars fetched     :", 0 if w.bars is None else len(w.bars))
        if w.bars is not None and len(w.bars):
            print("   last bar         :", w.bars.index[-1], "close", round(float(w.bars['close'].iloc[-1]), 2))
        print("   decisions        :", len(decisions))
        for d in decisions[-4:]:
            print("      ", d.ts, d.action, "side=%d" % d.side, "@%.2f" % d.price)
        snap = w.machine.snapshot()
        print("   machine          : bias=%s bars=%s order=%s position=%s"
              % (snap["bias"], snap["bars_processed"], bool(snap["order"]), bool(snap["position"])))
        stored = store.get_setting(w.state_key, "")
        print("   published state  :", "yes (%d bytes)" % len(stored) if stored else "NO")
        # isolation: the other symbol's key must be untouched by this worker
        others = [k for k in ("crypto_state_BTCUSD", "crypto_state_ETHUSD")
                  if k != w.state_key and store.get_setting(k, "")]
        if others:
            print("   other keys intact:", others)
        print()


if __name__ == "__main__":
    main([s.upper() for s in (sys.argv[1:] or ["BTCUSD", "ETHUSD"])])
