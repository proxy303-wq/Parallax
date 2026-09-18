"""JSON-file persistence for memory + audit + state."""
from __future__ import annotations

import json
import os
from datetime import datetime


class JsonStore:
    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def save(self, name: str, obj) -> str:
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, default=str)
        return path

    def load(self, name: str, default=None):
        path = os.path.join(self.directory, name)
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_audit(self, events: list) -> str:
        return self.save("audit.json", [e.as_dict() for e in events])

    def save_memory(self, memory) -> str:
        return self.save("memory.json", {
            "episodic": [r.__dict__ for r in memory.episodic],
            "errors": [r.__dict__ for r in memory.errors],
        })
