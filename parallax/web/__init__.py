"""PARALLAX web dashboard."""
from .store import JournalStore
from .app import app

__all__ = ["JournalStore", "app"]
