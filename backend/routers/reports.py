from fastapi import APIRouter, Request, HTTPException, Depends, Query
from datetime import datetime, date, timedelta
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from deps import require_current_role, require_op_access

router = APIRouter()

IST = ZoneInfo("Asia/Kolkata")

# How far back a reporter may submit or amend a report. Today counts as day 0.
MAX_BACKDATE_DAYS = 30

reporter_or_admin = require_current_role("freddy", "general", "chief", "captain")

def today_ist() -> date:
    return datetime.now(IST).date()

def parse_report_date(raw: Optional[str]) -> date:
    """Parse and bounds-check a report_date. Defaults to today IST.

    Rules: never in the future; never more than MAX_BACKDATE_DAYS in the past.
    Keeps the window tight so stale submissions can't drift in."""
    today = today_ist()
    if raw in (None, ""):
        return today
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(400, f"invalid report_date: {raw}")
    if d > today:
        raise HTTPException(400, "report_date cannot be in the future")
    if d < today - timedelta(days=MAX_BACKDATE_DAYS):
        raise HTTPException(400, f"report_date cannot be older than {MAX_BACKDATE_DAYS} days")
    return d

def _time_str(v) -> Optional[str]:
    if v is None:
        return None
    return v.strftime("%H:%M") if hasattr(v, "strftime") else str(v)

@router.get("/{op_id}")
async def get_op_context(
    op_id: str,
    request: Request,
    date_str: Optional[str] = Query(None, alias="date"),
    claims: dict = Depends(reporter_or_admin),
):
    # Assignment gate: chiefs/captains only get the op context for ops they're
    # assigned to. Generals + Freddy see everything.
    await require_op_access(request, claims, op_id)
    db = request.app.state.db
    row = await db.fetchrow("SELECT * FROM operations WHERE op_id = $1", op_id)
    if not row:
        raise HTTPException(404, "operation not found")
    if not row["is_active"]:
        raise HTTPException(410, "operation is not active")

    report_date = parse_report_date(date_str)
    already = await db.fetchval(
        "SELECT 1 FROM daily_reports WHERE op_id = $1 AND report_date = $2",
        op_id, report_date,
    )

    d = dict(row)
    d["report_date"] = report_date.isoformat()
    d["today"] = today_ist().isoformat()
    d["max_backdate_days"] = MAX_BACKDATE_DAYS
    d["already_submitted"] = bool(already)
    d["my_role"] = claims.get("role")
    if d.get("created_at") is not None:
        d["created_at"] = d["created_at"].isoformat()
    for k in ("shift_start", "shift_end", "reporting_time", "deployment_start",
              "collection_start", "report_submission_time", "final_closing_time"):
        if d.get(k) is not None:
            d[k] = _time_str(d[k])
    return d
