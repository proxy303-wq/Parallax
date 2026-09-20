"""Deploy PARALLAX to the VPS and leave BOTH crypto workers in PAPER mode.

Order matters -- a restart must never be the thing that promotes an account to live:
  1. pull the verified commit
  2. assert the stored trade mode is paper BEFORE restarting, and abort if it is not
  3. install/enable both crypto units (BTCUSD + ETHUSD)
  4. restart, then read each worker's own published state back out of the journal store

Credentials come from the environment (VPS_HOST, VPS_PW) -- never hardcoded.
"""
import io
import os
import sys

import paramiko

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HOST = os.environ.get("VPS_HOST")
PW = os.environ.get("VPS_PW")
if not HOST or not PW:
    sys.exit("set VPS_HOST and VPS_PW in the environment first")

SCRIPT = r"""
set -e
cd /opt/parallax

echo "== 1. pull the verified commit =="
git fetch --all -q
git pull --ff-only 2>&1 | tail -3
echo "   HEAD: $(git log --oneline -1)"

echo "== 2. mode BEFORE any restart (must be paper) =="
MODE=$(.venv/bin/python -c "import sqlite3;print(dict(sqlite3.connect('parallax.db').execute('select key,value from settings')).get('mode'))")
echo "   stored mode: $MODE"
if [ "$MODE" != "paper" ]; then
  echo "   REFUSING to restart: mode is '$MODE', not paper"
  exit 1
fi

echo "== 3. install both crypto units =="
cp deploy/parallax-crypto.service     /etc/systemd/system/parallax-crypto.service
cp deploy/parallax-crypto-eth.service /etc/systemd/system/parallax-crypto-eth.service
systemctl daemon-reload
systemctl enable -q parallax-crypto parallax-crypto-eth

echo "== 4. restart =="
systemctl restart parallax-crypto
systemctl restart parallax-crypto-eth
sleep 30
for u in parallax-crypto parallax-crypto-eth; do
  echo "   $u: $(systemctl is-active $u)  $(systemctl show $u -p NRestarts --value) restarts"
done

echo "== 5. each worker's own state =="
.venv/bin/python -c "
import sqlite3, json
r = {k: v for k, v in sqlite3.connect('parallax.db').execute('select key, value from settings')}
print('   mode    :', r.get('mode'), '| paper capital', r.get('paper_capital'))
for s in ('BTCUSD', 'ETHUSD'):
    raw = r.get('crypto_state_' + s)
    if not raw:
        print('   %-7s : NO STATE PUBLISHED' % s); continue
    st = json.loads(raw)
    print('   %-7s : bias %s | bars %s | last_bar %s | price %s | position %s'
          % (s, st.get('bias'), st.get('bars_processed'), st.get('last_bar'),
             st.get('last_price'), bool(st.get('position'))))
"

echo "== 6. logs (last 6 lines each) =="
for u in parallax-crypto parallax-crypto-eth; do
  echo "   --- $u ---"
  journalctl -u $u -n 6 --no-pager | sed 's/^/   /'
done

echo "== 7. everything else still up =="
for u in parallax-web parallax-live parallax-chat; do echo "   $u: $(systemctl is-active $u)"; done
curl -s -o /dev/null -w "   /crypto HTTP %{http_code}
" --max-time 10 http://127.0.0.1:8000/crypto
"""

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username="root", password=PW, timeout=25)
_, so, se = c.exec_command(SCRIPT, timeout=900)
print(so.read().decode(errors="replace"))
err = se.read().decode(errors="replace").strip()
if err:
    print("--STDERR--", err[:800])
c.close()
