"""Why no signal yet: the arming flag, and how long each worker has actually run."""
import io, os, sys
import paramiko

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(os.environ["VPS_HOST"], username="root", password=os.environ["VPS_PW"], timeout=25)

remote = r'''
cd /opt/parallax
echo "== process uptime =="
for u in parallax-crypto parallax-crypto-eth; do
  PID=$(systemctl show $u -p ExecMainPID --value)
  echo "   $u pid=$PID started $(ps -o lstart= -p $PID 2>/dev/null) elapsed $(ps -o etime= -p $PID 2>/dev/null)"
done
echo "   server now: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

echo
echo "== armed flag (this is what gates a new limit order) =="
.venv/bin/python - <<'PY'
import json, sqlite3
from datetime import datetime, timezone
con = sqlite3.connect("parallax.db")
r = {k: v for k, v in con.execute("select key, value from settings")}
for sym in ("BTCUSD", "ETHUSD"):
    s = json.loads(r.get("crypto_state_" + sym) or "{}")
    bias = s.get("bias")
    z = s.get("zone") or {}
    zd = z.get("direction")
    print("   %-7s bias=%s  zone.dir=%s  armed=%s  -> %s" % (
        sym, {1: "LONG", -1: "SHORT", 0: "flat"}.get(bias), zd, s.get("armed"),
        "WILL arm on this zone" if (zd is not None and bias == zd and not s.get("armed"))
        else ("already armed on this zone (mirrors the backtest state)" if s.get("armed")
              else "no -- bias and zone disagree")))
    if z:
        print("           zone %.2f - %.2f" % (z["bot"], z["top"]))
PY
'''
_, so, se = c.exec_command(remote, timeout=300)
print(so.read().decode(errors="replace"))
err = se.read().decode(errors="replace").strip()
if err:
    print("--STDERR--", err[:400])
c.close()
