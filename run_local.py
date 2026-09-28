#!/usr/bin/env python3
"""Lokale Flask-server (zelfde app als op Vercel)."""
from __future__ import annotations

import threading
import webbrowser

from kpt.flask_app import app

PORT = 8765


def main() -> None:
    url = f"http://127.0.0.1:{PORT}"
    print(f"KPT tolverdeling: {url}")
    print("Dit venster open laten zolang je de tool gebruikt.")
    print("Opslag: geheugen (lokaal) of Redis als REDIS_URL gezet is.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
