from fastapi import APIRouter, Request, HTTPException, Depends, Form, File, UploadFile
from datetime import datetime, time
from typing import List, Optional
import json
import os
import re

from deps import require_current_role, require_op_access
from crypto import encrypt, hash_pan
from exif import extract_gps
from geo import haversine_m
from storage import upload_attendance_photo
from routers.reports import today_ist

router = APIRouter()

reporter_or_admin = require_current_role("marshal", "general", "chief", "captain")
ALLOWED_PERSON_ROLES = {"chief", "captain", "operator"}

ALLOWED_PHOTO_MIMES = {"image/jpeg", "image/heic", "image/heif"}
MAX_PHOTO_MB = 5
GEO_THRESHOLD_M = float(os.environ.get("GEO_VERIFY_THRESHOLD_M", "200"))

PAN_RE   = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
PHONE_RE = re.compile(r"^[6-9]\d{9}$")

_DAILY_INT_FIELDS  = ["chiefs", "captains", "operators", "sd_cards_used", "sd_cards_left",
                      "devices_available", "devices_deployed", "devices_lost", "devices_recovered"]
_DAILY_NUM_FIELDS  = ["good_hours_projected", "good_hours_actual"]
_DAILY_TIME_FIELDS = ["actual_reporting_time", "time_leaving"]

def _ext_for_mime(mime: str) -> str:
    return {"image/jpeg": ".jpg", "image/heic": ".heic", "image/heif": ".heif"}.get(mime, ".bin")

def _validate_time_str(v: Optional[str]) -> Optional[time]:
    if v is None or v == "":
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", v)
    if not m:
        raise HTTPException(400, f"invalid time value: {v}")
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59 and 0 <= s <= 59):
        raise HTTPException(400, f"time out of range: {v}")
    return time(h, mi, s)

