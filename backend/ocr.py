"""Local, on-device text extraction from receipt files.

No cloud — ever. Two backends:
  * PDFs (all platforms): pypdf, for digital/text-layer PDFs.
  * Images (macOS only): Apple's Vision framework via PyObjC.

`extract_text` NEVER raises — any failure degrades to ("", "none"). All native
(PyObjC) imports are lazy and guarded behind `sys.platform == "darwin"`, so this
module imports cleanly on every platform and under pytest where PyObjC is absent.
"""
import atexit
import json
import hashlib
import logging
import subprocess
import tempfile
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("stowe.ocr")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
PDF_EXTS = {".pdf"}

_IS_MACOS = sys.platform == "darwin"
OCR_TIMEOUT_SECONDS = 10.0
OCR_WARMUP_TIMEOUT_SECONDS = 75.0
_OCR_SLOTS = threading.BoundedSemaphore(2)
_WARMUP_LOCK = threading.Lock()
_WARMUP_CANCEL = threading.Event()
_WARMUP_STATE = "not_started"
_WARMUP_THREAD = None
_WARMUP_PROCESS = None
_WARMUP_IMAGE_SHA256 = "5ea7680196176bdd1d1d64b7ebf50bbc84cd31eb7f091e9795353861e5b3cc34"


def image_ocr_notice() -> str | None:
    if not _IS_MACOS:
        return "Image recognition is available on macOS. Verify this receipt manually."
    with _WARMUP_LOCK:
        state = _WARMUP_STATE
    if state == "ready":
        return None
    if state in {"not_started", "warming"}:
        return "Local image recognition is preparing. Try Analyze again shortly."
    return "Local image recognition is unavailable. Restart Wellslip Desktop to retry, or verify this receipt manually."


def start_image_warmup() -> bool:
    global _WARMUP_STATE, _WARMUP_THREAD
    if not _IS_MACOS:
        return False
    with _WARMUP_LOCK:
        if _WARMUP_STATE != "not_started":
            return False
        _WARMUP_STATE = "warming"
        _WARMUP_THREAD = threading.Thread(
            target=_warm_image_worker,
            args=(time.monotonic() + OCR_WARMUP_TIMEOUT_SECONDS,),
            name="stowe-image-warmup",
            daemon=True,
        )
        atexit.register(stop_image_warmup)
        try:
            _WARMUP_THREAD.start()
        except RuntimeError:
            _WARMUP_THREAD = None
            _WARMUP_STATE = "failed"
            logger.warning("Local image recognition startup did not start", exc_info=False)
            return False
    return True


def _warm_image_worker(deadline: float) -> None:
    global _WARMUP_STATE, _WARMUP_PROCESS
    acquired = False
    process = None
    ready = False
    try:
        while not _WARMUP_CANCEL.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            if _OCR_SLOTS.acquire(timeout=min(0.1, remaining)):
                acquired = True
                break
        if not acquired or _WARMUP_CANCEL.is_set():
            return
        image = Path(__file__).resolve().parents[1] / "assets" / "ocr-warmup.png"
        if hashlib.sha256(image.read_bytes()).hexdigest() != _WARMUP_IMAGE_SHA256:
            return
        with tempfile.TemporaryDirectory(prefix="stowe-ocr-warmup-") as folder:
            result_path = Path(folder) / "result.json"
            with _WARMUP_LOCK:
                if _WARMUP_CANCEL.is_set():
                    return
                process = subprocess.Popen(
                    ["/usr/bin/nice", "-n", "10", *_worker_command(image, result_path)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                _WARMUP_PROCESS = process
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                process.wait(timeout=remaining)
                if process.returncode != 0 or time.monotonic() >= deadline:
                    return
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if (
                    isinstance(result, list) and len(result) == 2
                    and isinstance(result[0], str) and result[1] == "vision"
                ):
                    normalized = " ".join(result[0].split()).upper()
                    ready = all(token in normalized for token in (
                        "SYNTHETIC PHARMACY", "RECEIPT FOR TEST ONLY", "42.50",
                    ))
            finally:
                if process.poll() is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                process.wait(timeout=2)
    except Exception:
        logger.warning("Local image recognition startup did not complete", exc_info=False)
    finally:
        if acquired:
            _OCR_SLOTS.release()
        with _WARMUP_LOCK:
            _WARMUP_PROCESS = None
            _WARMUP_STATE = "stopped" if _WARMUP_CANCEL.is_set() else ("ready" if ready else "failed")


def stop_image_warmup() -> None:
    _WARMUP_CANCEL.set()
    with _WARMUP_LOCK:
        process = _WARMUP_PROCESS
        thread = _WARMUP_THREAD
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    if thread is not None:
        thread.join(timeout=2)
        if thread.is_alive():
            with _WARMUP_LOCK:
                process = _WARMUP_PROCESS
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            thread.join(timeout=2)


def _worker_command(path: Path, result_path: Path) -> list[str]:
    entry = [] if getattr(sys, "frozen", False) else [str(Path(__file__).resolve().parents[1] / "run.py")]
    return [sys.executable, *entry, "--stowe-ocr-worker", str(path.resolve()), str(result_path)]


def extract_text(path: Path) -> tuple[str, str]:
    if path.suffix.lower() not in PDF_EXTS | IMAGE_EXTS:
        return "", "none"
    if path.suffix.lower() in IMAGE_EXTS and not _IS_MACOS:
        return "", "none"
    if path.suffix.lower() in IMAGE_EXTS and image_ocr_notice() is not None:
        return "", "none"
    deadline = time.monotonic() + OCR_TIMEOUT_SECONDS
    if not _OCR_SLOTS.acquire(timeout=max(0, deadline - time.monotonic())):
        return "", "none"
    try:
        with tempfile.TemporaryDirectory(prefix="stowe-ocr-") as folder:
            result_path = Path(folder) / "result.json"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "", "none"
            subprocess.run(
                _worker_command(path, result_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=remaining,
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
