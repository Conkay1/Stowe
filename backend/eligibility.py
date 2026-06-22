"""Rule-based HSA-eligibility classifier + receipt field extraction.

Pure Python, no native dependencies — fully unit-testable without OCR. Given the
raw text of a receipt, this guesses the merchant / date / amount / category and
produces an *advisory* eligibility hint. It is deliberately conservative and
never asserts that an item is or isn't legally HSA-qualified — the wording is a
hint and every summary points the user at IRS Publication 502.
"""
import datetime as _dt
import re
from typing import Optional

from config import HSA_CATEGORIES

# ── Keyword data ─────────────────────────────────────────────────────────────
# Lowercase canonical forms. Matching is case-insensitive + word-boundary.

# HSA-eligible signals: OTC meds/supplies/services that are clearly medical.
# (The CARES Act, 2020, made most OTC drugs and menstrual products eligible.)
ELIGIBLE_KEYWORDS: list[str] = [
    # Pharmacy / OTC meds
    "prescription", "rx", "copay", "co-pay", "ibuprofen", "acetaminophen",
    "tylenol", "advil", "motrin", "aspirin", "antacid", "antihistamine",
    "allergy", "claritin", "zyrtec", "flonase", "cough", "cold medicine",
    "decongestant", "naproxen", "aleve", "pepto", "tums", "imodium",
    # Supplies / equipment
    "bandage", "band-aid", "first aid", "thermometer", "blood pressure",
    "glucose", "test strips", "lancets", "insulin", "syringe", "nebulizer",
    "crutches", "wheelchair", "orthotic", "compression", "kt tape",
    "contact lens", "contact lenses", "contact solution", "eyeglasses",
    "reading glasses", "hearing aid", "cpap", "knee brace", "wrist brace",
    "pregnancy test", "prenatal",
    # Menstrual / now-eligible OTC
    "tampons", "pads", "menstrual", "feminine care",
    # Sun / skin (medical)
    "sunscreen", "spf",
    # Services
    "office visit", "consultation", "examination", "x-ray", "lab work",
    "vaccination", "vaccine", "flu shot", "immunization", "physical therapy",
    "therapy", "cleaning", "filling", "crown", "root canal", "extraction",
    "eye exam", "vision exam", "dental", "orthodontic", "diagnosis", "mri",
    "ultrasound", "deductible",
]

# Clearly NOT eligible without a Letter of Medical Necessity: toiletries, food,
# cosmetics, general merchandise.
INELIGIBLE_KEYWORDS: list[str] = [
    "shampoo", "conditioner", "soap", "body wash", "deodorant",
    "toothpaste", "toothbrush", "mouthwash", "perfume", "cologne",
    "makeup", "lipstick", "mascara", "cosmetic", "nail polish",
    "grocery", "groceries", "snack", "candy", "soda", "beverage",
    "coffee", "beer", "wine", "alcohol", "cigarette", "tobacco",
    "vitamins", "supplement", "multivitamin", "protein powder",
    "gum", "chips", "milk", "bread", "produce", "magazine",
    "gift card", "greeting card", "clothing", "shoes", "battery",
]

# Merchant-name signals → strong "this is a medical vendor" prior.
MEDICAL_MERCHANT_KEYWORDS: list[str] = [
    "pharmacy", "drugstore", "cvs", "walgreens", "rite aid", "duane reade",
    "clinic", "medical center", "hospital", "health", "healthcare",
    "urgent care", "physician", "dental", "dentist", "orthodontics",
    "endodontics", "vision", "optical", "optometry", "ophthalmology",
    "eye care", "lenscrafters", "pearle vision", "warby parker",
    "quest diagnostics", "labcorp", "imaging", "radiology",
    "physical therapy", "chiropractic", "dermatology", "pediatrics",
    "psychiatry", "psychology", "counseling", "behavioral health",
    "kaiser", "cleveland clinic", "mayo clinic",
]

