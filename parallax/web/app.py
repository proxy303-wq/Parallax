"""PARALLAX dashboard - FastAPI web UI (home / index options / stock options).

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
.opt{background:#8957e522;color:#bc8cff}.stk{background:#3fb95022;color:#3fb950}
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
        for t, h, a in (("Home", "/", "home"),
                        ("Index Options", "/options", "options"),
                        ("Stock Options", "/stock-options", "stock-options")))
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
        cls = "stk" if "stock" in str(t["strategy"]).lower() else "opt"
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


#: The dashboard shows option books only.  Crypto and futures were removed from
#: the UI, but the filtering happens HERE rather than in the store: the workers
#: keep recording to the journal exactly as before, the dashboard just stops
#: displaying them.  Nothing is deleted, so any of it can be shown again by
#: relaxing these predicates.
def _is_option_strategy(strategy) -> bool:
    return "option" in str(strategy or "").lower()


def _option_trades(limit=200):
    return [t for t in store.trades(limit=limit) if _is_option_strategy(t["strategy"])]


def _option_positions():
    return [p for p in store.positions() if _is_option_strategy(p.get("strategy"))]


def _option_summary() -> dict:
    """Aggregate P&L over option strategies only."""
    full = store.summary()
    by = {k: v for k, v in full["by_strategy"].items() if _is_option_strategy(k)}
    return {"total_pnl": round(sum(v["pnl"] for v in by.values())),
            "by_strategy": by,
            "n_trades": sum(v["n"] for v in by.values())}


def _open_pnl() -> float:
    """Open P&L summed from the figure each option book publishes on its own row.

    The holder writes its rupee mark into `target`, so the dashboard never has to
    know a contract's multiplier.
    """
    return sum(float(p.get("target") or 0.0) for p in _option_positions())


def _positions_card():
    rows = _option_positions()
    if not rows:
        return _card("Open Positions",
                     "<p style='color:#8b949e;margin:0'>flat</p>")
    head = ("<table><tr><th>Instrument</th><th>Strategy</th><th>Side</th>"
            "<th>Qty</th><th>Entry</th><th>Mark</th><th>Open P&amp;L</th>"
            "<th>Updated</th></tr>")
    out = ""
    for p in rows:
        strat = str(p.get("strategy") or "")
        pnl = float(p.get("target") or 0.0)
        cls = "pos" if pnl >= 0 else "neg"
        ptxt = "Rs{:+,.0f}".format(pnl)
        out += (f"<tr><td>{p.get('instrument')}</td><td>{strat}</td>"
                f"<td>{p.get('side')}</td><td>{_fmt(p.get('qty'))}</td>"
                f"<td>{_fmt(p.get('entry'), 2)}</td>"
                f"<td>{_fmt(p.get('stop'), 2)}</td>"
                f"<td class='{cls}'>{ptxt}</td>"
                f"<td>{str(p.get('updated') or '')[11:19]}</td></tr>")
    return _card("Open Positions", head + out)


def _summary_cards(cap, summ):
    total = summ["total_pnl"]
    op = _open_pnl()
    cls = "pos" if total >= 0 else "neg"
    ocls = "pos" if op >= 0 else "neg"
    net = total + op
    ncls = "pos" if net >= 0 else "neg"
    grid = ("<div class='grid'>"
            + _stat("Equity", "Rs" + _fmt(cap["equity"]))
            + _stat("Available", "Rs" + _fmt(cap["available"]))
            + _stat("Margin Used", "Rs" + _fmt(cap["margin_used"]))
            + _stat("Closed P&L", "Rs{:+,.0f}".format(total), cls)
            + _stat("Open P&L", "Rs{:+,.0f}".format(op), ocls)
            + _stat("Total P&L", "Rs{:+,.0f}".format(net), ncls)
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
    live = [p for p in _option_positions() if strategy in str(p.get("strategy") or "")]
    if live:
        body += _positions_card()
    body += _card("Journal", _trade_rows(trades))
    return body


@app.get("/", response_class=HTMLResponse)
def home():
    return _page("Home", "home",
                 _summary_cards(store.capital(), _option_summary())
                 + _positions_card()
                 + _card("Trade Journal (recent)", _trade_rows(_option_trades(limit=15))))


@app.get("/options", response_class=HTMLResponse)
def options():
    return _page("Index Options", "options", _strategy_page(
        "options", "Index 0DTE - hedged condor (selling)",
        "Hold to expiry, no stop, wing 3. Per-index shape and entry time are in "
        "deploy/README.md."))


# --------------------------------------------------------------- stock options
# Single-stock (equity) options.  Nothing writes to this book yet, so the page is
# built to render whatever appears -- it starts working the moment a worker
# journals a strategy whose name contains "stock", with no further change here.
STOCK_KEY = "stock"


@app.get("/stock-options", response_class=HTMLResponse)
def stock_options():
    trades = [t for t in store.trades(limit=300)
              if STOCK_KEY in str(t["strategy"]).lower()]
    live = [p for p in _option_positions() if STOCK_KEY in str(p.get("strategy")).lower()]
    wins = sum(1 for t in trades if t["pnl"] > 0)
    pnl = sum(t["pnl"] for t in trades)
    cls = "pos" if pnl >= 0 else "neg"
    body = _card(
        "Stock Options",
        "<div class='grid'>"
        + _stat("P&L", "Rs{:+,.0f}".format(pnl), cls)
        + _stat("Win rate", "{:.0%}".format(wins / len(trades) if trades else 0.0))
        + _stat("Trades", str(len(trades)))
        + "</div>",
        "Single-stock options. No book is wired up yet -- this page is ready for one.")
    body += _positions_card() if live else _card(
        "Open Positions", "<p style='color:#8b949e;margin:0'>flat</p>")
    body += _card("Journal", _trade_rows(trades))
    return _page("Stock Options", "stock-options", body)




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


#: Names the deployment must supply.  PARALLAX_DB_URL is included because
#: without it each container silently falls back to its own ephemeral SQLite
#: file: the journal resets on every redeploy and all six workers authenticate
#: against Dhan separately.
_REQUIRED_ENV = (
    "DHAN_CLIENT_ID", "DHAN_PIN", "DHAN_TOTP_SECRET", "PARALLAX_DB_URL",
    "PARALLAX_TELEGRAM_BOT_TOKEN", "PARALLAX_TELEGRAM_CHAT_ID",
    "DEEPSEEK_API_KEY",
)


@app.get("/health")
def health():
    """Deployment readiness -- "“did my environment variables land?”

    Open this after a deploy.  Anything named in `env.missing` is a variable the
    platform did not pass through, and `ready:false` means the host cannot
    trade.  Names, counts and token metadata only -- never a value, because this
    endpoint sits on the public URL.

    Always answers 200.  A non-2xx here would have the platform evict and
    replace the container, and none of these conditions are fixed by a restart;
    the point is to be *readable*, not to trigger a restart loop.
    """
    from parallax.adapters import db
    from parallax.adapters.env import env

    missing = [n for n in _REQUIRED_ENV if not env(n)]
    out = {
        "service": "parallax",
        "ready": True,
        "env": {"set": [n for n in _REQUIRED_ENV if env(n)], "missing": missing},
    }

    try:
        out["journal"] = {
            "backend": "postgres" if db.is_postgres() else "sqlite",
            "reachable": True,
            "mode": store.mode(),
            "trades": len(store.trades(limit=100000)),
            "open_positions": len(store.positions()),
        }
        if not db.is_postgres():
            out["journal"]["warning"] = (
                "not a shared store -- on a PaaS every container gets its own "
                "ephemeral file, so the journal resets on redeploy")
            out["ready"] = False
    except Exception as exc:
        out["journal"] = {"reachable": False, "error": type(exc).__name__}
        out["ready"] = False

    try:
        from parallax.adapters.broker.dhan_auth import token_status
        st = token_status()
        out["token"] = {"present": bool(st.get("valid")), "type": st.get("type"),
                        "hours_left": st.get("hours_left")}
        if not st.get("valid"):
            out["ready"] = False
    except Exception as exc:
        out["token"] = {"present": False, "error": type(exc).__name__}

    try:
        from parallax.config.schedule import options_plan
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        today = datetime.now(ist).date()
        out["schedule"] = {
            "now_ist": datetime.now(ist).strftime("%Y-%m-%d %H:%M %a"),
            "today": options_plan(today),
            "tomorrow": options_plan(today + timedelta(days=1)),
        }
    except Exception as exc:
        out["schedule"] = {"error": type(exc).__name__}

    if missing:
        out["ready"] = False
    out["status"] = "ok" if out["ready"] else "degraded"
    return JSONResponse(out)