@router.post("/{op_id}")
async def submit_daily(
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

    daily = body.get("daily") or {}
    events = body.get("events") or []
    attendance = body.get("attendance") or []

    if not isinstance(attendance, list) or len(attendance) == 0:
        raise HTTPException(400, "attendance must be a non-empty list")

    # Photos are optional per-attendee. The client sends `has_photo: true` on
    # entries that have a photo, and appends those photo files (in same order)
    # to the `photos` multipart field. Photos-with-flag count must match.
    expected_photos = sum(1 for p in attendance if p.get("has_photo"))
    if expected_photos != len(photos):
        raise HTTPException(
            400, f"attendance expects {expected_photos} photos but got {len(photos)}",
        )

    # Pre-validate attendance entries (and read photo bytes where provided).
    prepared = []
    seen_pans = set()
    photo_iter = iter(photos)
    for i, person in enumerate(attendance):
        name  = (person.get("full_name") or "").strip()
        phone = (person.get("phone") or "").strip()
        pan   = (person.get("pan") or "").strip().upper()
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
        if not PAN_RE.match(pan):
            raise HTTPException(400, f"attendance[{i}]: invalid PAN format")
        if pan in seen_pans:
            raise HTTPException(400, f"attendance[{i}]: duplicate PAN in submission")
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
            # No EXIF GPS -> accept the photo but don't contradict device location;
            # verified stays True because device location is authoritative.
            entry["photo_bytes"] = data
            entry["photo_mime"]  = mime

        prepared.append(entry)

    db = request.app.state.db
    report_date = today_ist()

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

            daily_vals = {}
            for k in _DAILY_INT_FIELDS:
                v = daily.get(k)
                daily_vals[k] = int(v) if v not in (None, "") else None
            for k in _DAILY_NUM_FIELDS:
                v = daily.get(k)
                daily_vals[k] = float(v) if v not in (None, "") else None
            for k in _DAILY_TIME_FIELDS:
                daily_vals[k] = _validate_time_str(daily.get(k))

            report_id = await conn.fetchval(
                """
                INSERT INTO daily_reports (
                    op_id, report_date,
                    chiefs, captains, operators,
                    sd_cards_used, sd_cards_left,
                    devices_available, devices_deployed, devices_lost, devices_recovered,
                    good_hours_projected, good_hours_actual,
                    actual_reporting_time, time_leaving,
                    submitted_by_email
                ) VALUES (
                    $1, $2,
                    $3, $4, $5,
                    $6, $7,
                    $8, $9, $10, $11,
                    $12, $13,
                    $14, $15,
                    $16
                )
                ON CONFLICT (op_id, report_date) DO UPDATE SET
                    chiefs = EXCLUDED.chiefs,
                    captains = EXCLUDED.captains,
                    operators = EXCLUDED.operators,
                    sd_cards_used = EXCLUDED.sd_cards_used,
                    sd_cards_left = EXCLUDED.sd_cards_left,
                    devices_available = EXCLUDED.devices_available,
                    devices_deployed = EXCLUDED.devices_deployed,
                    devices_lost = EXCLUDED.devices_lost,
                    devices_recovered = EXCLUDED.devices_recovered,
                    good_hours_projected = EXCLUDED.good_hours_projected,
                    good_hours_actual = EXCLUDED.good_hours_actual,
                    actual_reporting_time = EXCLUDED.actual_reporting_time,
                    time_leaving = EXCLUDED.time_leaving,
                    submitted_by_email = EXCLUDED.submitted_by_email,
                    submitted_at = NOW()
                RETURNING id
                """,
                op_id, report_date,
                daily_vals["chiefs"], daily_vals["captains"], daily_vals["operators"],
                daily_vals["sd_cards_used"], daily_vals["sd_cards_left"],
                daily_vals["devices_available"], daily_vals["devices_deployed"],
                daily_vals["devices_lost"], daily_vals["devices_recovered"],
                daily_vals["good_hours_projected"], daily_vals["good_hours_actual"],
                daily_vals["actual_reporting_time"], daily_vals["time_leaving"],
                claims.get("email", ""),
            )

            # Replace events for this report (clean re-submit semantics).
            await conn.execute("DELETE FROM report_events WHERE report_id = $1", report_id)
            for ev in events:
                ts_raw = ev.get("ts")
                note   = (ev.get("note") or "").strip()
                if not ts_raw or not note:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    raise HTTPException(400, f"invalid event timestamp: {ts_raw}")
                await conn.execute(
                    "INSERT INTO report_events (report_id, ts, note) VALUES ($1, $2, $3)",
                    report_id, ts, note,
                )

            for p in prepared:
                pan_h = hash_pan(p["pan"])
                key = None
                if p["photo_bytes"] is not None:
                    key = f"{op_id}/{report_date.isoformat()}/{pan_h[:12]}{_ext_for_mime(p['photo_mime'])}"
                    upload_attendance_photo(p["photo_bytes"], key, p["photo_mime"])
                try:
                    await conn.execute(
                        """
                        INSERT INTO attendance (
                            op_id, report_date,
                            full_name, phone, person_role,
                            pan_number_enc, pan_number_hash,
                            photo_key,
                            photo_exif_lat, photo_exif_lng,
                            browser_lat, browser_lng, browser_accuracy_m,
                            distance_m, verified
                        ) VALUES (
                            $1, $2,
                            $3, $4, $5,
                            $6, $7,
                            $8,
                            $9, $10,
                            $11, $12, $13,
                            $14, $15
                        )
                        """,
                        op_id, report_date,
                        p["name"], p["phone"], p["person_role"],
                        encrypt(p["pan"]), pan_h,
                        key,
                        p["exif_lat"], p["exif_lng"],
                        p["b_lat"], p["b_lng"], p["b_acc"],
                        p["distance_m"], p["verified"],
                    )
                except Exception as e:
                    raise HTTPException(409, f"attendance insert failed ({p['name']}): {e}")

    return {"ok": True, "report_id": report_id, "attendance_count": len(prepared)}
