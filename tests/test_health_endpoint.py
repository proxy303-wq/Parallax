"""The /health deployment check.

This endpoint exists so a new host can be verified from a browser, which means
it sits on a public URL.  It may therefore report WHICH variables arrived and
never what they contain -- a "helpful" error message that echoes a token would
turn a deployment aid into a credential leak.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from parallax.adapters.env import env
from parallax.web.app import _REQUIRED_ENV, app

CLIENT = TestClient(app)


def test_health_answers_and_reports_env_presence():
    r = CLIENT.get("/health")
    assert r.status_code == 200, "must not 503 -- the platform would evict the container"
    body = r.json()
    assert body["service"] == "parallax"
    assert set(body["env"]["set"]) | set(body["env"]["missing"]) == set(_REQUIRED_ENV)
    assert isinstance(body["env"]["missing"], list)


def test_health_never_echoes_a_secret_value():
    raw = CLIENT.get("/health").text
    leaked = []
    for name in _REQUIRED_ENV + ("DHAN_ACCESS_TOKEN", "TYPESAFE_API_KEY",
                                 "DELTA_API_KEY", "DELTA_API_SECRET"):
        value = env(name)
        if len(value) >= 8 and value in raw:
            leaked.append(name)
    assert not leaked, f"response body leaked: {leaked}"


def test_health_reports_sqlite_as_not_ready():
    """SQLite means per-container storage, which is wrong on a PaaS."""
    body = CLIENT.get("/health").json()
    assert body["journal"]["backend"] in ("sqlite", "postgres")
    if body["journal"]["backend"] == "sqlite":
        assert body["ready"] is False
        assert "warning" in body["journal"]


def test_health_includes_the_schedule():
    body = CLIENT.get("/health").json()
    assert "now_ist" in body["schedule"], "IST must drive the schedule, not host local time"
    assert isinstance(body["schedule"]["today"], list)
