"""PARALLAX dashboard - FastAPI web UI (home / futures / options / crypto).

Reads the shared SQLite journal store; the PAPER/LIVE toggle is stored there
too so the same switch drives the web dashboard, the Telegram bot and the
workers.  Run with:  uvicorn parallax.web.app:app
"""
from __future__ import annotations

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from parallax.web.store import JournalStore

app = FastAPI(title="PARALLAX", docs_url=None, redoc_url=None)
store = JournalStore()

CSS = """
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:24px}
h1{font-size:20px;margin:0}.hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.nav{display:flex;gap:8px;margin-bottom:20px}
.nav a{color:#58a6ff;text-decoration:none;padding:8px 14px;border:1px solid #30363d;border-radius:6px}
.nav a.active{background:#1f6feb;color:#fff;border-color:#1f6feb}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:16px}
.grid{display:flex;gap:16px;flex-wrap:wrap}.stat{flex:1;min-width:150px}
.stat .k{color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.stat .v{font-size:28px;font-weight:600}.pos{color:#3fb950}.neg{color:#f85149}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #21262d}
th{color:#8b949e;font-weight:500}.tag{padding:2px 8px;border-radius:10px;font-size:11px}
.fut{background:#1f6feb22;color:#58a6ff}.opt{background:#8957e522;color:#bc8cff}
.cry{background:#d2992222;color:#d29922}
.mode{padding:4px 12px;border-radius:12px;font-size:12px;font-weight:700;letter-spacing:1px}
.mode.paper{background:#d2992222;color:#d29922}
.mode.demo{background:#1f6feb22;color:#58a6ff}
.mode.live{background:#f8514922;color:#f85149}
.btn{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 14px;cursor:pointer;font-size:12px}
.btn:hover{border-color:#58a6ff;color:#58a6ff}
"""


def _fmt(x, dec=0):
    return f"{x:,.{dec}f}" if isinstance(x, (int, float)) else str(x)


def _card(title, inner, sub=""):
    h = f"<h3>{title}</h3>"
    if sub:
        h += f"<p style='color:#8b949e;margin:4px 0'>{sub}</p>"
    return "<div class='card'>" + h + inner + "</div>"


def _stat(k, v, cls=""):
    return f"<div class='stat'><div class='k'>{k}</div><div class='v {cls}'>{v}</div></div>"


def _page(title, active, body):
    nav = "".join(
        f'<a href="{h}" class="{"active" if a == active else ""}">{t}</a>'
        for t, h, a in (("Home", "/", "home"), ("Futures", "/futures", "futures"),
                        ("Options", "/options", "options"),
                        ("Crypto", "/crypto", "crypto")))
    mode = store.mode()
    nxt = store.next_mode()
    toggle = ("<form method='post' action='/mode' style='margin:0'>"
              f"<input type='hidden' name='mode' value='{nxt}'>"
              f"<button class='btn'>Switch to {nxt.upper()}</button></form>")
    warn = ""
    if mode == "live":
        warn = ("<div class='card' style='border-color:#f85149'>"
                "<b style='color:#f85149'>LIVE MODE</b> - orders are sent to the real "
                "venue with real money.</div>")
    elif mode == "demo":
        warn = ("<div class='card' style='border-color:#1f6feb'>"
                "<b style='color:#58a6ff'>DEMO MODE</b> - orders go to the Delta "
                "testnet (fake money, real order flow).</div>")
    head = ("<div class='hdr'><h1>PARALLAX</h1>"
            "<div style='display:flex;gap:10px;align-items:center'>"
            f"<span class='mode {mode}'>{mode.upper()}</span>{toggle}</div></div>"
            f"<div class='nav'>{nav}</div>")
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title} - PARALLAX</title><style>{CSS}</style></head><body>"
            + head + warn + body + "</body></html>")


def _trade_rows(trades):
    if not trades:
        return "<p style='color:#8b949e'>No trades recorded yet.</p>"
    head = ("<tr><th>Time</th><th>Strategy</th><th>Instrument</th><th>Side</th>"
            "<th>Qty</th><th>Entry to Exit</th><th>P&amp;L</th><th>Outcome</th></tr>")
    rows = []
    for t in trades:
        cls = "fut" if t["strategy"] == "futures" else ("opt" if t["strategy"] == "options" else "cry")
        pcls = "pos" if t["pnl"] > 0 else "neg"
        rows.append(
            "<tr>"
            f"<td>{t['ts'][:16].replace('T', ' ')}</td>"
            f"<td><span class='tag {cls}'>{t['strategy']}</span></td>"
            f"<td>{t['instrument']}</td><td>{t['side']}</td><td>{t['qty']:.0f}</td>"
            f"<td>{t['entry']:.0f} to {t['exit']:.0f}</td>"
            f"<td class='{pcls}'>{t['pnl']:+,.0f}</td>"
            f"<td>{t['outcome']}</td></tr>")
    return "<table>" + head + "".join(rows) + "</table>"


