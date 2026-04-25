from fastapi import APIRouter, Request, HTTPException, Depends, Header, Query
from pydantic import BaseModel
from typing import Optional
from datetime import time, datetime
import asyncio
import logging
import re

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

from deps import require_current_role, has_op_access
import sheets as _sheets

log = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

router = APIRouter()

# General OR chief (we'll branch on who can do what inside each handler)
general_or_chief = require_current_role("freddy", "general", "chief")
any_role         = require_current_role("freddy", "general", "chief", "captain")
general_only     = require_current_role("freddy", "general")

def _slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s.strip().lower())
    return s.strip("-") or "x"

class OpIn(BaseModel):
    factory_name: str
    shift: str
    location: Optional[str] = None
    map_link: Optional[str] = None
    whatsapp_group_url: Optional[str] = None
    poc1_name: Optional[str] = None
    poc1_phone: Optional[str] = None
    poc1_role: Optional[str] = None
    poc2_name: Optional[str] = None
    poc2_phone: Optional[str] = None
    poc2_role: Optional[str] = None
    sales_team_name: Optional[str] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    reporting_time: Optional[str] = None
    deployment_start: Optional[str] = None
    collection_start: Optional[str] = None
    report_submission_time: Optional[str] = None
    final_closing_time: Optional[str] = None

class OpPatch(BaseModel):
    is_active: Optional[bool] = None
    location: Optional[str] = None
    map_link: Optional[str] = None
    whatsapp_group_url: Optional[str] = None
    poc1_name: Optional[str] = None
    poc1_phone: Optional[str] = None
    poc1_role: Optional[str] = None
    poc2_name: Optional[str] = None
    poc2_phone: Optional[str] = None
    poc2_role: Optional[str] = None
    sales_team_name: Optional[str] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    reporting_time: Optional[str] = None
    deployment_start: Optional[str] = None
    collection_start: Optional[str] = None
    report_submission_time: Optional[str] = None
    final_closing_time: Optional[str] = None

_TIME_FIELDS = {
    "shift_start", "shift_end", "reporting_time", "deployment_start",
    "collection_start", "report_submission_time", "final_closing_time",
}

def _parse_time(v: Optional[str]) -> Optional[time]:
    if v is None or v == "":
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", v)
    if not m:
        raise HTTPException(400, f"invalid time value: {v}")
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59 and 0 <= s <= 59):
        raise HTTPException(400, f"time out of range: {v}")
    return time(h, mi, s)

def _row_out(row) -> dict:
    d = dict(row)
    if d.get("created_at") is not None:
        d["created_at"] = d["created_at"].isoformat()
    for k in _TIME_FIELDS:
        if d.get(k) is not None:
            d[k] = d[k].strftime("%H:%M") if hasattr(d[k], "strftime") else str(d[k])
    return d


# ---------------------------------------------------------------------------
# Google Sheets mirror — pulls all ops + assignments and replaces the
# "Operations" tab. Fire-and-forget: callers never block on Sheets latency.
# ---------------------------------------------------------------------------

_OPS_SHEET_SQL = """
SELECT o.*,
       COALESCE((SELECT string_agg(email, ', ' ORDER BY email)
                   FROM op_assignments
                  WHERE op_id = o.op_id AND role = 'chief'),   '') AS chiefs,
       COALESCE((SELECT string_agg(email, ', ' ORDER BY email)
                   FROM op_assignments
                  WHERE op_id = o.op_id AND role = 'captain'), '') AS captains
  FROM operations o
 ORDER BY o.factory_name, o.shift
"""


async def _ops_sheet_rows(db) -> list:
    rows = await db.fetch(_OPS_SHEET_SQL)
    return [_row_out(r) for r in rows]


def schedule_ops_sheet_sync(db) -> None:
    """Schedule a background full sync of the Operations tab. No-op if Sheets
    isn't configured. Safe to call from any handler — never raises."""
    if not _sheets.sheets_enabled():
        return
    async def _push():
        try:
            rows = await _ops_sheet_rows(db)
            await asyncio.to_thread(_sheets.full_sync_ops, rows)
        except Exception:
            log.exception("ops sheets sync failed")
    asyncio.create_task(_push())


async def sync_ops_sheet_now(db) -> int:
    """Synchronous full sync used by the manual /admin/sync-ops-sheet endpoint.
    Returns the number of rows written (not counting the header)."""
    rows = await _ops_sheet_rows(db)
    await asyncio.to_thread(_sheets.full_sync_ops, rows)
    return len(rows)

