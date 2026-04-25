"""Shared PAN / Aadhaar / phone validation for onboarding, daily reports,
and attendance. Lives outside `routers/` so non-router code can import it
without pulling in FastAPI route state.

PAN: 5 letters + 4 digits + 1 letter (e.g. ABCDE1234F).
Aadhaar: 12 digits.
Indian mobile (10-digit): leading 6-9.

Inputs are stripped of whitespace, dots, and dashes before matching, so users
can paste IDs with their preferred separators.
"""
import re

from fastapi import HTTPException

PAN_RE     = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
AADHAAR_RE = re.compile(r"^\d{12}$")
PHONE_RE   = re.compile(r"^[6-9]\d{9}$")

ID_ERROR = "ID must be a 10-character PAN (ABCDE1234F) or a 12-digit Aadhaar"


def normalise_id(raw: str) -> str:
    return re.sub(r"[\s.\-]+", "", raw or "").upper()


def validate_id(value: str) -> None:
    """Raise 400 if `value` (already normalised) is not PAN or Aadhaar."""
    if not (PAN_RE.match(value) or AADHAAR_RE.match(value)):
        raise HTTPException(400, ID_ERROR)


def clean_and_validate_id(raw: str) -> str:
    cleaned = normalise_id(raw)
    validate_id(cleaned)
    return cleaned
