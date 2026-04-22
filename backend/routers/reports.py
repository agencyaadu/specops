from fastapi import APIRouter, Request, HTTPException, Depends
from datetime import datetime, date
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from deps import require_current_role, require_op_access

router = APIRouter()

IST = ZoneInfo("Asia/Kolkata")

reporter_or_admin = require_current_role("chief", "captain", "general")

def today_ist() -> date:
    return datetime.now(IST).date()

def _time_str(v) -> Optional[str]:
    if v is None:
        return None
    return v.strftime("%H:%M") if hasattr(v, "strftime") else str(v)

@router.get("/{op_id}")
async def get_op_context(op_id: str, request: Request, claims: dict = Depends(reporter_or_admin)):
    await require_op_access(request, claims, op_id)
    db = request.app.state.db
    row = await db.fetchrow("SELECT * FROM operations WHERE op_id = $1", op_id)
    if not row:
        raise HTTPException(404, "operation not found")
    if not row["is_active"]:
        raise HTTPException(410, "operation is not active")

    today = today_ist()
    already = await db.fetchval(
        "SELECT 1 FROM daily_reports WHERE op_id = $1 AND report_date = $2",
        op_id, today,
    )

    d = dict(row)
    d["report_date"] = today.isoformat()
    d["already_submitted"] = bool(already)
    d["my_role"] = claims.get("role")
    if d.get("created_at") is not None:
        d["created_at"] = d["created_at"].isoformat()
    for k in ("shift_start", "shift_end", "reporting_time", "deployment_start",
              "collection_start", "report_submission_time", "final_closing_time"):
        if d.get(k) is not None:
            d[k] = _time_str(d[k])
    return d
