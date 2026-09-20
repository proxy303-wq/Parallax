"""Count the rendered per-symbol cards on the live dashboard."""
import io, os, sys
import paramiko

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(os.environ["VPS_HOST"], username="root", password=os.environ["VPS_PW"], timeout=25)
remote = r'''
.venv/bin/python - <<'PY'
import re, urllib.request
h = urllib.request.urlopen("http://127.0.0.1:8000/crypto", timeout=10).read().decode()
for label, pat in (("BTC card", "FVG retest, BTCUSD 1h"),
                   ("ETH card", "FVG retest, ETHUSD 1h"),
                   ("XAUT placeholder", "SMC XAUTUSD"),
                   ("strategy param cards", "<h3>Strategy parameters</h3>"),
                   ("Contract stats", ">Contract<"),
                   ("stray BTC/USD label", "BTC/USD")):
    print("   %-22s %d" % (label, len(re.findall(re.escape(pat), h))))
PY
'''
_, so, se = c.exec_command("cd /opt/parallax && " + remote, timeout=180)
print(so.read().decode(errors="replace"))
err = se.read().decode(errors="replace").strip()
if err:
    print("--STDERR--", err[:400])
c.close()