@router.post("")
async def create_op(body: OpIn, request: Request, claims: dict = Depends(general_or_chief)):
    # Chiefs must have can_create_ops granted; generals always can.
    if claims["role"] not in ("freddy", "general") and not claims.get("can_create_ops"):
        raise HTTPException(403, "you are not permissioned to create ops")

    if not body.factory_name.strip() or not body.shift.strip():
        raise HTTPException(400, "factory_name and shift required")

    op_id = f"{_slugify(body.factory_name)}_{_slugify(body.shift)}"
    db = request.app.state.db

    existing = await db.fetchval(
        "SELECT 1 FROM operations WHERE factory_name = $1 AND shift = $2",
        body.factory_name, body.shift,
    )
    if existing:
        raise HTTPException(409, "operation already exists for this factory+shift")

    creator_email = (claims.get("email") or "").lower()

    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO operations (
                    op_id, factory_name, shift, location, map_link, whatsapp_group_url,
                    poc1_name, poc1_phone, poc1_role,
                    poc2_name, poc2_phone, poc2_role,
                    sales_team_name,
                    shift_start, shift_end, reporting_time, deployment_start,
                    collection_start, report_submission_time, final_closing_time
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,
                    $7,$8,$9,
                    $10,$11,$12,
                    $13,
                    $14,$15,$16,$17,
                    $18,$19,$20
                )
                """,
                op_id, body.factory_name.strip(), body.shift.strip(),
                body.location, body.map_link, body.whatsapp_group_url,
                body.poc1_name, body.poc1_phone, body.poc1_role,
                body.poc2_name, body.poc2_phone, body.poc2_role,
                body.sales_team_name,
                _parse_time(body.shift_start), _parse_time(body.shift_end),
                _parse_time(body.reporting_time), _parse_time(body.deployment_start),
                _parse_time(body.collection_start), _parse_time(body.report_submission_time),
                _parse_time(body.final_closing_time),
            )
            # Auto-assign the chief creator to this op so they can immediately edit it.
            if claims["role"] == "chief" and creator_email:
                await conn.execute(
                    """
                    INSERT INTO op_assignments (op_id, email, role, assigned_by_email)
                    VALUES ($1, $2, 'chief', $3)
                    ON CONFLICT (op_id, email) DO NOTHING
                    """,
                    op_id, creator_email, creator_email,
                )

    row = await db.fetchrow("SELECT * FROM operations WHERE op_id = $1", op_id)
    schedule_ops_sheet_sync(db)
    return _row_out(row)

@router.get("")
async def list_ops(
    request: Request,
    include: Optional[str] = Query(None, description="comma list: assignments,today"),
    claims: dict = Depends(any_role),
):
    wants = {x.strip() for x in (include or "").split(",") if x.strip()}
    today = datetime.now(_IST).date()
    db = request.app.state.db

    # Column list + joins depend on the include flags. Kept as one SQL so
    # the fan-out N+1 in the frontend collapses into a single request.
    select_extras = []
    joins = []
    if "assignments" in wants:
        select_extras.append("COALESCE(a.chief_count,   0) AS chief_count")
        select_extras.append("COALESCE(a.captain_count, 0) AS captain_count")
        joins.append("""
            LEFT JOIN (
              SELECT op_id,
                     COUNT(*) FILTER (WHERE role='chief')   AS chief_count,
                     COUNT(*) FILTER (WHERE role='captain') AS captain_count
                FROM op_assignments
               GROUP BY op_id
            ) a ON a.op_id = o.op_id
        """)
    if "today" in wants:
        select_extras.append("(r.op_id IS NOT NULL) AS already_submitted")
        joins.append(
            "LEFT JOIN daily_reports r ON r.op_id = o.op_id AND r.report_date = $today_date"
        )

    extras_sql = (", " + ", ".join(select_extras)) if select_extras else ""
    joins_sql  = "\n".join(joins)

    if claims["role"] in ("freddy", "general"):
        sql = f"""
            SELECT o.*{extras_sql}
              FROM operations o
              {joins_sql}
             ORDER BY o.factory_name, o.shift
        """
        if "today" in wants:
            sql = sql.replace("$today_date", "$1")
            rows = await db.fetch(sql, today)
        else:
            rows = await db.fetch(sql)
    else:
        # Chiefs/captains see only ops they're assigned to.
        email = (claims.get("email") or "").lower()
        sql = f"""
            SELECT o.*{extras_sql}
              FROM operations o
              JOIN op_assignments asn ON asn.op_id = o.op_id AND asn.email = $1
              {joins_sql}
             ORDER BY o.factory_name, o.shift
        """
        if "today" in wants:
            sql = sql.replace("$today_date", "$2")
            rows = await db.fetch(sql, email, today)
        else:
            rows = await db.fetch(sql, email)

    out = []
    for r in rows:
        d = _row_out(r)
        # _row_out only formats time columns; assignment/today extras already come typed right.
        out.append(d)
    return {"rows": out}

@router.post("/sync-sheet")
async def sync_sheet(request: Request, _claims: dict = Depends(general_only)):
    """Force a full sync of the Operations Google Sheets tab. The auto-sync
    already fires on every create / patch / assignment change; this endpoint
    is for one-shot backfills (e.g. after the sheet was wiped or the service
    account was newly shared). Freddy + general only — sheet writes are global,
    so chiefs don't see the button."""
    if not _sheets.sheets_enabled():
        raise HTTPException(503, "Google Sheets not configured (GOOGLE_SHEETS_ID / GOOGLE_SERVICE_ACCOUNT_JSON missing)")
    n = await sync_ops_sheet_now(request.app.state.db)
    return {"synced": n}


@router.patch("/{op_id}")
async def patch_op(op_id: str, body: OpPatch, request: Request, claims: dict = Depends(general_or_chief)):
    if claims["role"] not in ("freddy", "general"):
        # chief must be assigned to this op
        if not await has_op_access(request, claims, op_id):
            raise HTTPException(403, "not assigned to this operation")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "no fields to update")

    set_clauses = []
    values = []
    for i, (k, v) in enumerate(updates.items(), start=1):
        if k in _TIME_FIELDS:
            set_clauses.append(f"{k} = ${i}")
            values.append(_parse_time(v))
        else:
            set_clauses.append(f"{k} = ${i}")
            values.append(v)
    values.append(op_id)

    db = request.app.state.db
    row = await db.fetchrow(
        f"UPDATE operations SET {', '.join(set_clauses)} WHERE op_id = ${len(values)} RETURNING *",
        *values,
    )
    if not row:
        raise HTTPException(404, "not found")
    schedule_ops_sheet_sync(db)
    return _row_out(row)
