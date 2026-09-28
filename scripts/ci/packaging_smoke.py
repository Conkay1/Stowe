#!/usr/bin/env python3
"""Throwaway packaging smoke test for draft PR #16.

Launches an already-built Stowe package, checks that it listens only on
127.0.0.1, that the API answers on loopback, and that the ledger UI is served
with the vendored Chart.js (no external host).

This does not sign or notarize anything. On macOS, if the unsigned bundle is
killed by the kernel, a temporary copy may be ad-hoc signed with
`codesign --sign -` (no Developer ID, no hardened runtime, no notarization)
so the runner can execute it. The DMG uploaded from CI is never signed.

The ledger screenshot is taken by headless Chromium pointed at the packaged
server, with non-loopback requests aborted. It is not a capture of the
pywebview window. A separate check records whether the native process
exposed a window title.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


TEXT_SUFFIXES = {".html", ".js", ".css", ".json", ".svg", ".md"}
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
PAGE_RESOURCE_TYPES = {
    "document",
    "script",
    "stylesheet",
    "image",
    "font",
    "xhr",
    "fetch",
    "media",
    "manifest",
    "websocket",
}


class CheckLog:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, status: str, detail: str, gating: bool = True) -> None:
        self.checks.append(
            {
                "name": name,
                "status": status,
                "gating": gating,
                "detail": detail.strip(),
            }
        )
        label = status.upper()
        gate = "" if gating else " (non-gating)"
        print(f"[{label}] {name}{gate}: {detail.strip()}", flush=True)

    def gating_failed(self) -> bool:
        return any(c["gating"] and c["status"] != "pass" for c in self.checks)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def comment_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in (r"/\*.*?\*/", r"<!--.*?-->", r"(?<!:)//[^\n]*"):
        spans.extend(m.span() for m in re.finditer(pattern, text, flags=re.S))
    return spans


def _covered(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def external_urls(text: str) -> list[str]:
    """External http(s) and protocol-relative URLs outside comments."""
    spans = comment_spans(text)
    found: list[str] = []
    for match in re.finditer(r"https?://[^\s\"'`<>)\\]+", text):
        if _covered(match.start(), spans):
            continue
        url = match.group(0).rstrip(".,;")
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host not in LOOPBACK_HOSTS:
            found.append(url)
    for match in re.finditer(
        r"(?<![A-Za-z0-9:])//[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s\"'`<>)\\]*",
        text,
    ):
        if _covered(match.start(), spans):
            continue
        found.append(match.group(0).rstrip(".,;"))
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def classify_host(addr: str) -> tuple[str, str, int]:
    """Return (kind, host, port) for a lsof/netstat local address.

    kind is loopback, wildcard, or other.
    """
    if addr.startswith("["):
        host, _, rest = addr[1:].partition("]")
        port_text = rest[1:] if rest.startswith(":") else rest
    else:
        host, _, port_text = addr.rpartition(":")
    try:
        port = int(port_text)
    except ValueError:
        port = -1
    normalized = host.lower()
    if normalized in LOOPBACK_HOSTS:
        kind = "loopback"
    elif normalized in {"0.0.0.0", "::", "*", ""}:
        kind = "wildcard"
    else:
        kind = "other"
    return kind, host, port


def parse_lsof_fn(output: str) -> list[dict]:
    """Parse `lsof -Fpn` output into {pid, addr} records."""
    records: list[dict] = []
    current: dict | None = None
    for raw in output.splitlines():
        if not raw:
            continue
        kind, value = raw[0], raw[1:]
        if kind == "p":
            current = {"pid": int(value), "addrs": []}
            records.append(current)
        elif kind == "n" and current is not None:
            current["addrs"].append(value)
    flat = []
    for rec in records:
        for addr in rec["addrs"]:
            host_kind, host, port = classify_host(addr)
            flat.append(
                {
                    "pid": rec["pid"],
                    "addr": addr,
                    "host": host,
                    "port": port,
                    "kind": host_kind,
                }
            )
    return flat


def parse_netstat_listening(output: str) -> list[dict]:
    """Parse Windows `netstat -ano -p tcp` listening rows."""
    rows = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[0].upper() not in {"TCP", "TCPV6"}:
            continue
        if parts[3].upper() != "LISTENING":
            continue
        host_kind, host, port = classify_host(parts[1])
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        rows.append(
            {
                "pid": pid,
                "addr": parts[1],
                "host": host,
                "port": port,
                "kind": host_kind,
            }
        )
    return rows


def _opener() -> urllib.request.OpenerDirector:
    # Ignore ambient HTTP_PROXY so loopback calls cannot be redirected.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 10,
) -> tuple[int, bytes]:
    data = None
    headers = {"User-Agent": "stowe-packaging-smoke"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener().open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def api_is_stowe(port: int) -> bool:
    try:
        status, body = http_request(f"http://127.0.0.1:{port}/api/v1/categories")
    except Exception:
        return False
    if status != 200:
        return False
    try:
        data = json.loads(body.decode())
    except json.JSONDecodeError:
        return False
    return isinstance(data, list)


def find_vendor(root: Path) -> tuple[Path, Path]:
    matches = [
        p
        for p in root.rglob("chart.umd.min.js")
        if "vendor" in p.parts and "chartjs" in p.parts
    ]
    if not matches:
        matches = list(root.rglob("chart.umd.min.js"))
    if not matches:
        raise FileNotFoundError(f"chart.umd.min.js not found under {root}")
    chart = matches[0]
    license_path = chart.parent / "LICENSE.md"
    if not license_path.is_file():
        raise FileNotFoundError(f"LICENSE.md missing next to {chart}")
    return chart, license_path


def run_cmd(args: list[str], timeout: float = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + "\nTIMEOUT"
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(args, 124, stdout, stderr)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", f"not found: {args[0]}")


def macos_signature_text(path: Path) -> str:
    result = run_cmd(["codesign", "-dv", "--verbose=4", str(path)], timeout=60)
    return (result.stdout + result.stderr).strip()


def developer_id_signed(text: str) -> bool:
    return "Developer ID Application" in text or "Authority=Developer ID" in text


def adhoc_sign_app(app: Path, log: CheckLog) -> None:
    """Ad-hoc sign a temp copy. Not a Developer ID signature and not notarized."""
    contents = app / "Contents"
    binaries = [
        p
        for p in contents.rglob("*")
        if p.is_file() and p.suffix in {".dylib", ".so"}
    ]
    failures = 0
    for binary in binaries:
        result = run_cmd(["codesign", "--force", "--sign", "-", str(binary)], timeout=60)
        if result.returncode != 0:
            failures += 1
    frameworks = sorted(
        [p for p in contents.rglob("*.framework") if p.is_dir()],
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for framework in frameworks:
        result = run_cmd(["codesign", "--force", "--sign", "-", str(framework)], timeout=60)
        if result.returncode != 0:
            failures += 1
    exe = app / "Contents" / "MacOS" / "Stowe"
    for target in (exe, app):
        result = run_cmd(["codesign", "--force", "--sign", "-", str(target)], timeout=60)
        if result.returncode != 0:
            failures += 1
            log.add(
                "adhoc_sign_temp_copy",
                "fail",
                f"codesign --sign - failed for {target}: {result.stderr[-500:]}",
                gating=False,
            )
            return
    log.add(
        "adhoc_sign_temp_copy",
        "pass",
        (
            f"Ad-hoc signed temp copy only ({len(binaries)} libraries, "
            f"{len(frameworks)} frameworks, {failures} individual library failures ignored). "
            "Identity was '-' with no hardened runtime, no entitlements, and no notarization. "
            "The uploaded DMG was not signed."
        ),
        gating=False,
    )


def clear_quarantine(path: Path) -> None:
    if sys.platform != "darwin":
        return
    run_cmd(["xattr", "-dr", "com.apple.quarantine", str(path)], timeout=60)


def process_table() -> list[dict]:
    if sys.platform == "win32":
        script = (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId, ParentProcessId, Name, ExecutablePath | "
            "ConvertTo-Json -Compress"
        )
        result = run_cmd(
            ["powershell", "-NoProfile", "-Command", script],
            timeout=60,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = [data]
        rows = []
        for item in data:
            exe = item.get("ExecutablePath") or ""
            name = item.get("Name") or ""
            rows.append(
                {
                    "pid": int(item["ProcessId"]),
                    "ppid": int(item["ParentProcessId"] or 0),
                    "cmd": exe or name,
                }
            )
        return rows

    result = run_cmd(["ps", "-ax", "-o", "pid=,ppid=,command="], timeout=30)
    rows = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        rows.append(
            {
                "pid": int(parts[0]),
                "ppid": int(parts[1]),
                "cmd": parts[2] if len(parts) > 2 else "",
            }
        )
    return rows


def descendant_pids(root_pids: set[int], table: list[dict] | None = None) -> set[int]:
    table = process_table() if table is None else table
    children: dict[int, list[int]] = {}
    for row in table:
        children.setdefault(row["ppid"], []).append(row["pid"])
    seen = set(root_pids)
    stack = list(root_pids)
    while stack:
        pid = stack.pop()
        for child in children.get(pid, []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def pids_for_path(fragment: str) -> set[int]:
    fragment = str(fragment)
    if sys.platform == "win32":
        needle = fragment.lower()
        return {row["pid"] for row in process_table() if needle in row["cmd"].lower()}
    found = {row["pid"] for row in process_table() if fragment in row["cmd"]}
    if sys.platform == "darwin":
        result = run_cmd(["pgrep", "-f", re.escape(fragment)], timeout=15)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                found.add(int(line))
    return found


def listeners_for(pids: set[int]) -> tuple[list[dict], str]:
    if not pids:
        return [], ""
    if sys.platform == "win32":
        result = run_cmd(["netstat", "-ano", "-p", "tcp"], timeout=30)
        raw = result.stdout
        rows = [row for row in parse_netstat_listening(raw) if row["pid"] in pids]
        return rows, raw
    pid_arg = ",".join(str(pid) for pid in sorted(pids))
    cmd = ["lsof", "-nP", "-a", "-p", pid_arg, "-iTCP", "-sTCP:LISTEN", "-Fpn"]
    result = run_cmd(cmd, timeout=30)
    rows = parse_lsof_fn(result.stdout)
    raw = result.stdout + result.stderr
    if not rows:
        sudo = run_cmd(["sudo", *cmd], timeout=30)
        if sudo.stdout.strip():
            rows = parse_lsof_fn(sudo.stdout)
            raw = sudo.stdout + sudo.stderr
    return rows, raw


def window_title(pid: int) -> str:
    if sys.platform == "win32":
        script = f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).MainWindowTitle"
        result = run_cmd(["powershell", "-NoProfile", "-Command", script], timeout=30)
        return result.stdout.strip()
    script = (
        'tell application "System Events" to get name of windows of process "Stowe"'
    )
    result = run_cmd(["osascript", "-e", script], timeout=30)
    return (result.stdout + result.stderr).strip()


def desktop_screenshot(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        result = run_cmd(["screencapture", "-x", str(path)], timeout=30)
        if result.returncode != 0:
            return f"screencapture failed: {result.stderr.strip()}"
        return f"wrote {path}"
    if sys.platform == "win32":
        script = f"""
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp.Save('{path}')
"""
        result = run_cmd(["powershell", "-NoProfile", "-Command", script], timeout=30)
        if result.returncode != 0 or not path.is_file():
            return f"desktop capture failed: {result.stderr.strip() or result.stdout.strip()}"
        return f"wrote {path}"
    return "desktop capture is only implemented for macOS and Windows"


def kill_pids(pids: set[int]) -> None:
    if not pids:
        return
    if sys.platform == "win32":
        for pid in pids:
            run_cmd(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=30)
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(1)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def collect_crash_reports(dest: Path) -> list[str]:
    if sys.platform != "darwin":
        return []
    diag = Path.home() / "Library" / "Logs" / "DiagnosticReports"
    if not diag.is_dir():
        return []
    copied = []
    reports = sorted(diag.glob("Stowe*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for report in reports[:3]:
        target = dest / report.name
        try:
            shutil.copy2(report, target)
        except OSError:
            continue
        copied.append(str(target))
    return copied


class Launch:
    def __init__(self, root_pids: set[int], method: str, log_path: Path, fragment: str) -> None:
        self.root_pids = root_pids
        self.method = method
        self.log_path = log_path
        self.fragment = fragment
        self.direct: subprocess.Popen[bytes] | None = None

    def refresh(self) -> set[int]:
        found = pids_for_path(self.fragment)
        if self.direct is not None and self.direct.poll() is None:
            found.add(self.direct.pid)
        self.root_pids |= found
        return {pid for pid in self.root_pids if pid in found or (self.direct and pid == self.direct.pid and self.direct.poll() is None)}


def _append_log(log_path: Path, text: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


def start_macos(app: Path, method: str, log_path: Path) -> Launch:
    exe = app / "Contents" / "MacOS" / "Stowe"
    if method == "direct":
        fh = log_path.open("ab", buffering=0)
        try:
            proc = subprocess.Popen(
                [str(exe)],
                stdout=fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            fh.close()
        launch = Launch({proc.pid}, method, log_path, str(exe))
        launch.direct = proc
        _append_log(log_path, f"\n== direct exec pid {proc.pid} ==\n")
        return launch

    _append_log(log_path, f"\n== open -n {app} ==\n")
    result = run_cmd(
        [
            "open",
            "-n",
            str(app),
            "--stdout",
            str(log_path),
            "--stderr",
            str(log_path),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        _append_log(log_path, result.stderr)
        result = run_cmd(["open", "-n", str(app)], timeout=30)
        _append_log(log_path, result.stdout + result.stderr)
    time.sleep(1)
    return Launch(pids_for_path(str(exe)), "open", log_path, str(exe))


def start_windows(exe: Path, log_path: Path) -> Launch:
    fh = log_path.open("ab", buffering=0)
    try:
        proc = subprocess.Popen(
            [str(exe)],
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    finally:
        fh.close()
    _append_log(log_path, f"\n== launch pid {proc.pid} ==\n")
    launch = Launch({proc.pid}, "direct", log_path, str(exe))
    launch.direct = proc
    return launch


def launch_alive(launch: Launch) -> bool:
    alive = launch.refresh()
    if launch.direct is not None:
        return launch.direct.poll() is None
    return bool(alive)


def wait_for_api(launch: Launch, timeout: float) -> int | None:
    deadline = time.time() + timeout
    started = time.time()
    while time.time() < deadline:
        if launch.direct is not None and launch.direct.poll() is not None and not launch.refresh():
            return None
        # `open` returns before the app exists. Give it a short window, then stop
        # if no process with the bundle path is running.
        if (
            launch.direct is None
            and time.time() - started > 20
            and not launch.refresh()
        ):
            return None
        for port in range(8000, 8021):
            if api_is_stowe(port):
                return port
        time.sleep(1)
    return None


def log_tail(path: Path, limit: int = 80) -> str:
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-limit:])


def signature_failure(launch: Launch) -> bool:
    if launch.direct is None or launch.direct.poll() is None:
        return False
    code = launch.direct.returncode
    text = log_tail(launch.log_path, 200).lower()
    if code in {-9, -4, 137}:
        return True
    markers = (
        "code signature",
        "signature invalid",
        "killed",
        "taskgated",
        "amfi",
    )
    return any(marker in text for marker in markers)


def mount_dmg(dmg: Path, work: Path) -> tuple[Path, Path]:
    mount = work / "mnt"
    result = run_cmd(
        [
            "hdiutil",
            "attach",
            str(dmg),
            "-nobrowse",
            "-mountpoint",
            str(mount),
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"hdiutil attach failed: {result.stderr}")
    return mount, mount / "Stowe.app"


def detach_dmg(mount: Path) -> None:
    run_cmd(["hdiutil", "detach", str(mount), "-force"], timeout=60)


def copy_app(src: Path, dest_root: Path) -> Path:
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / "Stowe.app"
    if dest.exists():
        shutil.rmtree(dest)
    result = run_cmd(["ditto", str(src), str(dest)], timeout=180)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    clear_quarantine(dest)
    return dest


def seed_expenses(port: int) -> None:
    samples = [
        {
            "merchant": "Parkside Pharmacy",
            "date": "2026-01-15",
            "amount": 42.5,
            "category": "Pharmacy",
        },
        {
            "merchant": "Harbor Clinic",
            "date": "2026-03-02",
            "amount": 180.0,
            "category": "Medical",
        },
        {
            "merchant": "North Lens Optometry",
            "date": "2026-06-20",
            "amount": 95.0,
            "category": "Vision",
        },
    ]
    for sample in samples:
        status, body = http_request(
            f"http://127.0.0.1:{port}/api/v1/expenses",
            method="POST",
            payload=sample,
        )
        if status != 201:
            raise RuntimeError(f"POST expense failed ({status}): {body[:300]!r}")


def browser_ledger(port: int, screenshot: Path, checks: CheckLog) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        checks.add(
            "charts_render_offline",
            "fail",
            "playwright is not installed; ledger was not rendered in a browser",
        )
        checks.add("screenshot_captured", "fail", "playwright is not installed")
        return

    screenshot.parent.mkdir(parents=True, exist_ok=True)
    blocked: list[str] = []
    console: list[str] = []
    url = f"http://127.0.0.1:{port}/#/ledger"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-proxy-server", "--disable-background-networking"],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                service_workers="block",
            )
            page = context.new_page()

            def handle(route) -> None:
                request = route.request
                host = (urllib.parse.urlparse(request.url).hostname or "").lower()
                if host in LOOPBACK_HOSTS or request.url.startswith(("data:", "blob:", "about:")):
                    route.continue_()
                    return
                blocked.append(f"{request.resource_type} {request.url}")
                route.abort("blockedbyclient")

            page.route("**/*", handle)
            page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}"))
            page.on("pageerror", lambda err: console.append(f"pageerror: {err}"))
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#categoryChart", timeout=20000)
            page.wait_for_timeout(2000)
            info = page.evaluate(
                """() => {
                    const scripts = [...document.querySelectorAll('script[src]')].map(s => s.src);
                    const resources = performance.getEntriesByType('resource').map(r => r.name);
                    const nav = performance.getEntriesByType('navigation').map(r => r.name);
                    function sample(id) {
                      const canvas = document.getElementById(id);
                      if (!canvas) return {id, present: false};
                      const ctx = canvas.getContext('2d');
                      const pix = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                      let opaque = 0;
                      for (let i = 3; i < pix.length; i += 16) if (pix[i] > 0) opaque++;
                      return {id, present: true, w: canvas.width, h: canvas.height, opaque};
                    }
                    return {
                      chartType: typeof window.Chart,
                      scripts,
                      resources: resources.concat(nav),
                      canvases: [sample('categoryChart'), sample('trendChart')],
                      text: document.body.innerText.slice(0, 400),
                    };
                }"""
            )
            page.screenshot(path=str(screenshot), full_page=True)
            browser.close()
    except Exception as exc:
        checks.add(
            "charts_render_offline",
            "fail",
            f"headless browser failed: {exc}\nconsole:\n" + "\n".join(console[-20:]),
        )
        checks.add(
            "screenshot_captured",
            "pass" if screenshot.is_file() else "fail",
            str(screenshot if screenshot.is_file() else exc),
        )
        return

    page_blocked = [
        item
        for item in blocked
        if item.split(" ", 1)[0] in PAGE_RESOURCE_TYPES
    ]
    resources = info.get("resources") or []
    external_resources = []
    for resource in resources:
        host = (urllib.parse.urlparse(resource).hostname or "").lower()
        if resource.startswith(("data:", "blob:")):
            continue
        if host not in LOOPBACK_HOSTS:
            external_resources.append(resource)
    scripts = info.get("scripts") or []
    local_chart = f"http://127.0.0.1:{port}/static/vendor/chartjs/chart.umd.min.js"
    canvases = info.get("canvases") or []
    pixels_ok = all(c.get("present") and c.get("opaque", 0) > 0 for c in canvases)
    chart_ok = info.get("chartType") == "function" and pixels_ok and local_chart in scripts
    detail = json.dumps(
        {
            "chartType": info.get("chartType"),
            "scripts": scripts,
            "canvases": canvases,
            "external_resources": external_resources,
            "page_blocked": page_blocked,
            "other_blocked": [item for item in blocked if item not in page_blocked][:20],
            "console": console[-15:],
            "text": info.get("text"),
        },
        indent=2,
    )
    checks.add("charts_render_offline", "pass" if chart_ok else "fail", detail)
    checks.add(
        "browser_loaded_no_external_host",
        "pass" if not external_resources and not page_blocked else "fail",
        detail,
    )
    checks.add(
        "screenshot_captured",
        "pass" if screenshot.is_file() and screenshot.stat().st_size > 0 else "fail",
        f"{screenshot} ({screenshot.stat().st_size if screenshot.is_file() else 0} bytes)",
    )


def scan_served_frontend(port: int, frontend: Path, chart: Path, checks: CheckLog) -> None:
    bad: list[str] = []
    fetched = 0
    for path in sorted(frontend.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(frontend).as_posix()
        urls = [f"http://127.0.0.1:{port}/static/{rel}"]
        if rel == "index.html":
            urls.insert(0, f"http://127.0.0.1:{port}/")
        for url in urls:
            status, body = http_request(url)
            fetched += 1
            if status != 200:
                bad.append(f"{url} -> HTTP {status}")
                continue
            text = body.decode("utf-8", errors="replace")
            if sha256_bytes(body) != sha256_file(path):
                bad.append(f"{url} body does not match packaged file {path}")
            for external in external_urls(text):
                bad.append(f"{url} references {external}")
            if rel == "index.html" and url.endswith("/") and not any(
                c["name"] == "ledger_page_references_local_chartjs" for c in checks.checks
            ):
                if '/static/vendor/chartjs/chart.umd.min.js' not in text:
                    bad.append("ledger document does not reference vendored Chart.js")
                else:
                    checks.add(
                        "ledger_page_references_local_chartjs",
                        "pass",
                        "GET / (the document pywebview opens; ledger is the #/ledger route) "
                        'contains src="/static/vendor/chartjs/chart.umd.min.js"',
                    )
    if not any(c["name"] == "ledger_page_references_local_chartjs" for c in checks.checks):
        checks.add(
            "ledger_page_references_local_chartjs",
            "fail",
            "index.html was not fetched",
        )
    status, served = http_request(
        f"http://127.0.0.1:{port}/static/vendor/chartjs/chart.umd.min.js"
    )
    if status == 200 and sha256_bytes(served) == sha256_file(chart) and len(served) > 100_000:
        checks.add(
            "served_chartjs_matches_bundle",
            "pass",
            f"sha256 {sha256_bytes(served)} ({len(served)} bytes)",
        )
    else:
        checks.add(
            "served_chartjs_matches_bundle",
            "fail",
            f"HTTP {status}, {len(served)} bytes",
        )
    status, license_body = http_request(
        f"http://127.0.0.1:{port}/static/vendor/chartjs/LICENSE.md"
    )
    license_text = license_body.decode("utf-8", errors="replace")
    if status == 200 and "MIT License" in license_text and "Chart.js" in license_text:
        checks.add("served_license", "pass", "LICENSE.md served from the package and names Chart.js")
    else:
        checks.add("served_license", "fail", f"HTTP {status}: {license_text[:180]}")
    checks.add(
        "no_external_hosts_in_served_pages",
        "pass" if not bad else "fail",
        "scanned packaged HTML/JS/CSS/JSON/SVG/Markdown served by the app\n"
        + ("\n".join(bad) if bad else f"{fetched} responses, no external hosts outside comments"),
    )

    # Informational: FastAPI Swagger is not what the window opens.
    try:
        status, docs = http_request(f"http://127.0.0.1:{port}/api/docs")
        docs_text = docs.decode("utf-8", errors="replace")
        docs_urls = external_urls(docs_text)
        checks.add(
            "fastapi_docs_not_opened_by_window",
            "pass",
            (
                f"GET /api/docs -> HTTP {status}. The pywebview window opens /, not this page. "
                f"External URLs on the docs page: {docs_urls or 'none'}"
            ),
            gating=False,
        )
    except Exception as exc:
        checks.add(
            "fastapi_docs_not_opened_by_window",
            "pass",
            f"docs probe skipped: {exc}",
            gating=False,
        )


def check_vendor_files(chart: Path, license_path: Path, checks: CheckLog, label: str) -> None:
    license_text = license_path.read_text(encoding="utf-8", errors="replace")
    size = chart.stat().st_size
    ok = (
        size > 100_000
        and "MIT License" in license_text
        and "Chart.js" in license_text
        and license_path.parent == chart.parent
    )
    checks.add(
        label,
        "pass" if ok else "fail",
        f"chart={chart} ({size} bytes sha256={sha256_file(chart)}); license={license_path}",
    )


def non_loopback_probe(port: int) -> str:
    ips: set[str] = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ips.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips = {ip for ip in ips if not ip.startswith("127.")}
    notes = []
    for ip in sorted(ips):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        try:
            sock.connect((ip, port))
            notes.append(f"{ip}:{port} ACCEPTED a connection")
        except OSError as exc:
            notes.append(f"{ip}:{port} refused or filtered ({exc})")
        finally:
            sock.close()
    if not notes:
        return "no non-loopback IPv4 address found on this runner"
    return "; ".join(notes)


def assert_runtime(
    launch: Launch,
    port: int,
    checks: CheckLog,
    screenshot: Path,
    chart: Path,
    license_path: Path,
    bundle_root: Path,
) -> None:
    roots = set(launch.root_pids)
    if launch.direct is not None and launch.direct.poll() is None:
        roots.add(launch.direct.pid)
    table = process_table()
    tree = descendant_pids(roots, table)
    rows, raw = listeners_for(tree)
    kinds = sorted({row["kind"] for row in rows})
    summary = ", ".join(f"{row['addr']} pid={row['pid']}" for row in rows) or "no listeners"
    loopback_only = bool(rows) and all(row["kind"] == "loopback" for row in rows)
    checks.add(
        "listens_loopback_only",
        "pass" if loopback_only else "fail",
        f"{summary}\nkinds={kinds}\nprobe: {non_loopback_probe(port)}\n"
        f"raw listener tool output follows\n{raw[:4000]}",
    )
    try:
        status, body = http_request(f"http://127.0.0.1:{port}/api/v1/summary")
        ok = status == 200 and b"total_unreimbursed" in body
        checks.add(
            "api_responds",
            "pass" if ok else "fail",
            f"GET /api/v1/summary -> HTTP {status} {body[:240]!r}",
        )
    except Exception as exc:
        checks.add("api_responds", "fail", str(exc))

    frontend = chart.parents[2]
    if frontend.name != "frontend":
        checks.add(
            "no_external_hosts_in_served_pages",
            "fail",
            f"unexpected frontend dir {frontend}",
        )
    else:
        scan_served_frontend(port, frontend, chart, checks)

    try:
        seed_expenses(port)
    except Exception as exc:
        checks.add("charts_render_offline", "fail", f"could not seed expenses: {exc}")
        checks.add("screenshot_captured", "fail", "skipped because seeding failed")
    else:
        browser_ledger(port, screenshot, checks)

    desk = screenshot.with_name("desktop.png")
    note = desktop_screenshot(desk)
    checks.add(
        "desktop_screenshot",
        "pass" if desk.is_file() else "fail",
        note + " Captured while the packaged process was still the test target.",
        gating=False,
    )
    alive = launch_alive(launch)
    title = ""
    main_pid = next(iter(launch.refresh() or roots), 0)
    if main_pid:
        title = window_title(main_pid)
    checks.add(
        "process_stays_up",
        "pass" if alive else "fail",
        f"method={launch.method} pids={sorted(roots)} alive={alive} window_title={title!r}",
    )
    checks.add(
        "native_window_opened",
        "pass" if title and "error" not in title.lower() and title not in {"missing value", "{}"} else "fail",
        (
            "Window title probe. A failure here means the pywebview window was not "
            f"confirmed. Packaged server checks above still describe what ran. title={title!r}"
        ),
        gating=False,
    )
    _ = license_path, bundle_root


def smoke_macos(dmg: Path, screenshot: Path, log_path: Path, work: Path, checks: CheckLog) -> None:
    if not dmg.is_file():
        checks.add("dmg_built", "fail", f"missing {dmg}")
        return
    checks.add("dmg_built", "pass", f"{dmg} ({dmg.stat().st_size} bytes), unsigned build")
    dmg_sig = macos_signature_text(dmg)
    checks.add(
        "dmg_not_developer_id_signed",
        "pass" if not developer_id_signed(dmg_sig) else "fail",
        dmg_sig[-800:] or "codesign produced no output (treated as unsigned)",
        gating=False,
    )
    mount, app_on_dmg = mount_dmg(dmg, work)
    try:
        if not app_on_dmg.is_dir():
            checks.add("pyinstaller_app_in_dmg", "fail", f"missing {app_on_dmg}")
            return
        checks.add("pyinstaller_app_in_dmg", "pass", str(app_on_dmg))
        app_sig = macos_signature_text(app_on_dmg)
        checks.add(
            "app_not_developer_id_signed",
            "pass" if not developer_id_signed(app_sig) else "fail",
            app_sig[-800:] or "codesign produced no output (treated as unsigned)",
            gating=False,
        )
        chart, license_path = find_vendor(app_on_dmg)
        check_vendor_files(chart, license_path, checks, "dmg_contains_chartjs_and_license")
        app = copy_app(app_on_dmg, work / "launch")
    finally:
        detach_dmg(mount)

    chart, license_path = find_vendor(app)
    attempts: list[tuple[str, bool]] = [
        ("direct", False),
        ("open", False),
        ("direct", True),
        ("open", True),
    ]
    launch: Launch | None = None
    port: int | None = None
    signed = False
    for method, needs_adhoc in attempts:
        if needs_adhoc and not signed:
            _append_log(log_path, "\n== unsigned launch did not stay up; ad-hoc signing temp copy ==\n")
            adhoc_sign_app(app, checks)
            signed = True
        if launch is not None:
            kill_pids(descendant_pids(launch.root_pids))
        launch = start_macos(app, method, log_path)
        port = wait_for_api(launch, 75 if method == "direct" and not needs_adhoc else 45)
        if port is not None and launch_alive(launch):
            checks.add(
                "process_starts",
                "pass",
                f"method={method} adhoc={needs_adhoc} port={port} pids={sorted(launch.root_pids)}",
            )
            break
        exit_code = launch.direct.returncode if launch.direct is not None else "n/a"
        _append_log(
            log_path,
            f"attempt method={method} adhoc={needs_adhoc} exit={exit_code} "
            f"signature_hint={signature_failure(launch)} did not serve API\n",
        )
    else:
        crashes = collect_crash_reports(log_path.parent)
        checks.add(
            "process_starts",
            "fail",
            "packaged app did not stay up and serve the API\n"
            + log_tail(log_path, 120)
            + ("\ncrash reports: " + ", ".join(crashes) if crashes else ""),
        )
        return

    assert launch is not None and port is not None
    if signed:
        checks.add(
            "launch_path",
            "pass",
            "Unsigned DMG contents were copied out and ad-hoc signed for execution on this runner. "
            "Developer ID signing and notarization were not performed. Gatekeeper was not assessed as a pass.",
            gating=False,
        )
    else:
        checks.add(
            "launch_path",
            "pass",
            "Launched the unsigned app copied out of the DMG. No ad-hoc signature was added.",
            gating=False,
        )
    try:
        assert_runtime(launch, port, checks, screenshot, chart, license_path, app)
    finally:
        kill_pids(descendant_pids(set(launch.root_pids) | launch.refresh()))


def smoke_windows(exe: Path, screenshot: Path, log_path: Path, checks: CheckLog) -> None:
    if not exe.is_file():
        checks.add("installed_exe_exists", "fail", f"missing {exe}")
        return
    checks.add(
        "installed_exe_exists",
        "pass",
        f"{exe} ({exe.stat().st_size} bytes). Silent install was performed by the workflow.",
    )
    sig = run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-AuthenticodeSignature -FilePath '{exe}').Status",
        ],
        timeout=60,
    )
    status = sig.stdout.strip() or sig.stderr.strip()
    checks.add(
        "built_without_authenticode",
        "pass" if status.lower() == "notsigned" else "fail",
        f"Get-AuthenticodeSignature status={status or 'unknown'}",
        gating=False,
    )
    try:
        chart, license_path = find_vendor(exe.parent)
    except FileNotFoundError as exc:
        checks.add("installed_bundle_contains_chartjs_and_license", "fail", str(exc))
        return
    check_vendor_files(
        chart,
        license_path,
        checks,
        "installed_bundle_contains_chartjs_and_license",
    )
    launch = start_windows(exe, log_path)
    port = wait_for_api(launch, 90)
    if port is None:
        exit_code = launch.direct.returncode if launch.direct is not None else "n/a"
        checks.add(
            "process_starts",
            "fail",
            f"exit={exit_code}\n" + log_tail(log_path, 120),
        )
        kill_pids(descendant_pids(set(launch.root_pids)))
        return
    checks.add(
        "process_starts",
        "pass",
        f"pid={next(iter(launch.root_pids))} port={port}",
    )
    try:
        assert_runtime(launch, port, checks, screenshot, chart, license_path, exe.parent)
    finally:
        kill_pids(descendant_pids(set(launch.root_pids) | launch.refresh()))


def write_report(path: Path, platform: str, checks: CheckLog, extra: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "platform": platform,
        "unsigned_build": True,
        "signing": "Developer ID, notarization, and Authenticode were not used",
        "what_headless_browser_covers": (
            "Chromium loaded the packaged server with non-loopback requests aborted. "
            "This exercises the packaged FastAPI app, vendored Chart.js, and ledger rendering. "
            "It does not exercise WKWebView or WebView2 pixel output."
        ),
        "checks": checks.checks,
        **extra,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def self_test() -> None:
    lsof = "p4321\nn127.0.0.1:8000\np99\nn*:22\nn[::]:445\nn[::1]:9\n"
    rows = parse_lsof_fn(lsof)
    assert [row["kind"] for row in rows] == ["loopback", "wildcard", "wildcard", "loopback"]
    netstat = """
  TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       4321
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       868
  TCP    [::1]:8000             [::]:0                 LISTENING       4321
