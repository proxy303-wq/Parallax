"""Execution modes and the promotion gate (§3, §25).

Paper -> shadow -> assisted-live -> controlled-live.  The kill switch and
pause are independent and always available.  Autonomous escalation of risk
limits or credentials is out of scope.
"""
from __future__ import annotations

from parallax.contracts import ExecutionMode

_PROMOTION_ORDER = [
    ExecutionMode.PAPER,
    ExecutionMode.SHADOW,
    ExecutionMode.ASSISTED_LIVE,
    ExecutionMode.CONTROLLED_LIVE,
]


class ModeManager:
    def __init__(self, mode: ExecutionMode = ExecutionMode.PAPER):
        self.mode = mode
        self.kill_switch = False
        self.paused = False

    def can_promote_to(self, target: ExecutionMode) -> bool:
        if target not in _PROMOTION_ORDER:
            return False
        return _PROMOTION_ORDER.index(target) == _PROMOTION_ORDER.index(self.mode) + 1

    def promote(self) -> ExecutionMode:
        i = _PROMOTION_ORDER.index(self.mode)
        if i + 1 < len(_PROMOTION_ORDER):
            self.mode = _PROMOTION_ORDER[i + 1]
        return self.mode

    def demote(self) -> ExecutionMode:
        i = _PROMOTION_ORDER.index(self.mode)
        if i > 0:
            self.mode = _PROMOTION_ORDER[i - 1]
        return self.mode

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def kill(self) -> None:
        self.kill_switch = True

    def reset_kill(self) -> None:
        self.kill_switch = False
