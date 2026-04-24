"""Standalone attendance submission, split off from the daily-report endpoint.

Chiefs were avoiding submitting reports because the old flow required every
attendance row to be filled in before anything could land. This endpoint lets
attendance land independently (any time, incrementally) without touching the
daily_reports row for the day.

Append semantics: rows coming in that are already recorded for (op_id,
report_date, pan_number_hash) are skipped. New rows follow the same validator
routing (captain -> chief -> auto-confirm) as the original combined endpoint.
"""
from fastapi import APIRouter, Request, HTTPException, Depends, Form, File, UploadFile
from datetime import datetime
from typing import List
import json
import os
import re

from deps import require_current_role, require_op_access
from crypto import encrypt, hash_pan
from exif import extract_gps
from geo import haversine_m
from storage import upload_attendance_photo
from routers.reports import today_ist, parse_report_date
from routers.daily import _pick_validator_role  # shared validator routing logic

router = APIRouter()

reporter_or_admin = require_current_role("freddy", "general", "chief", "captain")
ALLOWED_PERSON_ROLES = {"chief", "captain", "operator"}
ALLOWED_PHOTO_MIMES  = {"image/jpeg", "image/heic", "image/heif"}
MAX_PHOTO_MB = 5
GEO_THRESHOLD_M = float(os.environ.get("GEO_VERIFY_THRESHOLD_M", "200"))

PAN_RE     = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
AADHAAR_RE = re.compile(r"^\d{12}$")
PHONE_RE   = re.compile(r"^[6-9]\d{9}$")


def _normalise_unique_id(raw: str) -> str:
    return re.sub(r"[\s\-]+", "", (raw or "")).upper()


def _validate_unique_id(value: str) -> None:
    if not (PAN_RE.match(value) or AADHAAR_RE.match(value)):
        raise HTTPException(
            400,
            "ID must be a 10-character PAN (ABCDE1234F) or a 12-digit Aadhaar",
        )


def _ext_for_mime(mime: str) -> str:
    return {"image/jpeg": ".jpg", "image/heic": ".heic", "image/heif": ".heif"}.get(mime, ".bin")


