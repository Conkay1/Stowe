import pytest


@pytest.fixture(params=["upload", "analyze"])
def receipt_endpoint(client, request):
    response = client.post("/api/v1/expenses", json={
        "merchant": "Test Pharmacy",
        "date": "2026-01-15",
        "amount": 42.50,
        "category": "Pharmacy",
    })
    assert response.status_code == 201
    expense_id = response.json()["id"]
    if request.param == "upload":
        return f"/api/v1/expenses/{expense_id}/receipts", expense_id, 201
    return "/api/v1/receipts/analyze", expense_id, 200


@pytest.fixture
def ocr_reads(monkeypatch):
    from backend import ocr

    reads = []

    def extract_text(path):
        reads.append((path, path.read_bytes()))
        return "", "none"

    monkeypatch.setattr(ocr, "extract_text", extract_text)
    return reads


@pytest.mark.parametrize("filename", [
    "receipt.pdf",
    "x" * 196 + ".pdf",
    "x" * 197 + ".pdf",
    "x" * 300 + ".PDF",
    "領収書" * 100 + ".pdf",
    "../../" + "x" * 300 + ".pdf",
], ids=["short", "boundary", "over-boundary", "long-uppercase", "unicode", "path-like"])
def test_receipt_name_preserves_extension_and_bytes(
    client, receipt_endpoint, ocr_reads, filename
):
    from backend.routers import expenses

    endpoint, expense_id, expected_status = receipt_endpoint
    content = b"%PDF-1.4 synthetic receipt\n%%EOF\n"
    response = client.post(
        endpoint, files={"file": (filename, content, "application/pdf")}
    )
    assert response.status_code == expected_status, response.text
    assert len(ocr_reads) == 1
    path, read_content = ocr_reads[0]
    assert path.suffix == ".pdf"
    assert read_content == content
    expense = client.get(f"/api/v1/expenses/{expense_id}").json()
    if expected_status == 201:
        receipt = response.json()
        assert receipt["original_filename"] == filename.replace("/", "").replace("\\", "")
        assert receipt["file_size_bytes"] == len(content)
        assert path.parent == expenses.RECEIPTS_DIR
        assert len(path.stem) == 32
        assert all(char in "0123456789abcdef" for char in path.stem)
        assert list(expenses.RECEIPTS_DIR.iterdir()) == [path]
        download = client.get(f"/api/v1/receipts/{receipt['id']}/file")
        assert download.status_code == 200
        assert download.content == content
        assert [r["id"] for r in expense["receipts"]] == [receipt["id"]]
    else:
        assert not path.exists()
        assert not list(expenses.RECEIPTS_DIR.iterdir())
        assert expense["receipts"] == []


@pytest.mark.parametrize("filename", [
    "receipt.exe",
    "x" * 196 + ".pdf.exe",
    "receipt.pdf/unsupported",
], ids=["unsupported", "hidden-suffix", "directory-suffix"])
def test_receipt_rejects_unsupported_final_extension(
    client, receipt_endpoint, ocr_reads, filename
):
    from backend.routers import expenses

    endpoint, expense_id, _ = receipt_endpoint
    response = client.post(
        endpoint, files={"file": (filename, b"synthetic", "application/pdf")}
    )
    assert response.status_code == 400, response.text
    assert "Unsupported file type" in response.json()["detail"]
    assert ocr_reads == []
    assert not list(expenses.RECEIPTS_DIR.iterdir())
    assert client.get(f"/api/v1/expenses/{expense_id}").json()["receipts"] == []


class _Upload:
    def __init__(self, filename, content_type="application/pdf"):
        self.filename = filename
        self.content_type = content_type


@pytest.mark.parametrize(("filename", "ext", "display"), [
    ("safe\x00name.PDF", ".pdf", "safename.PDF"),
    ("..\x00/..\\=cmd|.pdf", ".pdf", "....=cmd|.pdf"),
    ("\t@sum.pdf", ".pdf", "@sum.pdf"),
    ("\r-minus.pdf", ".pdf", "-minus.pdf"),
    ("folder/sub\\receipt.pdf", ".pdf", "foldersubreceipt.pdf"),
    ("\x7f\x1fname.pdf", ".pdf", "name.pdf"),
    ("領収書.pdf", ".pdf", "領収書.pdf"),
])
def test_display_name_strips_controls_and_separators_keeps_raw_extension(
    client, filename, ext, display
):
    from backend.routers import expenses

    content = b"%PDF-1.4 synthetic receipt\n%%EOF\n"
    got_content, got_ext, got_name = expenses._read_validated_upload(
        _Upload(filename), content
    )
    assert got_content == content
    assert got_ext == ext
    assert got_name == display
    assert "/" not in got_name and "\\" not in got_name


