"""Read-only VPS deployment status for the PARALLAX crypto worker.

Credentials come from the environment -- never hardcoded, never committed:
    set VPS_HOST=<ip>   VPS_PW=<password>
(DEPLOY.md documents this.  A root password in a repo is a breach waiting to happen.)
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

cmds = [
    ("all parallax units",
     "systemctl list-units --type=service --all --no-pager | grep -i parallax || echo '(none)'"),
    ("crypto unit state",
     "systemctl is-active parallax-crypto; systemctl is-enabled parallax-crypto 2>/dev/null || echo not-enabled"),
    ("crypto unit detail",
     "systemctl show parallax-crypto -p ActiveState,SubState,NRestarts,ExecMainPID --no-pager 2>/dev/null || echo 'unit missing'"),
    ("stored mode + crypto state",
     "cd /opt/parallax && .venv/bin/python -c \"\nimport sqlite3, json\n"
     "con = sqlite3.connect('parallax.db')\n"
     "r = {k: v for k, v in con.execute('select key, value from settings')}\n"
     "print('  mode         :', r.get('mode'))\n"
     "print('  paper_capital:', r.get('paper_capital'))\n"
     "s = json.loads(r.get('crypto_state') or '{}')\n"
     "print('  symbol       :', s.get('symbol'))\n"
     "print('  bias         :', s.get('bias'))\n"
     "print('  last_bar     :', s.get('last_bar'))\n"
     "print('  bars         :', s.get('bars_processed'))\n"
     "print('  position     :', s.get('position'))\n\""),
    ("repo state", "cd /opt/parallax && git log --oneline -1 && git status --short | grep -v venv | head"),
    ("worker importable",
     "cd /opt/parallax && .venv/bin/python -c \"from parallax.core.smc_crypto import SMCCrypto; "
     "from parallax.apps.worker.crypto_smc import CryptoWorker; print('worker imports OK')\""),
]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username="root", password=PW, timeout=25)
for label, cmd in cmds:
    _, so, se = c.exec_command(cmd, timeout=180)
    out = so.read().decode(errors="replace").strip()
    err = se.read().decode(errors="replace").strip()
    print("--- %s ---" % label)
    print(out if out else "(no stdout)")
    if err:
        print("   err:", err[:300])
    print()
c.close()
