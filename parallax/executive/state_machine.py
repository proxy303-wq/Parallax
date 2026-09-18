"""Trading state machine (§20)."""
from __future__ import annotations

from parallax.contracts import SystemMode

_TRANSITIONS: dict[SystemMode, set[SystemMode]] = {
    SystemMode.OFFLINE: {SystemMode.OBSERVING},
    SystemMode.OBSERVING: {SystemMode.ANALYZING, SystemMode.PAUSED,
                           SystemMode.EMERGENCY_STOP, SystemMode.OFFLINE},
    SystemMode.ANALYZING: {SystemMode.WAITING, SystemMode.SIGNAL_READY,
                           SystemMode.PAUSED, SystemMode.EMERGENCY_STOP},
    SystemMode.WAITING: {SystemMode.ANALYZING, SystemMode.PAUSED,
                         SystemMode.EMERGENCY_STOP, SystemMode.REFLECTING},
    SystemMode.SIGNAL_READY: {SystemMode.RISK_CHECK, SystemMode.WAITING,
                              SystemMode.PAUSED, SystemMode.EMERGENCY_STOP},
    SystemMode.RISK_CHECK: {SystemMode.EXECUTING, SystemMode.WAITING,
                            SystemMode.PAUSED, SystemMode.EMERGENCY_STOP},
    SystemMode.EXECUTING: {SystemMode.POSITION_ACTIVE, SystemMode.WAITING,
                           SystemMode.EMERGENCY_STOP},
    SystemMode.POSITION_ACTIVE: {SystemMode.EXIT_PENDING, SystemMode.REFLECTING,
                                 SystemMode.EMERGENCY_STOP},
    SystemMode.EXIT_PENDING: {SystemMode.REFLECTING, SystemMode.EMERGENCY_STOP},
    SystemMode.REFLECTING: {SystemMode.OBSERVING, SystemMode.PAUSED,
                            SystemMode.EMERGENCY_STOP},
    SystemMode.PAUSED: {SystemMode.OBSERVING, SystemMode.EMERGENCY_STOP},
    SystemMode.EMERGENCY_STOP: {SystemMode.OFFLINE},
}


class StateMachine:
    def __init__(self, mode: SystemMode = SystemMode.OFFLINE):
        self.mode = mode

    @property
    def state(self) -> SystemMode:
        return self.mode

    def transition(self, to: SystemMode) -> bool:
        if to in _TRANSITIONS.get(self.mode, set()):
            self.mode = to
            return True
        return False

    def force(self, to: SystemMode) -> None:
        """Emergency transitions bypass the normal graph."""
        self.mode = to