def test_extension_allowlist_sees_the_raw_suffix_before_display_cleanup(client):
    from fastapi import HTTPException
    from backend.routers import expenses

    content = b"%PDF-1.4 synthetic receipt\n%%EOF\n"
    # A trailing NUL is part of the raw suffix, so this is not ".pdf".
    with pytest.raises(HTTPException) as rejected:
        expenses._read_validated_upload(_Upload("receipt.pdf\x00"), content)
    assert rejected.value.status_code == 400
    with pytest.raises(HTTPException) as hidden:
        expenses._read_validated_upload(_Upload("receipt.pdf\x00.exe"), content)
    assert hidden.value.status_code == 400


def test_upload_stores_sanitized_display_name_and_preserves_extension(
    client, ocr_reads
):
    from backend.routers import expenses

    expense = client.post("/api/v1/expenses", json={
        "merchant": "Synthetic Clinic", "date": "2026-01-15", "amount": 12.5,
    })
    assert expense.status_code == 201
    expense_id = expense.json()["id"]
    content = b"%PDF-1.4 synthetic receipt\n%%EOF\n"
    filename = "..\\..\\folder/" + "x" * 300 + ".PDF"
    uploaded = client.post(
        f"/api/v1/expenses/{expense_id}/receipts",
        files={"file": (filename, content, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    receipt = uploaded.json()
    assert receipt["original_filename"] == "....folder" + "x" * 300 + ".PDF"
    assert "/" not in receipt["original_filename"]
    assert "\\" not in receipt["original_filename"]
    path, read_content = ocr_reads[0]
    assert path.suffix == ".pdf"
    assert read_content == content
    assert path.parent == expenses.RECEIPTS_DIR
    assert len(path.stem) == 32
    download = client.get(f"/api/v1/receipts/{receipt['id']}/file")
    assert download.status_code == 200
    assert download.content == content


def test_csv_export_neutralizes_formula_prefixes(client, ocr_reads):
    import csv
    import io

    from backend import db as db_module
    from backend.models import HSAExpense

    dangerous = client.post("/api/v1/expenses", json={
        "merchant": "=Clinic",
        "date": "2026-03-01",
        "amount": 12.5,
        "category": "+Visit",
        "notes": "@keep, this\nline",
    })
    assert dangerous.status_code == 201
    expense_id = dangerous.json()["id"]
    content = b"%PDF-1.4 synthetic receipt\n%%EOF\n"
    for name in ("ok.pdf", "=cmd.pdf"):
        uploaded = client.post(
            f"/api/v1/expenses/{expense_id}/receipts",
            files={"file": (name, content, "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        assert uploaded.json()["original_filename"] == name

    controls = client.post("/api/v1/expenses", json={
        "merchant": "\r=cmd",
        "date": "2026-03-02",
        "amount": 8,
        "category": "\t@cat",
        "notes": "\r+follow up",
    })
    assert controls.status_code == 201

    negative = client.post("/api/v1/expenses", json={
        "merchant": "Safe Clinic",
        "date": "2026-03-03",
        "amount": 5,
        "category": "Pharmacy",
        "notes": "plain",
    })
    assert negative.status_code == 201
    with db_module.SessionLocal() as session:
        session.get(HSAExpense, negative.json()["id"]).amount = -5
        session.commit()

    exported = client.get("/api/v1/export/csv")
    assert exported.status_code == 200
    rows = list(csv.reader(io.StringIO(exported.text)))
    assert rows[0] == [
        "Date", "Merchant", "Category", "Amount",
        "Reimbursed", "Reimbursed Date", "Receipt Count", "Receipt Filenames", "Notes",
    ]
    by_date = {row[0]: row for row in rows[1:]}

    first = by_date["2026-03-01"]
    assert first[1] == "'=Clinic"
    assert first[2] == "'+Visit"
    assert first[3] == "12.50"
    assert first[4] == "No"
    assert first[6] == "2"
    assert first[7] == "ok.pdf; '=cmd.pdf"
    assert first[8] == "'@keep, this line"

    second = by_date["2026-03-02"]
    assert second[1] == "'\r=cmd"
    assert second[2] == "'\t@cat"
    assert second[8] == "'+follow up"

    third = by_date["2026-03-03"]
    assert third[1] == "Safe Clinic"
    assert third[2] == "Pharmacy"
    assert third[3] == "'-5.00"
    assert third[8] == "plain"
    assert ocr_reads