def _summary_cards(cap, summ):
    total = summ["total_pnl"]
    cls = "pos" if total >= 0 else "neg"
    grid = ("<div class='grid'>"
            + _stat("Equity", "Rs" + _fmt(cap["equity"]))
            + _stat("Available", "Rs" + _fmt(cap["available"]))
            + _stat("Margin Used", "Rs" + _fmt(cap["margin_used"]))
            + _stat("Total P&L", "Rs{:+,.0f}".format(total), cls)
            + _stat("Trades", str(summ["n_trades"]))
            + "</div>")
    out = _card("Account", grid)
    for s, d in summ["by_strategy"].items():
        wc = "pos" if d["pnl"] >= 0 else "neg"
        g = ("<div class='grid'>"
             + _stat("P&L", "Rs{:+,.0f}".format(d["pnl"]), wc)
             + _stat("Win rate", "{:.0%}".format(d["win_rate"]))
             + _stat("Trades", str(d["n"]))
             + "</div>")
        out += _card(s.title(), g)
    return out


def _strategy_page(strategy, title, sub):
    trades = store.trades(strategy=strategy, limit=100)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    pnl = sum(t["pnl"] for t in trades)
    cls = "pos" if pnl >= 0 else "neg"
    wr = (wins / len(trades)) if trades else 0.0
    body = _card(title,
                 "<div class='grid'>"
                 + _stat("P&L", "Rs{:+,.0f}".format(pnl), cls)
                 + _stat("Win rate", "{:.0%}".format(wr))
                 + _stat("Trades", str(len(trades)))
                 + "</div>", sub)
    body += _card("Journal", _trade_rows(trades))
    return body


@app.get("/", response_class=HTMLResponse)
def home():
    return _page("Home", "home",
                 _summary_cards(store.capital(), store.summary())
                 + _card("Trade Journal (recent)", _trade_rows(store.trades(limit=15))))


@app.get("/futures", response_class=HTMLResponse)
def futures():
    return _page("Futures", "futures", _strategy_page(
        "futures", "NIFTY Futures - ICT scalper", "3 lots, intraday, sweep to OTE"))


@app.get("/options", response_class=HTMLResponse)
def options():
    return _page("Options", "options", _strategy_page(
        "options", "NIFTY 0DTE - hedged short strangle (selling)",
        "8 lots, intraday, TP 50% / SL 2x, IV>RV filter"))


# Every symbol gets its own card.  Each worker publishes its own state key, so adding a
# symbol here is all the dashboard needs to show a second book.
CRYPTO_SYMBOLS = ("BTCUSD", "ETHUSD", "XAUTUSD")


def _crypto_state(symbol: str):
    """State published by parallax.apps.worker.crypto_smc for ONE symbol.

    Falls back to the legacy un-scoped 'crypto_state' key so a worker still running older
    code renders on the BTC card instead of disappearing.
    """
    import json as _json
    raw = store.get_setting("crypto_state_" + symbol, "")
    if not raw and symbol == "BTCUSD":
        raw = store.get_setting("crypto_state", "")
    if not raw:
        return None
    try:
        return _json.loads(raw)
    except Exception:
        return None