# keyword (prefix-ok) → HSA category. First match wins, over (merchant + text).
CATEGORY_HINTS: list[tuple[str, str]] = [
    ("pharmacy", "Pharmacy"), ("drugstore", "Pharmacy"), ("rx", "Pharmacy"),
    ("prescription", "Pharmacy"), ("cvs", "Pharmacy"), ("walgreens", "Pharmacy"),
    ("rite aid", "Pharmacy"),
    ("dental", "Dental"), ("dentist", "Dental"), ("orthodont", "Dental"),
    ("crown", "Dental"), ("root canal", "Dental"), ("filling", "Dental"),
    ("vision", "Vision"), ("optical", "Vision"), ("optometr", "Vision"),
    ("eye exam", "Vision"), ("eyeglasses", "Vision"), ("contact lens", "Vision"),
    ("lenscrafters", "Vision"), ("ophthalmolog", "Vision"),
    ("psychiatry", "Mental Health"), ("psycholog", "Mental Health"),
    ("counseling", "Mental Health"), ("therapy", "Mental Health"),
    ("behavioral health", "Mental Health"), ("mental health", "Mental Health"),
    ("wheelchair", "Medical Equipment"), ("crutches", "Medical Equipment"),
    ("cpap", "Medical Equipment"), ("nebulizer", "Medical Equipment"),
    ("blood pressure", "Medical Equipment"), ("hearing aid", "Medical Equipment"),
    ("brace", "Medical Equipment"), ("orthotic", "Medical Equipment"),
    ("clinic", "Medical"), ("hospital", "Medical"), ("urgent care", "Medical"),
    ("physician", "Medical"), ("office visit", "Medical"),
]

# Guard: every CATEGORY_HINTS target must be a real category.
assert all(cat in HSA_CATEGORIES for _, cat in CATEGORY_HINTS)


# ── Matching primitives ──────────────────────────────────────────────────────

def _compile(keywords: list[str]) -> list[tuple[str, "re.Pattern[str]"]]:
    # (?<!\w)…(?!\w) is a word-boundary that also behaves for hyphenated/multi-word
    # terms ("co-pay", "band-aid", "first aid") where \b misbehaves.
    return [
        (kw, re.compile(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", re.IGNORECASE))
        for kw in keywords
    ]


_ELIGIBLE = _compile(ELIGIBLE_KEYWORDS)
_INELIGIBLE = _compile(INELIGIBLE_KEYWORDS)
_MERCHANT = _compile(MEDICAL_MERCHANT_KEYWORDS)


def _find_matches(text: str, compiled: list[tuple[str, "re.Pattern[str]"]]) -> list[str]:
    return sorted({kw for kw, pat in compiled if pat.search(text)})


# ── Field extraction ─────────────────────────────────────────────────────────

# Matches $1,234.56 / 1234.56 / 12.00 — requires 2 decimals so it doesn't grab
# quantities, ZIP codes, or phone numbers.
_AMOUNT_RE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})")
_GRAND_TOTAL_HINTS = ("grand total", "total due", "amount due", "balance due", "total:")
_TOTAL_HINTS = ("total", "balance")
_DEMOTE_HINTS = ("subtotal", "sub total", "tax", "change", "tendered", "cash", "tip")


def _extract_amount(text: str) -> Optional[float]:
    """Pick the most total-like currency amount. Higher-priority lines win;
    within a tier the larger value wins (a grand total >= line items)."""
    best: Optional[tuple[int, float]] = None
    for raw_line in text.splitlines():
        line = raw_line.lower()
        amounts = [float(m.replace(",", "")) for m in _AMOUNT_RE.findall(raw_line)]
        if not amounts:
            continue
        val = max(amounts)
        is_grand = any(h in line for h in _GRAND_TOTAL_HINTS)
        if is_grand:
            prio = 3
        elif any(h in line for h in _DEMOTE_HINTS):
            prio = 0
        elif any(h in line for h in _TOTAL_HINTS):
            prio = 2
        else:
            prio = 1
        if best is None or prio > best[0] or (prio == best[0] and val > best[1]):
            best = (prio, val)
    return round(best[1], 2) if best else None


