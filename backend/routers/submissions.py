from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request
import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timezone

from crypto import encrypt
from storage import upload_to_storage
import sheets as _sheets

log = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_DOC_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_DOC_MB        = 2

PAN_RE     = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
AADHAAR_RE = re.compile(r"^\d{12}$")

def _check_size(data: bytes, max_mb: int, label: str):
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(400, f"{label} exceeds {max_mb}MB limit")

def _safe_filename(name: str, fallback_ext: str) -> str:
    # Supabase Storage keys must be ASCII; strip anything else.
    stem, ext = os.path.splitext(name or "")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "file"
    ext  = re.sub(r"[^A-Za-z0-9.]+", "", ext) or fallback_ext
    return f"{stem[:64]}{ext}"

def _validate_video_url(url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^https?://[^\s]+\.[^\s]+", url):
        raise HTTPException(400, "intro video must be a public http(s) URL")
    return url

def _normalize_id(raw: str) -> str:
    """Strip whitespace, dots, dashes from PAN/Aadhaar; uppercase the result."""
    return re.sub(r"[\s.\-]+", "", raw or "").upper()

def _validate_pan_or_aadhaar(raw: str) -> str:
    cleaned = _normalize_id(raw)
    if PAN_RE.match(cleaned) or AADHAAR_RE.match(cleaned):
        return cleaned
    raise HTTPException(400, "ID must be a 10-character PAN (ABCDE1234F) or 12-digit Aadhaar")

def _normalize_phone(raw: str) -> str:
    """Strip spaces/dots/dashes/parens. Prepend +91 if no country code given.
    Accepts a leading 0 (some users write 09876543210) and drops it."""
    s = re.sub(r"[\s\-.()]+", "", raw or "")
    if not s:
        return s
    if s.startswith("+"):
        return s
    s = re.sub(r"^0+", "", s)
    if s.startswith("91") and len(s) == 12:
        return "+" + s
    return "+91" + s

def _normalize_account(raw: str) -> str:
    return re.sub(r"[\s.\-]+", "", raw or "")

def _normalize_ifsc(raw: str) -> str:
    return re.sub(r"\s+", "", raw or "").upper()

async def _store(file_bytes: bytes, prefix: str, filename: str, mime: str, fallback_ext: str) -> str:
    key = f"{prefix}/{uuid.uuid4()}_{_safe_filename(filename, fallback_ext)}"
    return await asyncio.to_thread(upload_to_storage, file_bytes, key, mime)

@router.post("/")
async def submit(
    request: Request ,

    full_name:        str  = Form(...) ,
    whatsapp:         str  = Form(...) ,
    email:            str  = Form(...) ,
    alt_email:        str  = Form("") ,
    google_id:        str  = Form("") ,

    telegram_id:      str  = Form(...) ,
    discord_id:       str  = Form(...) ,
    twitter_id:       str  = Form(...) ,
    referred_by:      str  = Form("") ,

    languages:        str  = Form(...) ,
    hardest_problem:  str  = Form(...) ,
    health_notes:     str  = Form("") ,

    address_line1:    str  = Form(...) ,
    address_line2:    str  = Form("") ,
    pincode:          str  = Form(...) ,
    city:             str  = Form(...) ,
    state:            str  = Form(...) ,

    upi_id:           str  = Form(...) ,
    beneficiary_name: str  = Form(...) ,
    account_number:   str  = Form(...) ,
    ifsc_code:        str  = Form(...) ,
    bank_name:        str  = Form(...) ,
    branch_name:      str  = Form(...) ,

    pan_number:       str  = Form(...) ,
    video_url:        str  = Form(...) ,
    consented:        bool = Form(False) ,
    consented_terms:  bool = Form(False) ,

    pan_card:    UploadFile = File(...) ,
):
    if not consented or not consented_terms:
        raise HTTPException(400, "both consents required")

    lang_list = [l.strip() for l in languages.split(",") if l.strip()]
    if not lang_list:
        raise HTTPException(400, "pick at least one language")

    # Normalise all incoming text fields so the DB row is clean regardless
    # of how the client formatted them. The frontend cleans on submit too;
    # this is the source-of-truth pass.
    full_name        = (full_name or "").strip()
    whatsapp         = _normalize_phone(whatsapp)
    email            = (email or "").strip().lower()
    alt_email        = (alt_email or "").strip().lower()
    telegram_id      = (telegram_id or "").strip()
    discord_id       = (discord_id or "").strip()
    twitter_id       = (twitter_id or "").strip()
    referred_by      = (referred_by or "").strip()
    hardest_problem  = (hardest_problem or "").strip()
    health_notes     = (health_notes or "").strip()
    address_line1    = (address_line1 or "").strip()
    address_line2    = (address_line2 or "").strip()
    pincode          = re.sub(r"\s+", "", pincode or "")
    city             = (city or "").strip()
    state            = (state or "").strip()
    upi_id           = re.sub(r"\s+", "", upi_id or "")
    beneficiary_name = (beneficiary_name or "").strip()
    account_number   = _normalize_account(account_number)
    ifsc_code        = _normalize_ifsc(ifsc_code)
    bank_name        = (bank_name or "").strip()
    branch_name      = (branch_name or "").strip()
    pan_number       = _validate_pan_or_aadhaar(pan_number)

    required_text = {
        "full_name": full_name, "whatsapp": whatsapp, "email": email,
        "telegram_id": telegram_id, "discord_id": discord_id, "twitter_id": twitter_id,
        "hardest_problem": hardest_problem,
        "address_line1": address_line1, "pincode": pincode, "city": city, "state": state,
        "upi_id": upi_id, "beneficiary_name": beneficiary_name, "account_number": account_number,
        "ifsc_code": ifsc_code, "bank_name": bank_name, "branch_name": branch_name,
        "pan_number": pan_number, "video_url": video_url,
    }
    missing = [k for k, v in required_text.items() if not v]
    if missing:
        raise HTTPException(400, f"missing required fields: {', '.join(missing)}")

    clean_video_url = _validate_video_url(video_url)

    if not pan_card.filename:
        raise HTTPException(400, "ID card file is required")
    pan_bytes = await pan_card.read()
    mime = pan_card.content_type or "application/octet-stream"
    if mime not in ALLOWED_DOC_TYPES:
        raise HTTPException(400, "ID card must be image or PDF")
    _check_size(pan_bytes, MAX_DOC_MB, "ID card")
    pan_url = await _store(pan_bytes, "pan", pan_card.filename, mime, ".bin")

    db = request.app.state.db

    row_id = await db.fetchval("""
        INSERT INTO submissions (
            full_name, whatsapp, email, alt_email, google_id,
            telegram_id, discord_id, twitter_id, referred_by,
            languages, hardest_problem, health_notes,
            address_line1, address_line2, pincode, city, state,
            upi_id, beneficiary_name, account_number_enc,
            ifsc_code, bank_name, branch_name,
            pan_number_enc,
            pan_card_url, intro_video_url,
            consented, consented_terms
        ) VALUES (
            $1,$2,$3,$4,$5,
            $6,$7,$8,$9,
            $10,$11,$12,
            $13,$14,$15,$16,$17,
            $18,$19,$20,
            $21,$22,$23,
            $24,
            $25,$26,
            $27,$28
        ) RETURNING id
    """,
        full_name, whatsapp, email, alt_email, google_id,
        telegram_id, discord_id, twitter_id, referred_by,
        lang_list,
        hardest_problem, health_notes,
        address_line1, address_line2, pincode, city, state,
        upi_id, beneficiary_name, encrypt(account_number),
        ifsc_code, bank_name, branch_name,
        encrypt(pan_number),
        pan_url, clean_video_url,
        consented, consented_terms,
    )

    if _sheets.sheets_enabled():
        sheet_data = {
            "id": row_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "full_name": full_name, "whatsapp": whatsapp, "email": email,
            "alt_email": alt_email, "google_id": google_id,
            "telegram_id": telegram_id, "discord_id": discord_id,
            "twitter_id": twitter_id, "referred_by": referred_by,
            "languages": lang_list, "hardest_problem": hardest_problem,
            "health_notes": health_notes,
            "address_line1": address_line1, "address_line2": address_line2,
            "pincode": pincode, "city": city, "state": state,
            "upi_id": upi_id, "beneficiary_name": beneficiary_name,
            "account_number": account_number,
            "ifsc_code": ifsc_code, "bank_name": bank_name, "branch_name": branch_name,
            "pan_number": pan_number,
            "pan_card_url": pan_url, "intro_video_url": clean_video_url,
            "consented": consented, "consented_terms": consented_terms,
        }
        async def _push():
            try:
                await asyncio.to_thread(_sheets.append_row, sheet_data)
            except Exception:
                log.exception("sheets append failed for submission %s", row_id)
        asyncio.create_task(_push())

    return {"ok": True, "id": row_id}
