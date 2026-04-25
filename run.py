#!/usr/bin/env python3
"""
Stowe — entry point.
Run:  python3 run.py

When packaged with PyInstaller (FROZEN=True), launches a native pywebview
window wrapping the local FastAPI server instead of opening a browser tab.
When running from source, falls back to the system browser.
"""
import subprocess
import sys
import time
import threading
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


def _start_uvicorn():
    """Run uvicorn in a background daemon thread."""
    import uvicorn
    from main import app
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="warning",
    )


def _wait_for_server(timeout: float = 10.0):
    """Block until the local server accepts connections."""
    import socket, time
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def launch_webview():
    """Open a native WKWebView (macOS) / WebView2 (Windows) window."""
    import webview  # pywebview

    # Start uvicorn in a background thread before creating the window
    srv = threading.Thread(target=_start_uvicorn, daemon=True)
    srv.start()

    if not _wait_for_server():
        print("WARNING: server didn't start in time; opening anyway…")

    webview.create_window(
        title="Stowe",
        url=URL,
        width=1100,
        height=820,
        min_size=(640, 600),
        # Frameless is false so the OS chrome (traffic lights) shows
        frameless=False,
        easy_drag=False,
    )
    webview.start(debug=False)
    # When the window is closed, webview.start() returns — exit cleanly.
    sys.exit(0)


def launch_browser():
    """Fallback: open the app in the default system browser."""
    import webbrowser

    srv = threading.Thread(target=_start_uvicorn, daemon=True)
    srv.start()

    local_ip = get_local_ip()
    print(f"\nStowe running at {URL}")
    print(f"  On your phone (same WiFi): http://{local_ip}:{PORT}")
    print("Press Ctrl+C to stop.\n")

    def _open():
        _wait_for_server()
        webbrowser.open(URL)

    threading.Thread(target=_open, daemon=True).start()

    # Keep the main thread alive so uvicorn keeps running.
    try:
        srv.join()
    except KeyboardInterrupt:
        pass


def main():
    check_python()
    install_deps()
    init_database()

    if FROZEN:
        # Packaged app → native window
        launch_webview()
    else:
        # From source → browser tab (keeps pywebview optional for devs)
        launch_browser()


if __name__ == "__main__":
    main()
