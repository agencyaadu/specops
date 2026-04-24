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

dashboard_roles = require_current_role("freddy", "general", "chief", "viewer")


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
    if role in ("freddy", "general", "viewer"):
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
              if role in ("freddy", "general", "viewer")
              else [email, target])

    # Collapse the old N+1 (two correlated subqueries per op) into a single
    # GROUP BY join over attendance for the target date. Dashboard loads with
    # 30 ops dropped from ~60 round-trips to 1.
    sql = f"""
        SELECT
          o.op_id, o.factory_name, o.shift, o.location, o.sales_team_name,
          o.poc1_name, o.poc1_phone, o.is_active, o.whatsapp_group_url,
          r.id                    AS report_id,
          r.submitted_at,
          r.submitted_by_email,
          r.chiefs, r.captains, r.operators,
          r.sd_cards_used, r.sd_cards_left,
          r.devices_available, r.devices_deployed, r.devices_lost, r.devices_recovered,
          r.actual_reporting_time,
          COALESCE(ac.attendance_count, 0) AS attendance_count,
          COALESCE(ac.verified_count, 0)   AS verified_count
        FROM operations o
        {scope_join}
        LEFT JOIN daily_reports r
               ON r.op_id = o.op_id AND r.report_date = ${date_param}
        LEFT JOIN (
            SELECT op_id,
                   COUNT(*)                          AS attendance_count,
                   COUNT(*) FILTER (WHERE verified)  AS verified_count
              FROM attendance
             WHERE report_date = ${date_param}
             GROUP BY op_id
        ) ac ON ac.op_id = o.op_id
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
            "whatsapp_group_url":r["whatsapp_group_url"],
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
                "actual_reporting_time":_fmt_time(r["actual_reporting_time"]),
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


@router.get("/range")
async def dashboard_range(
    request: Request,
    from_: str = Query(..., alias="from"),
    to:    str = Query(...),
    claims: dict = Depends(dashboard_roles),
):
    """Return /api/dashboard payloads for every date in [from, to].

    Collapses the analytics page's previous Promise.all of 14 per-date calls
    into a single SQL query using generate_series. Capped at 90 days to keep
    payload size sensible.
    """
    d_from = _parse_date(from_)
    d_to   = _parse_date(to)
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    span_days = (d_to - d_from).days + 1
    if span_days > 90:
        raise HTTPException(400, "range may not exceed 90 days")

    db = request.app.state.db
    role = claims["role"]
    email = (claims.get("email") or "").lower()

    # Same scope logic as /api/dashboard, but we build the SQL with numbered
    # params so from/to come after the email (if any).
    if role in ("freddy", "general", "viewer"):
        scope_join = ""
        params: list = []
    else:
        scope_join = "JOIN op_assignments asn ON asn.op_id = o.op_id AND asn.email = $1"
        params = [email]

    from_idx = len(params) + 1   # $1 if admin, $2 if chief
    to_idx   = len(params) + 2
    params.extend([d_from, d_to])

    sql = f"""
        SELECT
          d.day::date               AS report_date,
          o.op_id, o.factory_name, o.shift, o.location, o.sales_team_name,
          o.poc1_name, o.poc1_phone, o.is_active, o.whatsapp_group_url,
          r.id                      AS report_id,
          r.submitted_at,
          r.submitted_by_email,
          r.chiefs, r.captains, r.operators,
          r.sd_cards_used, r.sd_cards_left,
          r.devices_available, r.devices_deployed, r.devices_lost, r.devices_recovered,
          r.actual_reporting_time,
          COALESCE(ac.attendance_count, 0) AS attendance_count,
          COALESCE(ac.verified_count, 0)   AS verified_count
        FROM generate_series(${from_idx}::date, ${to_idx}::date, '1 day'::interval) AS d(day)
        CROSS JOIN operations o
        {scope_join}
        LEFT JOIN daily_reports r
               ON r.op_id = o.op_id AND r.report_date = d.day
        LEFT JOIN (
            SELECT op_id, report_date,
                   COUNT(*)                         AS attendance_count,
                   COUNT(*) FILTER (WHERE verified) AS verified_count
              FROM attendance
             WHERE report_date BETWEEN ${from_idx}::date AND ${to_idx}::date
             GROUP BY op_id, report_date
        ) ac ON ac.op_id = o.op_id AND ac.report_date = d.day
        ORDER BY d.day, o.factory_name, o.shift
    """
    rows = await db.fetch(sql, *params)

    def _fmt_time(v):
        return v.strftime("%H:%M") if v is not None and hasattr(v, "strftime") else (v if v is None else str(v))

    # Bucket rows by date into the same shape /api/dashboard returns so the
    # frontend can reuse its flattenDashboard function unchanged.
    by_date: dict[str, dict] = {}
    for r in rows:
        day_iso = r["report_date"].isoformat() if hasattr(r["report_date"], "isoformat") else str(r["report_date"])
        bucket = by_date.setdefault(day_iso, {"date": day_iso, "summary": None, "ops": [],
                                               "_submitted": 0, "_pending": 0, "_att": 0, "_ver": 0})
        has_report = r["report_id"] is not None
        attc = int(r["attendance_count"] or 0)
        verc = int(r["verified_count"] or 0)
        if has_report:           bucket["_submitted"] += 1
        elif r["is_active"]:     bucket["_pending"]   += 1
        bucket["_att"] += attc
        bucket["_ver"] += verc
        bucket["ops"].append({
            "op_id":             r["op_id"],
            "factory_name":      r["factory_name"],
            "shift":             r["shift"],
            "location":          r["location"],
            "sales_team_name":   r["sales_team_name"],
            "poc1_name":         r["poc1_name"],
            "poc1_phone":        r["poc1_phone"],
            "is_active":         r["is_active"],
            "whatsapp_group_url":r["whatsapp_group_url"],
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
                "actual_reporting_time":_fmt_time(r["actual_reporting_time"]),
            },
        })

    days = []
    for day in sorted(by_date.keys()):
        b = by_date[day]
        b["summary"] = {
            "total_ops":         len(b["ops"]),
            "ops_submitted":     b.pop("_submitted"),
            "ops_pending":       b.pop("_pending"),
            "total_attendance":  b.pop("_att"),
            "verified_attendance": b.pop("_ver"),
        }
        days.append(b)

    return {"from": d_from.isoformat(), "to": d_to.isoformat(), "days": days}


@router.get("/attendance")
async def attendance_list(
    request: Request,
    op_id: str = Query(...),
    date_: Optional[str] = Query(None, alias="date"),
    claims: dict = Depends(dashboard_roles),
):
    target = _parse_date(date_)
    # general and viewer see every op's attendance; chief needs assignment.
    if claims["role"] not in ("freddy", "general", "viewer"):
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
