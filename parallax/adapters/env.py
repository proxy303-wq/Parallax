"""Shared environment / .env loader.  Values are never logged."""
from __future__ import annotations

import os

_ENV_FILES = (
    os.environ.get("PARALLAX_ENV_FILE"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), ".env"),      # C:\Parallax\.env
    r"C:\PrOxyTradingTerminal\.env",
    r"C:\Athena_X\.env",
)

_CACHE: dict = {}


def env(name: str, default: str = "") -> str:
    if name in _CACHE:
        return _CACHE[name]
    v = os.environ.get(name)
    if not v:
        for path in _ENV_FILES:
            if not path or not os.path.exists(path):
                continue
            try:
                for line in open(path, encoding="utf-8-sig"):
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, val = line.partition("=")
                    if k.strip() == name:
                        v = val.strip().strip('"').strip("'")
                        break
            except OSError:
                continue
            if v:
                break
    v = v or default
    _CACHE[name] = v
    return v


def mask(secret: str | None) -> str:
    return "unset" if not secret else "set(" + str(len(secret)) + " chars)"