def _crypto_card(symbol: str, mode: str):
    st = _crypto_state(symbol)
    if st is None:
        return _card("Crypto (Delta) - SMC " + symbol,
                     "<p style='color:#8b949e'>Worker has not run yet. Start it with "
                     "<code>python -m parallax.apps.worker.crypto_smc --symbol " + symbol
                     + "</code>.</p>")
    else:
        px = st.get("last_price")
        pos = st.get("position")
        order = st.get("order")
        zone = st.get("zone")
        # --- worker liveness: a dead worker must be visible, not silent ---
        age_txt, age_cls = "-", ""
        try:
            from datetime import datetime, timezone
            lb = str(st.get("last_bar", "")).replace("+00:00", "+0000")
            ts = datetime.strptime(lb[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            secs = (datetime.now(timezone.utc) - ts).total_seconds()
            age_txt = ("%.0fm" % (secs / 60)) if secs < 5400 else ("%.1fh" % (secs / 3600))
            # a 1h strategy should have a bar no older than ~2 intervals
            age_cls = "neg" if secs > 2.5 * 3600 else "pos"
        except Exception:
            pass

        grid = ("<div class='grid'>"
                + _stat("Mode", mode.upper())
                + _stat(symbol, _fmt(px, 1) if px else "-")
                + _stat("Bias", {1: "LONG", -1: "SHORT", 0: "flat"}.get(st.get("bias"), "-"))
                + _stat("Equity", "Rs" + _fmt(st.get("equity")))
                + _stat("Bar age", age_txt, age_cls)
                + "</div>")
        body = _card("Crypto (Delta) - SMC FVG retest, %s %s"
                     % (symbol, st.get("interval") or "1h"), grid,
                     "Last completed bar " + str(st.get("last_bar")) + "  |  "
                     + str(st.get("bars_processed")) + " bars processed  |  "
                     + "entries rest as limits (maker), targets limits, stops market (taker)")

        if pos:
            pg = ("<div class='grid'>"
                  + _stat("Side", "LONG" if pos["side"] > 0 else "SHORT")
                  + _stat("Entry", _fmt(pos["entry"], 1))
                  + _stat("Stop", _fmt(pos["stop"], 1))
                  + _stat("Qty", _fmt(pos["qty"], 0) + " cts")
                  + "</div>")
            body += _card("Open position", pg)
        elif order:
            og = ("<div class='grid'>"
                  + _stat("Resting", "BUY" if order["side"] > 0 else "SELL")
                  + _stat("Limit", _fmt(order["price"], 1))
                  + _stat("Stop", _fmt(order["stop"], 1))
                  + _stat("Expires in", str(order["bars_left"]) + " bars")
                  + "</div>")
            body += _card("Working order", og)
        else:
            z = ""
            if zone:
                z = ("Zone " + ("bullish" if zone["direction"] > 0 else "bearish")
                     + ": " + _fmt(zone["bot"], 1) + " - " + _fmt(zone["top"], 1))
            body += _card("Working order", "<p style='color:#8b949e'>Flat - no order "
                          + ("(" + z + ")" if z else "") + "</p>")

        ev = st.get("events") or []
        if ev:
            body += _card("Recent decisions",
                          "<pre style='font-size:12px;color:#8b949e;white-space:pre-wrap'>"
                          + "\n".join(str(e) for e in ev[-6:]) + "</pre>")

        # --- frozen strategy parameters, straight from the config ---
        try:
            from parallax.config.crypto import DEFAULT, USD_INR, product
            sp = product(symbol)          # contract size is per symbol, never assumed
            pg = ("<div class='grid'>"
                  + _stat("Risk / trade", "%.2f%%" % (DEFAULT.risk_pct * 100))
                  + _stat("Leverage", "%.0fx" % DEFAULT.max_leverage)
                  + _stat("Stop floor", "%.1f ATR" % DEFAULT.stop_atr_floor)
                  + _stat("Trail", "%.1f ATR" % DEFAULT.trail_atr)
                  + _stat("Zone", DEFAULT.zone.upper() + " " + DEFAULT.interval)
                  + _stat("Contract", _fmt(sp.contract_value, 4) + " " + symbol[:3])
                  + _stat("Direction", "long+short" if DEFAULT.allow_short else "long only")
                  + "</div>")
            body += _card("Strategy parameters", pg,
                          "Frozen from the walk-forward (REPORT.md s13-s17). "
                          "USD/INR %.1f | venue notional cap $%s | fees %.2f/%.2f bp" %
                          (USD_INR, _fmt(sp.max_notional_usd),
                           sp.maker_rate * 10000, sp.taker_rate * 10000))
        except Exception:
            pass

    return body


@app.get("/crypto", response_class=HTMLResponse)
def crypto():
    trades = store.trades(strategy="crypto", limit=100)
    mode = store.mode()
    body = "".join(_crypto_card(s, mode) for s in CRYPTO_SYMBOLS)
    body += _card("Journal", _trade_rows(trades))
    return _page("Crypto", "crypto", body)


@app.post("/mode")
def switch_mode(mode: str = Form(...)):
    store.set_mode(mode)
    return RedirectResponse("/", status_code=303)


@app.get("/api/state")
def api_state():
    return JSONResponse({
        "mode": store.mode(),
        "capital": store.capital(),
        "summary": store.summary(),
        "positions": store.positions(),
        "trades": store.trades(limit=200),
    })