"""
    parsed = parse_netstat_listening(netstat)
    assert [row["kind"] for row in parsed] == ["loopback", "wildcard", "loopback"]
    sample = "/* https://www.chartjs.org */\nconst x = 1;\n"
    assert external_urls(sample) == []
    assert external_urls('script.src = "https://cdn.example/chart.js";') == [
        "https://cdn.example/chart.js"
    ]
    assert external_urls("listen on 127.0.0.1 only") == []
    chart = Path("frontend/vendor/chartjs/chart.umd.min.js")
    if chart.is_file():
        assert external_urls(chart.read_text(encoding="utf-8")) == []
    print("self-test ok", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("macos", "windows", "self-test"))
    parser.add_argument("--dmg", type=Path)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()

    if args.platform == "self-test":
        self_test()
        return 0

    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    checks = CheckLog()
    log_path = args.log or Path(tempfile.gettempdir()) / "stowe-packaging-smoke.log"
    screenshot = args.screenshot or Path(tempfile.gettempdir()) / "ledger.png"
    report = args.report or Path(tempfile.gettempdir()) / "packaging-smoke-report.json"
    log_path.write_text(
        "UNSIGNED BUILD. No Developer ID, notarization, or Authenticode.\n",
        encoding="utf-8",
    )
    work = Path(tempfile.mkdtemp(prefix="stowe-smoke-"))
    try:
        if args.platform == "macos":
            if args.dmg is None:
                checks.add("dmg_built", "fail", "--dmg is required")
            else:
                smoke_macos(args.dmg, screenshot, log_path, work, checks)
        else:
            if args.executable is None:
                checks.add("installed_exe_exists", "fail", "--executable is required")
            else:
                smoke_windows(args.executable, screenshot, log_path, checks)
    finally:
        extra = {"log": str(log_path), "work_dir": str(work)}
        write_report(report, args.platform, checks, extra)
        print(f"report: {report}", flush=True)
    return 1 if checks.gating_failed() else 0


if __name__ == "__main__":
    sys.exit(main())
