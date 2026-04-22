from fastapi import APIRouter, Request, HTTPException, Depends, Query
from datetime import datetime, date
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from deps import require_current_role, has_op_access
from crypto import decrypt
from storage import sign_attendance_url

router = APIRouter()

IST = ZoneInfo("Asia/Kolkata")

dashboard_roles = require_current_role("marshal", "general", "chief", "viewer")


def _parse_date(s: Optional[str]) -> date:
    if not s:
        return datetime.now(IST).date()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"date must be YYYY-MM-DD, got {s!r}")


def _op_scope_sql(role: str) -> tuple[str, int]:
    """Return ('' or 'JOIN op_assignments ...', next_param_index_start).

    general + viewer see all ops; chief sees only assigned ops.
    """
    if role in ("marshal", "general", "viewer"):
        return "", 1
    return (
        "JOIN op_assignments asn ON asn.op_id = o.op_id AND asn.email = $1",
        2,
    )


@router.get("")
async def dashboard(
    request: Request,
    date_: Optional[str] = Query(None, alias="date"),
    claims: dict = Depends(dashboard_roles),
):
    target = _parse_date(date_)
    db = request.app.state.db
    role = claims["role"]
    email = (claims.get("email") or "").lower()

    scope_join, date_param = _op_scope_sql(role)
    params = ([target]
              if role in ("marshal", "general", "viewer")
              else [email, target])

    sql = f"""
        SELECT
          o.op_id, o.factory_name, o.shift, o.location, o.sales_team_name,
          o.poc1_name, o.poc1_phone, o.is_active,
          r.id                    AS report_id,
          r.submitted_at,
          r.submitted_by_email,
          r.chiefs, r.captains, r.operators,
          r.sd_cards_used, r.sd_cards_left,
          r.devices_available, r.devices_deployed, r.devices_lost, r.devices_recovered,
          r.good_hours_projected, r.good_hours_actual,
          r.actual_reporting_time, r.time_leaving,
          (SELECT COUNT(*) FROM attendance  a
             WHERE a.op_id = o.op_id AND a.report_date = ${date_param}) AS attendance_count,
          (SELECT COUNT(*) FROM attendance  a
             WHERE a.op_id = o.op_id AND a.report_date = ${date_param} AND a.verified) AS verified_count
        FROM operations o
        {scope_join}
        LEFT JOIN daily_reports r
               ON r.op_id = o.op_id AND r.report_date = ${date_param}
        ORDER BY o.factory_name, o.shift
    """
    rows = await db.fetch(sql, *params)

    def _fmt_time(v):
        return v.strftime("%H:%M") if v is not None and hasattr(v, "strftime") else (v if v is None else str(v))

    ops_out = []
    submitted = 0
    pending = 0
    total_att = 0
    verified_att = 0
    for r in rows:
        has_report = r["report_id"] is not None
        attc = int(r["attendance_count"] or 0)
        verc = int(r["verified_count"] or 0)
        if has_report: submitted += 1
        elif r["is_active"]: pending += 1
        total_att += attc
        verified_att += verc
        ops_out.append({
            "op_id":             r["op_id"],
            "factory_name":      r["factory_name"],
            "shift":             r["shift"],
            "location":          r["location"],
            "sales_team_name":   r["sales_team_name"],
            "poc1_name":         r["poc1_name"],
            "poc1_phone":        r["poc1_phone"],
            "is_active":         r["is_active"],
            "submitted":         has_report,
            "attendance_count":  attc,
            "verified_count":    verc,
            "report": None if not has_report else {
                "submitted_at":         r["submitted_at"].astimezone(IST).isoformat() if r["submitted_at"] else None,
                "submitted_by_email":   r["submitted_by_email"],
                "chiefs":               r["chiefs"],
                "captains":             r["captains"],
                "operators":            r["operators"],
                "sd_cards_used":        r["sd_cards_used"],
                "sd_cards_left":        r["sd_cards_left"],
                "devices_available":    r["devices_available"],
                "devices_deployed":     r["devices_deployed"],
                "devices_lost":         r["devices_lost"],
                "devices_recovered":    r["devices_recovered"],
                "good_hours_projected": float(r["good_hours_projected"]) if r["good_hours_projected"] is not None else None,
                "good_hours_actual":    float(r["good_hours_actual"])    if r["good_hours_actual"]    is not None else None,
                "actual_reporting_time":_fmt_time(r["actual_reporting_time"]),
                "time_leaving":         _fmt_time(r["time_leaving"]),
            },
        })

    return {
        "date": target.isoformat(),
        "summary": {
            "total_ops":         len(ops_out),
            "ops_submitted":     submitted,
            "ops_pending":       pending,
            "total_attendance":  total_att,
            "verified_attendance": verified_att,
        },
        "ops": ops_out,
    }


@router.get("/attendance")
async def attendance_list(
    request: Request,
    op_id: str = Query(...),
    date_: Optional[str] = Query(None, alias="date"),
    claims: dict = Depends(dashboard_roles),
):
    target = _parse_date(date_)
    # general and viewer see every op's attendance; chief needs assignment.
    if claims["role"] not in ("marshal", "general", "viewer"):
        if not await has_op_access(request, claims, op_id):
            raise HTTPException(403, "not assigned to this operation")

    rows = await request.app.state.db.fetch(
        """
        SELECT id, full_name, phone, person_role,
               pan_number_enc,
               photo_key,
               photo_exif_lat, photo_exif_lng,
               browser_lat, browser_lng, browser_accuracy_m,
               distance_m, verified,
               submitted_at
          FROM attendance
         WHERE op_id = $1 AND report_date = $2
         ORDER BY submitted_at
        """,
        op_id, target,
    )

    out = []
    for r in rows:
        d = dict(r)
        try:
            d["pan_number"] = decrypt(d.pop("pan_number_enc", "") or "")
        except Exception:
            d["pan_number"] = ""
            d.pop("pan_number_enc", None)
        pk = d.pop("photo_key")
        try:
            d["photo_url"] = sign_attendance_url(pk) if pk else None
        except Exception:
            d["photo_url"] = None
        for k in ("browser_lat", "browser_lng", "browser_accuracy_m",
                  "photo_exif_lat", "photo_exif_lng", "distance_m"):
            if d.get(k) is not None:
                d[k] = float(d[k])
        if d.get("submitted_at") is not None:
            d["submitted_at"] = d["submitted_at"].astimezone(IST).isoformat()
        out.append(d)

    return {"op_id": op_id, "date": target.isoformat(), "rows": out}
