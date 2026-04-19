from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request
import asyncio
import os
import re
import uuid

from crypto import encrypt
from storage import upload_to_storage

router = APIRouter()

ALLOWED_DOC_TYPES  = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
ALLOWED_VIDEO_TYPE = {"video/mp4"}
MAX_DOC_MB         = 10
MAX_VIDEO_MB       = 200

def _check_size(data: bytes, max_mb: int, label: str):
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(400, f"{label} exceeds {max_mb}MB limit")

def _safe_filename(name: str, fallback_ext: str) -> str:
    # Supabase Storage keys must be ASCII; strip anything else.
    stem, ext = os.path.splitext(name or "")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "file"
    ext  = re.sub(r"[^A-Za-z0-9.]+", "", ext) or fallback_ext
    return f"{stem[:64]}{ext}"

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
    occupation:       str  = Form(...) ,
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
    consented:        bool = Form(False) ,
    consented_terms:  bool = Form(False) ,

    pan_card:    UploadFile = File(...) ,
    intro_video: UploadFile = File(...) ,
):
    if not consented or not consented_terms:
        raise HTTPException(400, "both consents required")

    lang_list = [l.strip() for l in languages.split(",") if l.strip()]
    if not lang_list:
        raise HTTPException(400, "pick at least one language")

    required_text = {
        "full_name": full_name, "whatsapp": whatsapp, "email": email, "occupation": occupation,
        "telegram_id": telegram_id, "discord_id": discord_id, "twitter_id": twitter_id,
        "hardest_problem": hardest_problem,
        "address_line1": address_line1, "pincode": pincode, "city": city, "state": state,
        "upi_id": upi_id, "beneficiary_name": beneficiary_name, "account_number": account_number,
        "ifsc_code": ifsc_code, "bank_name": bank_name, "branch_name": branch_name,
        "pan_number": pan_number,
    }
    missing = [k for k, v in required_text.items() if not v.strip()]
    if missing:
        raise HTTPException(400, f"missing required fields: {', '.join(missing)}")

    if not pan_card.filename:
        raise HTTPException(400, "PAN card file is required")
    pan_bytes = await pan_card.read()
    mime = pan_card.content_type or "application/octet-stream"
    if mime not in ALLOWED_DOC_TYPES:
        raise HTTPException(400, "PAN card must be image or PDF")
    _check_size(pan_bytes, MAX_DOC_MB, "PAN card")
    pan_url = await _store(pan_bytes, "pan", pan_card.filename, mime, ".bin")

    if not intro_video.filename:
        raise HTTPException(400, "intro video file is required")
    vid_bytes = await intro_video.read()
    mime = intro_video.content_type or "video/mp4"
    if mime not in ALLOWED_VIDEO_TYPE:
        raise HTTPException(400, "intro video must be mp4")
    _check_size(vid_bytes, MAX_VIDEO_MB, "intro video")
    vid_url = await _store(vid_bytes, "videos", intro_video.filename, mime, ".mp4")

    db = request.app.state.db

    row_id = await db.fetchval("""
        INSERT INTO submissions (
            full_name, whatsapp, email, alt_email, occupation, google_id,
            telegram_id, discord_id, twitter_id, referred_by,
            languages, hardest_problem, health_notes,
            address_line1, address_line2, pincode, city, state,
            upi_id, beneficiary_name, account_number_enc,
            ifsc_code, bank_name, branch_name,
            pan_number_enc,
            pan_card_url, intro_video_url,
            consented, consented_terms
        ) VALUES (
            $1,$2,$3,$4,$5,$6,
            $7,$8,$9,$10,
            $11,$12,$13,
            $14,$15,$16,$17,$18,
            $19,$20,$21,
            $22,$23,$24,
            $25,
            $26,$27,
            $28,$29
        ) RETURNING id
    """,
        full_name, whatsapp, email, alt_email, occupation, google_id,
        telegram_id, discord_id, twitter_id, referred_by,
        lang_list,
        hardest_problem, health_notes,
        address_line1, address_line2, pincode, city, state,
        upi_id, beneficiary_name, encrypt(account_number),
        ifsc_code, bank_name, branch_name,
        encrypt(pan_number),
        pan_url, vid_url,
        consented, consented_terms,
    )

    return {"ok": True, "id": row_id}
