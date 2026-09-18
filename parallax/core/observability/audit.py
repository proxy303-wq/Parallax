"""Append-only audit trail (§23)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from parallax.contracts import AuditEvent, ExecutionMode, new_id


class AuditLog:
    def __init__(self):
        self.events: list[AuditEvent] = []

    def record(self, actor: str, action: str, target: str = "",
               details: dict | None = None,
               mode: ExecutionMode = ExecutionMode.PAPER) -> AuditEvent:
        ev = AuditEvent(
            id=new_id("audit"),
            timestamp=datetime.now(timezone.utc),
            actor=actor, action=action, target=target,
            details=details or {}, mode=mode)
        self.events.append(ev)
        return ev

    def as_list(self) -> list[dict]:
        return [e.as_dict() for e in self.events]

    def tail(self, n: int = 20) -> list[AuditEvent]:
        return self.events[-n:]

    def to_file(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.as_list(), f, indent=2, default=str)
