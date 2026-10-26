import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import ocr


_START_WARMUP = ocr.start_image_warmup
_STOP_WARMUP = ocr.stop_image_warmup

TOKENS = "SYNTHETIC PHARMACY\nReceipt for test only\nTotal $42.50"


@pytest.fixture(autouse=True)
def isolated_warmup(monkeypatch, no_native_warmup):
    monkeypatch.setattr(ocr, "start_image_warmup", _START_WARMUP)
    monkeypatch.setattr(ocr, "stop_image_warmup", _STOP_WARMUP)
    monkeypatch.setattr(ocr, "_IS_MACOS", True)
    monkeypatch.setattr(ocr, "_WARMUP_LOCK", threading.Lock())
    monkeypatch.setattr(ocr, "_WARMUP_CANCEL", threading.Event())
    monkeypatch.setattr(ocr, "_WARMUP_STATE", "not_started")
    monkeypatch.setattr(ocr, "_WARMUP_THREAD", None)
    monkeypatch.setattr(ocr, "_WARMUP_PROCESS", None)
    monkeypatch.setattr(ocr, "_OCR_SLOTS", threading.BoundedSemaphore(2))
    monkeypatch.setattr(ocr.atexit, "register", lambda _fn: None)
    yield
    ocr._WARMUP_CANCEL.set()
    thread = ocr._WARMUP_THREAD
    if thread is not None:
        thread.join(timeout=3)
        assert not thread.is_alive()


def fake_worker(monkeypatch, *, result=None, returncode=0, error=None):
    calls = []

    class Process:
        def __init__(self, args, **kwargs):
            self.returncode = None
            self.killed = False
            calls.append((self, args, kwargs))
            Path(args[-1]).write_text(json.dumps(result))

        def wait(self, timeout=None):
            if error and not self.killed:
                raise error
            self.returncode = returncode if not self.killed else -9
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    monkeypatch.setattr(ocr.subprocess, "Popen", Process)
    return calls


def run_warmup():
    ocr._WARMUP_STATE = "warming"
    ocr._warm_image_worker(time.monotonic() + 2)


def test_warmup_requires_verified_synthetic_vision_output(monkeypatch):
    calls = fake_worker(monkeypatch, result=[TOKENS, "vision"])
    run_warmup()
    assert ocr._WARMUP_STATE == "ready"
    assert ocr.image_ocr_notice() is None
    process, command, kwargs = calls[0]
    assert command[:3] == ["/usr/bin/nice", "-n", "10"]
    assert "--stowe-ocr-worker" in command
    assert Path(command[-2]).name == "ocr-warmup.png"
    assert not Path(command[-1]).parent.exists()
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == ocr.subprocess.DEVNULL
    assert process.returncode == 0
    assert ocr._WARMUP_PROCESS is None
    assert ocr._OCR_SLOTS.acquire(blocking=False)
    assert ocr._OCR_SLOTS.acquire(blocking=False)


@pytest.mark.parametrize("result", [None, {}, [TOKENS], [TOKENS, "none"], [TOKENS, "pdf-text"], [TOKENS, "vision", "extra"], [True, "vision"], ["SYNTHETIC PHARMACY 42.50", "vision"], ["Receipt for test only 42.50", "vision"], ["SYNTHETIC PHARMACY Receipt for test only", "vision"]])
def test_exit_zero_without_verified_tokens_stays_unavailable(monkeypatch, result):
    fake_worker(monkeypatch, result=result)
    run_warmup()
    assert ocr._WARMUP_STATE == "failed"
    assert "Restart Stowe" in ocr.image_ocr_notice()


def test_nonzero_exit_cannot_be_ready(monkeypatch):
    fake_worker(monkeypatch, result=[TOKENS, "vision"], returncode=2)
    run_warmup()
    assert ocr._WARMUP_STATE == "failed"


def test_timeout_kills_reaps_and_cleans_private_directory(monkeypatch):
    calls = fake_worker(monkeypatch, result=[TOKENS, "vision"], error=ocr.subprocess.TimeoutExpired("synthetic", 0.1))
    run_warmup()
    assert ocr._WARMUP_STATE == "failed"
    process, command, _ = calls[0]
    assert process.killed and process.returncode == -9
    assert not Path(command[-1]).parent.exists()
    assert ocr._OCR_SLOTS.acquire(blocking=False)
    assert ocr._OCR_SLOTS.acquire(blocking=False)


@pytest.mark.parametrize("reason", ["expired", "cancelled", "hash"])
def test_invalid_start_does_not_launch_worker(monkeypatch, reason):
    calls = fake_worker(monkeypatch, result=[TOKENS, "vision"])
    if reason == "cancelled":
        ocr._WARMUP_CANCEL.set()
    if reason == "hash":
        monkeypatch.setattr(ocr, "_WARMUP_IMAGE_SHA256", "0" * 64)
    ocr._warm_image_worker(time.monotonic() + (-1 if reason == "expired" else 1))
    assert not calls
    assert ocr._WARMUP_STATE != "ready"


