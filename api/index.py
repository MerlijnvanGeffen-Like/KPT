"""Vercel serverless entrypoint."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kpt.flask_app import app  # noqa: E402

# Vercel Python runtime expects a WSGI/ASGI `app`.
