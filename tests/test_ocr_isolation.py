import os
import socket
import sys
import threading
import time

import httpx
import pytest
import uvicorn

from backend import ocr


def test_worker_timeout_kills_and_reaps_process(tmp_path, monkeypatch):
    processes = []
    popen = ocr.subprocess.Popen

    def track_process(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(ocr.subprocess, "Popen", track_process)
    pid_path = tmp_path / "pid"
    late_path = tmp_path / "late"
    worker = tmp_path / "stall.py"
    worker.write_text(
        "import os, pathlib, time\n"
        f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(1)\n"
        f"pathlib.Path({str(late_path)!r}).write_text('late')\n"
    )
    monkeypatch.setattr(ocr, "OCR_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(ocr, "_worker_command", lambda *_: [sys.executable, str(worker)])
    assert ocr.extract_text(tmp_path / "receipt.pdf") == ("", "none")
    pid = int(pid_path.read_text())
    assert len(processes) == 1
    assert processes[0].pid == pid
    assert processes[0].returncode is not None
    if sys.platform != "win32":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
    assert not late_path.exists()


def test_exhausted_worker_slots_fail_without_starting_another_process(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "OCR_TIMEOUT_SECONDS", 0.01)
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(ocr, "_OCR_SLOTS", slots)
    assert slots.acquire(blocking=False)
    assert slots.acquire(blocking=False)

    def unexpected_worker(*_):
        pytest.fail("No worker may start while both OCR slots are occupied")

    monkeypatch.setattr(ocr, "_worker_command", unexpected_worker)
    try:
        assert ocr.extract_text(tmp_path / "receipt.pdf") == ("", "none")
    finally:
        slots.release()
        slots.release()


def test_source_worker_extracts_text_without_starting_app(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 700 Td (Synthetic pharmacy receipt 42.50) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "receipt.pdf"
    writer.write(path)
    text, method = ocr.extract_text(path)
    assert "Synthetic pharmacy receipt 42.50" in text
    assert method == "pdf-text"


@pytest.mark.parametrize("result", ["null", '[{}, "vision"]', '["text", "bad"]', 'invalid'])
def test_invalid_worker_result_is_unavailable(tmp_path, monkeypatch, result):
    worker = tmp_path / "result.py"
    worker.write_text(f"import pathlib, sys\npathlib.Path(sys.argv[1]).write_text({result!r})")
    monkeypatch.setattr(ocr, "_worker_command", lambda _p, out: [sys.executable, str(worker), str(out)])
    assert ocr.extract_text(tmp_path / "receipt.pdf") == ("", "none")


def test_frozen_worker_dispatch_uses_bundled_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command = ocr._worker_command(tmp_path / "r.png", tmp_path / "result.json")
    assert command == [sys.executable, "--stowe-ocr-worker", str(tmp_path / "r.png"), str(tmp_path / "result.json")]


@pytest.fixture
def live_api(client):
    from main import app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", loop="asyncio", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=1, trust_env=False) as api:
            yield api
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


def make_expense(api):
    response = api.post("/expenses", json={
        "merchant": "Synthetic Pharmacy", "date": "2026-01-15",
        "amount": 42.50, "category": "Pharmacy",
    })
    assert response.status_code == 201
    return response.json()


def test_stalled_review_does_not_block_upload_or_reads(live_api, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def stall(_path):
        started.set()
        assert release.wait(4)
        return "", "none"

    monkeypatch.setattr(ocr, "extract_text", stall)
    expense = make_expense(live_api)
    content = b"\x89PNG\r\n\x1a\n" + b"synthetic receipt"
    try:
        response = live_api.post(f"/expenses/{expense['id']}/receipts", files={"file": ("r.png", content, "image/png")})
        assert response.status_code == 201
        assert started.wait(1)
        receipt = response.json()
        assert live_api.get(f"/receipts/{receipt['id']}/file").content == content
        current = live_api.get(f"/expenses/{expense['id']}").json()
        assert current["amount"] == expense["amount"]
        assert current["reimbursed"] is False
        assert len(current["receipts"]) == 1
    finally:
        release.set()


def test_stalled_preview_does_not_block_reads(live_api, monkeypatch):
    started, release = threading.Event(), threading.Event()
    result = []

    def stall(_path):
        started.set()
        assert release.wait(4)
        return "", "none"

    def request():
        with httpx.Client(base_url=str(live_api.base_url), timeout=5, trust_env=False) as api:
            result.append(api.post("/receipts/analyze", files={"file": ("r.pdf", b"%PDF-fake", "application/pdf")}))

    monkeypatch.setattr(ocr, "extract_text", stall)
    thread = threading.Thread(target=request)
    thread.start()
    try:
        assert started.wait(1)
        assert live_api.get("/expenses").status_code == 200
    finally:
        release.set()
        thread.join(timeout=5)
    assert result[0].status_code == 200


@pytest.mark.parametrize("change", ["add", "remove", "replace", "newer_review", "delete_expense"])
def test_stale_review_cannot_overwrite_changed_receipts(client, monkeypatch, change):
    from backend import db as db_module
    from backend.models import HSAExpense, Receipt

    expense = client.post("/api/v1/expenses", json={
        "merchant": "Synthetic Pharmacy", "date": "2026-01-15", "amount": 42.50,
    }).json()
    monkeypatch.setattr(ocr, "extract_text", lambda _: ("", "none"))
    receipt = client.post(f"/api/v1/expenses/{expense['id']}/receipts", files={
        "file": ("r.pdf", b"%PDF-fake", "application/pdf"),
    }).json()

    def change_receipts(_path):
        with db_module.SessionLocal() as db:
            item = db.get(HSAExpense, expense["id"])
            if change == "delete_expense":
                db.delete(item)
            else:
                item.auto_review_status = "needs_review"
                item.auto_review_notes = "Newer analysis"
                if change == "add":
                    db.add(Receipt(expense_id=item.id, filename="new.pdf"))
                elif change == "replace":
                    db.get(Receipt, receipt["id"]).filename = "replacement.pdf"
                elif change == "newer_review":
                    item.auto_review_metadata = '{"newer": true}'
                else:
                    db.delete(db.get(Receipt, receipt["id"]))
            db.commit()
        return "DENTAL CLEANING", "pdf-text"

    monkeypatch.setattr(ocr, "extract_text", change_receipts)
    from backend.routers.expenses import _review_expense_after_upload
    _review_expense_after_upload(expense["id"])
    with db_module.SessionLocal() as db:
        item = db.get(HSAExpense, expense["id"])
        if change == "delete_expense":
            assert item is None
        else:
            assert item.auto_review_status == "needs_review"
            assert item.auto_review_notes == "Newer analysis"
            assert item.amount == 42.50


@pytest.mark.parametrize("change", ["amount", "date", "auto_review_status", "auto_review_notes"])
def test_stale_review_cannot_overwrite_expense_context(client, monkeypatch, change):
    from datetime import date

    from backend import db as db_module
    from backend.models import HSAExpense
    from backend.routers.expenses import _review_expense_after_upload

    expense = client.post("/api/v1/expenses", json={
        "merchant": "Synthetic Clinic", "date": "2026-01-15", "amount": 25.00,
    }).json()
    monkeypatch.setattr(ocr, "extract_text", lambda _: ("", "none"))
    uploaded = client.post(f"/api/v1/expenses/{expense['id']}/receipts", files={
        "file": ("receipt.pdf", b"%PDF-synthetic", "application/pdf"),
    })
    assert uploaded.status_code == 201
    before = client.get(f"/api/v1/expenses/{expense['id']}").json()
    changes = {
        "amount": 30.00,
        "date": date(2026, 2, 1),
        "auto_review_status": "needs_review",
        "auto_review_notes": "Newer review note",
    }

    def edit_during_ocr(_path):
        with db_module.SessionLocal() as db:
            item = db.get(HSAExpense, expense["id"])
            setattr(item, change, changes[change])
            db.commit()
        return "Synthetic Clinic\nOffice visit\nTotal $25.00\n2026-01-15", "pdf-text"

    monkeypatch.setattr(ocr, "extract_text", edit_during_ocr)
    _review_expense_after_upload(expense["id"])

    after = client.get(f"/api/v1/expenses/{expense['id']}").json()
    changed_value = changes[change]
    if isinstance(changed_value, date):
        changed_value = changed_value.isoformat()
    expected = {**before, change: changed_value}
    if change == "amount":
        expected["remaining_amount"] = changed_value
    assert after == expected