_DATE_PATTERNS: list[tuple["re.Pattern[str]", str]] = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),              # 2026-01-15
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "mdy"),         # 01/15/2026
    (re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b"), "mdy"),         # 01-15-2026
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b"), "mdy2"),        # 01/15/26
    (re.compile(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
        r"(\d{1,2}),?\s+(\d{4})\b", re.IGNORECASE), "mon"),          # Jan 15, 2026
]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _extract_date(text: str) -> Optional[str]:
    today = _dt.date.today()
    candidates: list[_dt.date] = []
    for pat, kind in _DATE_PATTERNS:
        for m in pat.finditer(text):
            try:
                if kind == "ymd":
                    d = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                elif kind == "mon":
                    d = _dt.date(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
                elif kind == "mdy2":
                    d = _dt.date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
                else:  # mdy
                    d = _dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            except (ValueError, KeyError):
                continue
            # Ignore future dates (expiry/artifacts) and pre-2000 noise.
            if _dt.date(2000, 1, 1) <= d <= today:
                candidates.append(d)
    if not candidates:
        return None
    return max(candidates).isoformat()


def _extract_merchant(text: str, merchant_hits: list[str]) -> Optional[str]:
    if merchant_hits:
        return merchant_hits[0]
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) < 3:
            continue
        # Skip lines that are mostly digits/punctuation (addresses, phones, totals).
        alpha = sum(c.isalpha() for c in line)
        if alpha >= 3 and alpha / len(line) > 0.5:
            return line[:80]
    return None


def _extract_category(text: str, merchant: Optional[str]) -> Optional[str]:
    hay = ((merchant or "") + "\n" + text).lower()
    for kw, cat in CATEGORY_HINTS:
        # prefix-ok match (e.g. "optometr" matches "optometry"/"optometrist")
        if re.search(r"(?<!\w)" + re.escape(kw), hay):
            return cat
    return None


# ── Classification ───────────────────────────────────────────────────────────

_STATUS_PHRASE = {
    "eligible":     "Likely HSA-eligible",
    "needs_review": "Review suggested",
    "ineligible":   "Doesn't look HSA-eligible",
    "not_analyzed": "Couldn't analyze",
}
_DISCLAIMER = "This is an automated hint, not tax advice — verify against IRS Publication 502."


def _classify(eligible: list[str], ineligible: list[str],
              merchant_hits: list[str]) -> tuple[str, str]:
    med = bool(merchant_hits)
    if eligible and not ineligible:
        return "eligible", ("high" if med else "medium")
    if eligible and ineligible:
        return "needs_review", "low"
    if not eligible and ineligible:
        return ("needs_review", "low") if med else ("ineligible", "medium")
    # no eligible, no ineligible
    return ("needs_review", "medium") if med else ("needs_review", "low")


def _summary(status: str, eligible: list[str], ineligible: list[str],
             merchant_hits: list[str]) -> str:
    bits = [_STATUS_PHRASE[status] + "."]
    if merchant_hits:
        bits.append(f"Looks like a medical vendor ({', '.join(merchant_hits[:3])}).")
    if eligible:
        bits.append(f"Found eligible items: {', '.join(eligible[:5])}.")
    if ineligible:
        bits.append(f"Found possibly-ineligible items: {', '.join(ineligible[:5])}.")
    bits.append(_DISCLAIMER)
    return " ".join(bits)


def analyze_text(text: str) -> dict:
    """Classify receipt text and extract fields. Pure, never raises.

    Returns a dict matching the ReceiptAnalysisOut schema (minus `method`,
    which the caller fills in from the OCR step).
    """
    if not text or not text.strip():
        return {
            "status": "not_analyzed", "confidence": "low",
            "matched_eligible": [], "matched_ineligible": [],
            "merchant": None, "date": None, "amount": None, "category": None,
            "notes": "No readable text found on the receipt. " + _DISCLAIMER,
        }

    eligible = _find_matches(text, _ELIGIBLE)
    ineligible = _find_matches(text, _INELIGIBLE)
    merchant_hits = _find_matches(text, _MERCHANT)

    merchant = _extract_merchant(text, merchant_hits)
    date = _extract_date(text)
    amount = _extract_amount(text)
    category = _extract_category(text, merchant)

    status, confidence = _classify(eligible, ineligible, merchant_hits)
    notes = _summary(status, eligible, ineligible, merchant_hits)

    return {
        "status": status, "confidence": confidence,
        "matched_eligible": eligible, "matched_ineligible": ineligible,
        "merchant": merchant, "date": date, "amount": amount,
        "category": category, "notes": notes,
    }