def test_start_is_nonblocking_and_only_once_with_exit_cleanup(monkeypatch):
    started, release = threading.Event(), threading.Event()
    registered = []

    def slow(_deadline):
        started.set()
        assert release.wait(2)

    monkeypatch.setattr(ocr, "_warm_image_worker", slow)
    monkeypatch.setattr(ocr.atexit, "register", registered.append)
    before = time.monotonic()
    try:
        assert ocr.start_image_warmup() is True
        assert time.monotonic() - before < 0.5
        assert started.wait(1)
        assert ocr.start_image_warmup() is False
        assert ocr._WARMUP_STATE == "warming"
        assert registered == [ocr.stop_image_warmup]
    finally:
        release.set()
        ocr._WARMUP_THREAD.join(timeout=2)


def test_thread_start_failure_leaves_app_available(monkeypatch):
    class Thread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("synthetic thread refusal")

    monkeypatch.setattr(ocr.threading, "Thread", Thread)
    assert ocr.start_image_warmup() is False
    assert ocr._WARMUP_THREAD is None
    assert ocr._WARMUP_STATE == "failed"
    assert "Restart Stowe" in ocr.image_ocr_notice()


def test_ready_image_uses_normal_request_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "_WARMUP_STATE", "ready")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        Path(args[-1]).write_text(json.dumps([TOKENS, "vision"]))

    monkeypatch.setattr(ocr.subprocess, "run", run)
    assert ocr.extract_text(tmp_path / "receipt.png") == (TOKENS, "vision")
    assert 0 < calls[0][1]["timeout"] <= 10


def test_nonmac_never_launches_warmup(monkeypatch):
    monkeypatch.setattr(ocr, "_IS_MACOS", False)
    monkeypatch.setattr(ocr, "_warm_image_worker", lambda _: pytest.fail("No native worker off macOS"))
    assert ocr.start_image_warmup() is False
    assert "macOS" in ocr.image_ocr_notice()


def test_shutdown_cancels_slot_wait_without_child(monkeypatch):
    calls = fake_worker(monkeypatch, result=[TOKENS, "vision"])
    assert ocr._OCR_SLOTS.acquire(blocking=False)
    assert ocr._OCR_SLOTS.acquire(blocking=False)
    assert ocr.start_image_warmup()
    ocr.stop_image_warmup()
    assert not calls
    assert not ocr._WARMUP_THREAD.is_alive()
    assert ocr._WARMUP_STATE == "stopped"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX worker lifecycle")
@pytest.mark.parametrize("action", ["deadline", "shutdown"])
def test_actual_disposable_worker_is_reaped(tmp_path, monkeypatch, action):
    pid_path = tmp_path / "pid"
    late_path = tmp_path / "late"
    worker = tmp_path / "stall.py"
    worker.write_text(
        "import os, pathlib, time\n"
        f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(2)\n"
        f"pathlib.Path({str(late_path)!r}).write_text('unexpected')\n"
    )
    processes = []
    popen = ocr.subprocess.Popen

    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(ocr.subprocess, "Popen", capture)
    monkeypatch.setattr(ocr, "_worker_command", lambda *_: [sys.executable, "-I", "-B", str(worker)])
    monkeypatch.setattr(ocr, "OCR_WARMUP_TIMEOUT_SECONDS", 0.5 if action == "deadline" else 10)
    assert ocr.start_image_warmup()
    deadline = time.monotonic() + 2
    while not pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pid_path.exists()
    if action == "shutdown":
        ocr.stop_image_warmup()
    else:
        ocr._WARMUP_THREAD.join(timeout=2)
    assert not ocr._WARMUP_THREAD.is_alive()
    assert len(processes) == 1 and processes[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_path.read_text()), 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(processes[0].pid, os.WNOHANG)
    assert ocr._WARMUP_STATE == ("stopped" if action == "shutdown" else "failed")
    assert not late_path.exists()
    assert ocr._OCR_SLOTS.acquire(blocking=False)
    assert ocr._OCR_SLOTS.acquire(blocking=False)


@pytest.mark.parametrize("state", ["not_started", "warming", "failed", "stopped"])
def test_image_requests_fast_fail_until_ready(tmp_path, monkeypatch, state):
    monkeypatch.setattr(ocr, "_WARMUP_STATE", state)
    monkeypatch.setattr(ocr.subprocess, "run", lambda *_a, **_kw: pytest.fail("No request worker before readiness"))
    assert ocr.extract_text(tmp_path / "receipt.png") == ("", "none")
    assert ocr.image_ocr_notice()


