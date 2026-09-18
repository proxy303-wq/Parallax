"""PARALLAX — observability & audit trail (§23)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import ExecutionMode


@dataclass
class AuditEvent:
    id: str
    timestamp: datetime
    actor: str            # "executive" | "risk" | "decision" | "telegram:user"
    action: str
    target: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    mode: ExecutionMode = ExecutionMode.PAPER

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "timestamp": self.timestamp.isoformat(),
            "actor": self.actor, "action": self.action, "target": self.target,
            "details": self.details, "mode": self.mode.value,
        }
