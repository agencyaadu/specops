from fastapi import APIRouter, UploadFile, File, Form, HTTPException
import asyncio
import os
import re

from appscript import submit as appscript_submit

router = APIRouter()

ALLOWED_DOC_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_DOC_MB        = 10

def _check_size(data: bytes, max_mb: int, label: str):
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(400, f"{label} exceeds {max_mb}MB limit")

def _safe_filename(name: str, fallback_ext: str) -> str:
    stem, ext = os.path.splitext(name or "")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "file"
    ext  = re.sub(r"[^A-Za-z0-9.]+", "", ext) or fallback_ext
    return f"{stem[:64]}{ext}"

@router.post("/")
async def submit(
    full_name:        str  = Form(...),
    whatsapp:         str  = Form(...),
    email:            str  = Form(...),
    alt_email:        str  = Form(""),
    occupation:       str  = Form(...),
    google_id:        str  = Form(""),

    telegram_id:      str  = Form(...),
    discord_id:       str  = Form(...),
    twitter_id:       str  = Form(...),
    referred_by:      str  = Form(""),

    languages:        str  = Form(...),
    hardest_problem:  str  = Form(...),
    health_notes:     str  = Form(""),

    address_line1:    str  = Form(...),
    address_line2:    str  = Form(""),
    pincode:          str  = Form(...),
    city:             str  = Form(...),
    state:            str  = Form(...),

    upi_id:           str  = Form(...),
    beneficiary_name: str  = Form(...),
    account_number:   str  = Form(...),
    ifsc_code:        str  = Form(...),
    bank_name:        str  = Form(...),
    branch_name:      str  = Form(...),

    pan_number:       str  = Form(...),
    video_url:        str  = Form(...),
    consented:        bool = Form(False),
    consented_terms:  bool = Form(False),

    pan_card: UploadFile = File(...),
):
    if not consented or not consented_terms:
        raise HTTPException(400, "both consents required")

    lang_list = [l.strip() for l in languages.split(",") if l.strip()]
    if not lang_list:
        raise HTTPException(400, "pick at least one language")

    if not re.match(r"^https?://", video_url.strip()):
        raise HTTPException(400, "video_url must be a public http(s) link")

    if not pan_card.filename:
        raise HTTPException(400, "PAN card file is required")
    pan_bytes = await pan_card.read()
    mime = pan_card.content_type or "application/octet-stream"
    if mime not in ALLOWED_DOC_TYPES:
        raise HTTPException(400, "PAN card must be image or PDF")
    _check_size(pan_bytes, MAX_DOC_MB, "PAN card")
    pan_name = _safe_filename(pan_card.filename, ".bin")

    payload = {
        "full_name": full_name, "whatsapp": whatsapp, "email": email,
        "alt_email": alt_email, "occupation": occupation, "google_id": google_id,
        "telegram_id": telegram_id, "discord_id": discord_id, "twitter_id": twitter_id,
        "referred_by": referred_by,
        "languages": ", ".join(lang_list),
        "hardest_problem": hardest_problem, "health_notes": health_notes,
        "address_line1": address_line1, "address_line2": address_line2,
        "pincode": pincode, "city": city, "state": state,
        "upi_id": upi_id, "beneficiary_name": beneficiary_name,
        "account_number_encrypted": account_number,
        "ifsc_code": ifsc_code, "bank_name": bank_name, "branch_name": branch_name,
        "pan_number_encrypted": pan_number,
        "consented": consented, "consented_terms": consented_terms,
        "video_url": video_url.strip(),
    }

    result = await asyncio.to_thread(appscript_submit, payload, pan_bytes, pan_name, mime)
    return {"ok": True, "id": result.get("id")}
