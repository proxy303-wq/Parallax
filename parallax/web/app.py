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
    nxt = "paper" if mode == "live" else "live"
    toggle = ("<form method='post' action='/mode' style='margin:0'>"
              f"<input type='hidden' name='mode' value='{nxt}'>"
              f"<button class='btn'>Switch to {nxt.upper()}</button></form>")
    warn = ""
    if mode == "live":
        warn = ("<div class='card' style='border-color:#f85149'>"
                "<b style='color:#f85149'>LIVE MODE</b> - orders are sent to Dhan "
                "with real money.</div>")
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


@app.get("/crypto", response_class=HTMLResponse)
def crypto():
    trades = store.trades(strategy="crypto", limit=100)
    quotes = "<div class='grid'>"
    try:
        from parallax.adapters.broker.delta import DeltaBroker
        d = DeltaBroker(dry_run=True)
        for sym, label in (("BTC", "BTC/USD"), ("XAUT", "XAUT/USD")):
            q = d.get_quote("DELTA:" + sym)
            quotes += _stat(label, _fmt(q.last, 0))
    except Exception:
        quotes += _stat("BTC/USD", "-") + _stat("XAUT/USD", "-")
    quotes += "</div>"
    body = _card("Crypto (Delta) - paper", quotes,
                 "Strategy engine coming later - page reserved for BTC + XAUTUSD.")
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
