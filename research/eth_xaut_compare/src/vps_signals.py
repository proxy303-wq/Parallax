"""Have the paper crypto workers produced any signals since deployment?"""
import io, os, sys
import paramiko

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(os.environ["VPS_HOST"], username="root", password=os.environ["VPS_PW"], timeout=25)

remote = r'''
cd /opt/parallax
echo "== now =="
date -u '+   UTC %Y-%m-%d %H:%M:%S'
echo "   worker started: $(systemctl show parallax-crypto -p ActiveEnterTimestamp --value)"
echo "                   $(systemctl show parallax-crypto-eth -p ActiveEnterTimestamp --value)"

echo
echo "== worker state + event log =="
.venv/bin/python - <<'PY'
import json, sqlite3
from datetime import datetime, timezone

con = sqlite3.connect("parallax.db")
r = {k: v for k, v in con.execute("select key, value from settings")}
now = datetime.now(timezone.utc)

for sym in ("BTCUSD", "ETHUSD"):
    raw = r.get("crypto_state_" + sym)
    print("  --- %s ---" % sym)
    if not raw:
        print("     NO STATE"); continue
    s = json.loads(raw)
    print("     bias        :", {1: "LONG", -1: "SHORT", 0: "flat"}.get(s.get("bias")))
    print("     zone        :", s.get("zone"))
    print("     resting order:", s.get("order"))
    print("     position    :", s.get("position"))
    print("     last bar    :", s.get("last_bar"), "| bars processed", s.get("bars_processed"))
    lb = str(s.get("last_bar", "")).replace("+00:00", "+0000")
    try:
        ts = datetime.strptime(lb[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        print("     bar age     : %.1f min" % ((now - ts).total_seconds() / 60))
    except Exception:
        pass
    ev = s.get("events") or []
    print("     events      : %d recorded" % len(ev))
    for e in ev[-6:]:
        print("        ", e)

print()
print("  == trades recorded (strategy=crypto) ==")
rows = list(con.execute("select ts, instrument, side, qty, entry, exit, pnl, outcome, note "
                        "from trades where strategy='crypto' order by id desc limit 10"))
if not rows:
    print("     none")
for t in rows:
    print("     ", t)

print()
print("  == open position rows ==")
pos = list(con.execute("select instrument, side, qty, entry, stop, updated from positions"))
print("     ", pos if pos else "none")
PY

echo
echo "== decision lines in the journal since start =="
for u in parallax-crypto parallax-crypto-eth; do
  echo "   --- $u ---"
  journalctl -u $u --no-pager | grep -iE "armed|limit filled|limit expired|exit|ORDER RESTING|FILLED|CLOSED|bias" | tail -8 | sed 's/^/   /'
  echo "   (blank above = no signal lines yet)"
done
'''
_, so, se = c.exec_command(remote, timeout=300)
print(so.read().decode(errors="replace"))
err = se.read().decode(errors="replace").strip()
if err:
    print("--STDERR--", err[:500])
c.close()