@router.post("/{op_id}")
async def submit_attendance(
    op_id: str,
    request: Request,
    payload: str = Form(...),
    photos: List[UploadFile] = File(default_factory=list),
    claims: dict = Depends(reporter_or_admin),
):
    await require_op_access(request, claims, op_id)

    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(400, "payload must be valid JSON")

    attendance = body.get("attendance") or []
    if not isinstance(attendance, list) or len(attendance) == 0:
        raise HTTPException(400, "attendance must be a non-empty list")

    report_date = parse_report_date(body.get("report_date"))

    # Photos-with-flag count must match the number of attachments.
    expected_photos = sum(1 for p in attendance if p.get("has_photo"))
    if expected_photos != len(photos):
        raise HTTPException(
            400, f"attendance expects {expected_photos} photos but got {len(photos)}",
        )

    # Pre-validate attendance entries; read photo bytes where provided.
    prepared = []
    seen_pans = set()
    photo_iter = iter(photos)
    for i, person in enumerate(attendance):
        name  = (person.get("full_name") or "").strip()
        phone = (person.get("phone") or "").strip()
        pan   = _normalise_unique_id(person.get("pan") or "")
        person_role = (person.get("person_role") or "operator").strip().lower()
        b_lat = person.get("browser_lat")
        b_lng = person.get("browser_lng")
        b_acc = person.get("browser_accuracy_m")

        if not name:
            raise HTTPException(400, f"attendance[{i}]: full_name required")
        if person_role not in ALLOWED_PERSON_ROLES:
            raise HTTPException(400, f"attendance[{i}]: person_role must be one of {sorted(ALLOWED_PERSON_ROLES)}")
        if not PHONE_RE.match(phone):
            raise HTTPException(400, f"attendance[{i}]: invalid phone")
        try:
            _validate_unique_id(pan)
        except HTTPException as e:
            raise HTTPException(400, f"attendance[{i}]: {e.detail}")
        if pan in seen_pans:
            raise HTTPException(400, f"attendance[{i}]: duplicate ID in submission")
        seen_pans.add(pan)
        if b_lat is None or b_lng is None:
            raise HTTPException(400, f"attendance[{i}]: device location required")
        try:
            b_lat = float(b_lat); b_lng = float(b_lng)
            b_acc = float(b_acc) if b_acc not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(400, f"attendance[{i}]: invalid device coords")

        entry = {
            "name": name, "phone": phone, "pan": pan,
            "person_role": person_role,
            "b_lat": b_lat, "b_lng": b_lng, "b_acc": b_acc,
            "exif_lat": None, "exif_lng": None,
            "distance_m": None, "verified": True,
            "photo_bytes": None, "photo_mime": None,
        }

        if person.get("has_photo"):
            photo = next(photo_iter)
            mime = (photo.content_type or "").lower()
            if mime not in ALLOWED_PHOTO_MIMES:
                raise HTTPException(400, f"attendance[{i}]: photo must be jpeg or heic")
            data = await photo.read()
            if len(data) > MAX_PHOTO_MB * 1024 * 1024:
                raise HTTPException(400, f"attendance[{i}]: photo exceeds {MAX_PHOTO_MB}MB")
            gps = extract_gps(data)
            if gps:
                entry["exif_lat"], entry["exif_lng"] = gps
                entry["distance_m"] = haversine_m(gps[0], gps[1], b_lat, b_lng)
                entry["verified"]   = entry["distance_m"] <= GEO_THRESHOLD_M
            entry["photo_bytes"] = data
            entry["photo_mime"]  = mime
        prepared.append(entry)

    db = request.app.state.db
    submitter_email = (claims.get("email") or "").lower()

    async with db.acquire() as conn:
        async with conn.transaction():
            op_row = await conn.fetchrow(
                "SELECT op_id, is_active FROM operations WHERE op_id = $1 FOR UPDATE",
                op_id,
            )
            if not op_row:
                raise HTTPException(404, "operation not found")
            if not op_row["is_active"]:
                raise HTTPException(410, "operation is not active")

            # Validator routing: same rule set as daily.py so rows queued from this
            # endpoint land in the same captain/chief review flow.
            assignee_rows = await conn.fetch(
                "SELECT email, role FROM op_assignments WHERE op_id = $1", op_id,
            )
            captains = {r["email"].lower() for r in assignee_rows if r["role"] == "captain"}
            chiefs   = {r["email"].lower() for r in assignee_rows if r["role"] == "chief"}
            validator_role = _pick_validator_role(submitter_email, captains, chiefs)

            status = "pending" if validator_role else "confirmed"
            confirmed_at = datetime.utcnow() if status == "confirmed" else None
            confirmed_by = submitter_email if status == "confirmed" else None

            inserted = 0
            skipped_duplicates = 0
            for p in prepared:
                pan_h = hash_pan(p["pan"])
                key = None
                if p["photo_bytes"] is not None:
                    key = f"{op_id}/{report_date.isoformat()}/{pan_h[:12]}{_ext_for_mime(p['photo_mime'])}"
                    upload_attendance_photo(p["photo_bytes"], key, p["photo_mime"])
                # Append mode: swallow unique-key collisions so submitting the same
                # person twice on the same day is a no-op rather than an error.
                result = await conn.execute(
                    """
                    INSERT INTO attendance (
                        op_id, report_date,
                        full_name, phone, person_role,
                        pan_number_enc, pan_number_hash,
                        photo_key,
                        photo_exif_lat, photo_exif_lng,
                        browser_lat, browser_lng, browser_accuracy_m,
                        distance_m, verified,
                        submitted_by_email,
                        status, validator_role,
                        confirmed_by_email, confirmed_at
                    ) VALUES (
                        $1, $2,
                        $3, $4, $5,
                        $6, $7,
                        $8,
                        $9, $10,
                        $11, $12, $13,
                        $14, $15,
                        $16,
                        $17, $18,
                        $19, $20
                    )
                    ON CONFLICT (op_id, report_date, pan_number_hash) DO NOTHING
                    """,
                    op_id, report_date,
                    p["name"], p["phone"], p["person_role"],
                    encrypt(p["pan"]), pan_h,
                    key,
                    p["exif_lat"], p["exif_lng"],
                    p["b_lat"], p["b_lng"], p["b_acc"],
                    p["distance_m"], p["verified"],
                    submitter_email,
                    status, validator_role,
                    confirmed_by, confirmed_at,
                )
                # asyncpg returns "INSERT 0 1" on insert, "INSERT 0 0" when ON CONFLICT fires.
                if result and result.endswith("1"):
                    inserted += 1
                else:
                    skipped_duplicates += 1

    return {
        "ok": True,
        "report_date": report_date.isoformat(),
        "received": len(prepared),
        "inserted": inserted,
        "skipped_duplicates": skipped_duplicates,
        "attendance_status": "pending" if validator_role else "confirmed",
        "validator_role": validator_role,
    }