@pytest.mark.parametrize("state", ["warming", "failed", "ready"])
def test_pdf_request_remains_available(tmp_path, monkeypatch, state):
    monkeypatch.setattr(ocr, "_WARMUP_STATE", state)

    def run(args, **_kwargs):
        Path(args[-1]).write_text(json.dumps(["synthetic PDF text", "pdf-text"]))

    monkeypatch.setattr(ocr.subprocess, "run", run)
    assert ocr.extract_text(tmp_path / "receipt.pdf") == ("synthetic PDF text", "pdf-text")


@pytest.mark.parametrize("slot_elapsed", [6.0, 10.0, 11.0])
def test_request_deadline_includes_slot_wait(tmp_path, monkeypatch, slot_elapsed):
    clock = [100.0]
    calls, released, timeouts = [], [], []

    def acquire(*, timeout):
        timeouts.append(timeout)
        clock[0] += slot_elapsed
        return True

    def run(args, **kwargs):
        calls.append(kwargs)
        Path(args[-1]).write_text(json.dumps(["text", "pdf-text"]))

    monkeypatch.setattr(ocr, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(ocr, "_OCR_SLOTS", SimpleNamespace(acquire=acquire, release=lambda: released.append(True)))
    monkeypatch.setattr(ocr.subprocess, "run", run)
    result = ocr.extract_text(tmp_path / "receipt.pdf")
    assert timeouts == [10.0] and released == [True]
    if slot_elapsed < 10:
        assert calls[0]["timeout"] == 10.0 - slot_elapsed
        assert result == ("text", "pdf-text")
    else:
        assert calls == [] and result == ("", "none")


@pytest.mark.parametrize("state,phrase", [("warming", "preparing"), ("failed", "Restart Stowe"), ("ready", "Try Analyze again")])
def test_unreadable_image_api_is_explicit_and_preserves_receipt(client, monkeypatch, state, phrase):
    monkeypatch.setattr(ocr, "_WARMUP_STATE", state)
    monkeypatch.setattr(ocr, "extract_text", lambda _: ("", "none"))
    content = b"synthetic image receipt"
    files = {"file": ("receipt.png", content, "image/png")}
    preview = client.post("/api/v1/receipts/analyze", files=files)
    assert preview.status_code == 200
    assert preview.json()["status"] == "not_analyzed"
    assert preview.json()["amount"] is None
    assert phrase in preview.json()["notes"]
    expense = client.post("/api/v1/expenses", json={"merchant": "Synthetic Pharmacy", "date": "2026-01-15", "amount": 42.50}).json()
    uploaded = client.post(f"/api/v1/expenses/{expense['id']}/receipts", files=files)
    assert uploaded.status_code == 201
    after = client.get(f"/api/v1/expenses/{expense['id']}").json()
    assert after["auto_review_status"] == "not_analyzed"
    assert phrase in after["auto_review_notes"]
    for key in ("merchant", "date", "amount", "reimbursed", "reimbursed_date"):
        assert after[key] == expense[key]
    assert client.get(f"/api/v1/receipts/{uploaded.json()['id']}/file").content == content


def test_mixed_pdf_image_incomplete_is_not_successful(client, monkeypatch):
    monkeypatch.setattr(ocr, "_WARMUP_STATE", "warming")
    monkeypatch.setattr(ocr, "extract_text", lambda p: ("DENTAL CLEANING Total $42.50 01/15/2026", "pdf-text") if p.suffix == ".pdf" else ("", "none"))
    expense = client.post("/api/v1/expenses", json={"merchant": "Synthetic Dental", "date": "2026-01-15", "amount": 42.50}).json()
    for name, mime in [("receipt.pdf", "application/pdf"), ("receipt.png", "image/png")]:
        response = client.post(f"/api/v1/expenses/{expense['id']}/receipts", files={"file": (name, b"synthetic receipt", mime)})
        assert response.status_code == 201
    after = client.get(f"/api/v1/expenses/{expense['id']}").json()
    assert after["auto_review_status"] == "needs_review"
    assert "preparing" in after["auto_review_notes"]
    assert after["amount"] == 42.50
    assert after["date"] == "2026-01-15"
    assert after["reimbursed"] is False
    assert len(after["receipts"]) == 2


@pytest.mark.parametrize("raise_inside", [False, True])
def test_lifespan_starts_and_stops_warmup(monkeypatch, raise_inside):
    import main
    events = []
    monkeypatch.setattr(main, "init_db", lambda: events.append("database"))
    monkeypatch.setattr(ocr, "start_image_warmup", lambda: events.append("start"))
    monkeypatch.setattr(ocr, "stop_image_warmup", lambda: events.append("stop"))

    async def exercise():
        async with main.lifespan(main.app):
            assert events == ["database", "start"]
            if raise_inside:
                raise ValueError("synthetic")

    if raise_inside:
        with pytest.raises(ValueError):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    assert events == ["database", "start", "stop"]
