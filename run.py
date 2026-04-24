#!/usr/bin/env python3
"""
Stowe — entry point.
Run:  python3 run.py
"""
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

BASE = Path(__file__).parent
FROZEN = getattr(sys, "frozen", False)

# When running from source on older system Python, re-exec into a newer
# interpreter if one exists. Skip this entirely inside a packaged app.
if not FROZEN and sys.version_info < (3, 10):
    _PREFERRED_PYTHONS = ["/opt/homebrew/bin/python3.11", "/usr/local/bin/python3.11"]
    for _py in _PREFERRED_PYTHONS:
        if Path(_py).exists():
            import os
            os.execv(_py, [_py] + sys.argv)
            break


def find_free_port(start=8000, end=8020) -> int:
    import socket
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return start


def get_local_ip() -> str:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "unknown"


PORT = find_free_port()
URL = f"http://localhost:{PORT}"


def check_python():
    if sys.version_info < (3, 9):
        print("ERROR: Python 3.9 or higher is required.")
        print(f"  Current: {sys.version}")
        sys.exit(1)


def install_deps():
    if FROZEN:
        return  # dependencies are bundled inside the packaged app
    req = BASE / "requirements.txt"
    print("Checking dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("WARNING: Dependency install had issues:")
        print(result.stderr[:500])


def init_database():
    sys.path.insert(0, str(BASE))
    from backend.db import init_db
    print("Initializing database...")
    init_db()
    print("Database ready.")


def open_browser():
    time.sleep(1.5)
    webbrowser.open(URL)


def main():
    check_python()
    install_deps()
    init_database()

    local_ip = get_local_ip()
    print(f"\n🏥 Stowe running at {URL}")
    print(f"  On your phone (same WiFi): http://{local_ip}:{PORT}")
    print("Press Ctrl+C to stop.\n")

    import threading
    t = threading.Thread(target=open_browser, daemon=True)
    t.start()

    import uvicorn
    from main import app
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
