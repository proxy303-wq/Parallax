"""OpenAI-compatible reasoning clients (DeepSeek + TypeSafe AI).

Credentials are read at call time from the process environment or the known
.env files, never logged, and every failure returns None so callers fall back
to deterministic reasoning.  These clients only produce text/JSON — they can
never place an order or read broker state.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


def _env_files() -> tuple:
    return (
        os.environ.get("PARALLAX_ENV_FILE"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), ".env"),  # C:\Parallax\.env
        r"C:\PrOxyTradingTerminal\.env",
        r"C:\Athena_X\.env",
    )


def _parse_env(path: str | None) -> dict:
    out = {}
    if not path or not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                if v:
                    out[k.strip()] = v
    except OSError:
        pass
    return out


def _env(name: str) -> str:
    v = os.environ.get(name)
    if v:
        return v
    for path in _env_files():
        v = _parse_env(path).get(name)
        if v:
            return v
    return ""


def mask(secret: str | None) -> str:
    return "unset" if not secret else "set(" + str(len(secret)) + " chars)"


class OpenAICompatClient:
    """A minimal OpenAI-compatible chat-completions client.

    Subclasses set ENV_KEY / ENV_BASE / ENV_MODEL and DEFAULT_ENDPOINT /
    DEFAULT_MODEL.  A client is enabled only when both a key and an endpoint
    resolve, so an unconfigured provider is a harmless no-op.
    """
    ENV_KEY = "API_KEY"
    ENV_BASE = "API_BASE"
    ENV_MODEL = "MODEL"
    DEFAULT_ENDPOINT = ""
    DEFAULT_MODEL = ""

    def __init__(self, api_key: str | None = None, endpoint: str | None = None,
                 model: str | None = None, timeout: int = 40):
        self.api_key = api_key if api_key is not None else _env(self.ENV_KEY)
        self.endpoint = endpoint or _env(self.ENV_BASE) or self.DEFAULT_ENDPOINT
        self.model = model or _env(self.ENV_MODEL) or self.DEFAULT_MODEL
        self.timeout = timeout

    @property
    def name(self) -> str:
        return self.__class__.__name__.replace("Client", "")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.endpoint)

    def masked(self) -> dict:
        return {"provider": self.name, "endpoint": self.endpoint,
                "model": self.model, "api_key": mask(self.api_key)}

    def complete(self, messages: list, temperature: float = 0.2,
                 max_tokens: int = 1400) -> str | None:
        if not self.enabled:
            return None
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }).encode()
        req = urllib.request.Request(self.endpoint, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]
        except Exception:
            return None

    def complete_json(self, messages: list, temperature: float = 0.2) -> dict | None:
        text = self.complete(messages, temperature=temperature)
        if not text:
            return None
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


class DeepSeekClient(OpenAICompatClient):
    ENV_KEY = "DEEPSEEK_API_KEY"
    ENV_BASE = "DEEPSEEK_API_BASE"
    ENV_MODEL = "DEEPSEEK_MODEL"
    DEFAULT_ENDPOINT = "https://api.deepseek.com/chat/completions"
    DEFAULT_MODEL = "deepseek-chat"


