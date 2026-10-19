"""Local, on-device text extraction from receipt files.

No cloud — ever. Two backends:
  * PDFs (all platforms): pypdf, for digital/text-layer PDFs.
  * Images (macOS only): Apple's Vision framework via PyObjC.

`extract_text` NEVER raises — any failure degrades to ("", "none"). All native
(PyObjC) imports are lazy and guarded behind `sys.platform == "darwin"`, so this
module imports cleanly on every platform and under pytest where PyObjC is absent.
"""
import json
import logging
import subprocess
import tempfile
import sys
import threading
from pathlib import Path

logger = logging.getLogger("stowe.ocr")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
PDF_EXTS = {".pdf"}

_IS_MACOS = sys.platform == "darwin"
OCR_TIMEOUT_SECONDS = 10.0
_OCR_SLOTS = threading.BoundedSemaphore(2)


def _worker_command(path: Path, result_path: Path) -> list[str]:
    entry = [] if getattr(sys, "frozen", False) else [str(Path(__file__).resolve().parents[1] / "run.py")]
    return [sys.executable, *entry, "--stowe-ocr-worker", str(path.resolve()), str(result_path)]


def extract_text(path: Path) -> tuple[str, str]:
    if path.suffix.lower() not in PDF_EXTS | IMAGE_EXTS:
        return "", "none"
    if path.suffix.lower() in IMAGE_EXTS and not _IS_MACOS:
        return "", "none"
    if not _OCR_SLOTS.acquire(timeout=OCR_TIMEOUT_SECONDS):
        return "", "none"
    try:
        with tempfile.TemporaryDirectory(prefix="stowe-ocr-") as folder:
            result_path = Path(folder) / "result.json"
            subprocess.run(
                _worker_command(path, result_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=OCR_TIMEOUT_SECONDS,
                check=True,
            )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if (
                isinstance(result, list) and len(result) == 2
                and isinstance(result[0], str)
                and result[1] in {"pdf-text", "vision", "none"}
            ):
                return result[0], result[1]
    except Exception:
        logger.warning("Receipt text extraction unavailable", exc_info=False)
    finally:
        _OCR_SLOTS.release()
    return "", "none"


def worker_main(args: list[str]) -> int:
    if len(args) != 2:
        return 2
    path, result_path = map(Path, args)
    result_path.write_text(json.dumps(_extract_text_local(path)), encoding="utf-8")
    return 0


def _extract_text_local(path: Path) -> tuple[str, str]:
    """Return (text, method) where method is "pdf-text" | "vision" | "none".

    Run only in the disposable OCR worker.
    """
    ext = path.suffix.lower()
    try:
        if ext in PDF_EXTS:
            text = _extract_pdf_text(path)
            if text.strip():
                return text, "pdf-text"
            # Scanned / image-only PDF — OCR-via-rasterize is a deferred follow-up.
            return "", "none"
        if ext in IMAGE_EXTS:
            if not _IS_MACOS:
                return "", "none"          # graceful skip off-macOS
            text = _vision_ocr_image(path)
            return (text, "vision") if text.strip() else ("", "none")
    except Exception:                       # defensive — extract_text must never raise
        logger.exception("extract_text failed for %s", path)
    return "", "none"


def _extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader            # lazy: keep import cost off the hot path
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")          # many "encrypted" PDFs use an empty owner pw
            except Exception:
                return ""
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue                    # skip a bad page, keep the rest
        return "\n".join(parts)
    except Exception:
        logger.exception("pypdf failed for %s", path)
        return ""


def _vision_ocr_image(path: Path) -> str:
    """OCR an image with Apple Vision. macOS only — all imports are local."""
    import objc
    import Quartz
    import Vision
    from Foundation import NSURL

    # Vision/ImageIO allocate many autoreleased objects; on a uvicorn worker
    # thread there's no run-loop draining a pool, so wrap the whole body.
    with objc.autorelease_pool():
        url = NSURL.fileURLWithPath_(str(path))

        # Decode the file (jpg/png/webp/heic/heif) → CGImage via ImageIO.
        src = Quartz.CGImageSourceCreateWithURL(url, None)
        if src is None or Quartz.CGImageSourceGetCount(src) < 1:
            return ""
        cg_image = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
        if cg_image is None:
            return ""

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            cg_image, None
        )
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)

        # ObjC `(BOOL)performRequests:error:` → Python (ok, err) tuple; pass None
        # for the out-error slot.
        ok, err = handler.performRequests_error_([request], None)
        if not ok or err is not None:
            return ""

        lines = []
        for obs in (request.results() or []):
            candidates = obs.topCandidates_(1)
            if candidates and len(candidates) > 0:
                lines.append(candidates[0].string())
        return "\n".join(lines)
