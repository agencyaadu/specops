from fastapi import APIRouter, Request, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional
from datetime import time
import re

from deps import require_current_role, has_op_access

router = APIRouter()

# General OR chief (we'll branch on who can do what inside each handler)
general_or_chief = require_current_role("general", "chief")
any_role         = require_current_role("general", "chief", "captain")
general_only     = require_current_role("general")

def _slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s.strip().lower())
    return s.strip("-") or "x"

class OpIn(BaseModel):
    factory_name: str
    shift: str
    location: Optional[str] = None
    map_link: Optional[str] = None
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

@router.post("")
async def create_op(body: OpIn, request: Request, claims: dict = Depends(general_or_chief)):
    # Chiefs must have can_create_ops granted; generals always can.
    if claims["role"] != "general" and not claims.get("can_create_ops"):
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
                    op_id, factory_name, shift, location, map_link,
                    poc1_name, poc1_phone, poc1_role,
                    poc2_name, poc2_phone, poc2_role,
                    sales_team_name,
                    shift_start, shift_end, reporting_time, deployment_start,
                    collection_start, report_submission_time, final_closing_time
                ) VALUES (
                    $1,$2,$3,$4,$5,
                    $6,$7,$8,
                    $9,$10,$11,
                    $12,
                    $13,$14,$15,$16,
                    $17,$18,$19
                )
                """,
                op_id, body.factory_name.strip(), body.shift.strip(),
                body.location, body.map_link,
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
    return _row_out(row)

@router.get("")
async def list_ops(request: Request, claims: dict = Depends(any_role)):
    db = request.app.state.db
    if claims["role"] == "general":
        rows = await db.fetch("SELECT * FROM operations ORDER BY factory_name, shift")
    else:
        # Chiefs see only ops they're assigned to.
        email = (claims.get("email") or "").lower()
        rows = await db.fetch(
            """
            SELECT o.* FROM operations o
              JOIN op_assignments a ON a.op_id = o.op_id AND a.email = $1
             ORDER BY o.factory_name, o.shift
            """,
            email,
        )
    return {"rows": [_row_out(r) for r in rows]}

@router.patch("/{op_id}")
async def patch_op(op_id: str, body: OpPatch, request: Request, claims: dict = Depends(general_or_chief)):
    if claims["role"] != "general":
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
    return _row_out(row)
