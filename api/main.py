import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

os.environ["ENABLE_POLLER"] = "false"

from app.main import app, handler

__all__ = ["app", "handler"]
