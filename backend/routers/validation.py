from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from deps import require_current_role
from storage import sign_attendance_url

router = APIRouter()

# Only the two validator roles need this surface. Admins also see everything so they
# can step in if a validator is unavailable.
validator_or_admin = require_current_role("freddy", "general", "chief", "captain")


class RejectIn(BaseModel):
    reason: str


def _row_out(row) -> dict:
    d = dict(row)
    for k in ("submitted_at", "confirmed_at", "rejected_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    if d.get("report_date") is not None:
        d["report_date"] = d["report_date"].isoformat()
    # Never leak encrypted PAN or its hash to the UI.
    d.pop("pan_number_enc", None)
    d.pop("pan_number_hash", None)
    key = d.pop("photo_key", None)
    d["photo_url"] = sign_attendance_url(key) if key else None
    return d


async def _load_for_action(request: Request, claims: dict, att_id: int) -> dict:
    """Fetch a pending attendance row and verify the caller is allowed to act on it."""
    row = await request.app.state.db.fetchrow(
        "SELECT * FROM attendance WHERE id = $1",
        att_id,
    )
    if not row:
        raise HTTPException(404, "attendance row not found")
    if row["status"] != "pending":
        raise HTTPException(409, f"row is already {row['status']}")

    role = claims.get("role")
    email = (claims.get("email") or "").lower()

    # Admins can always act; otherwise the caller's role must match validator_role and
    # they must be assigned to this op.
    if role in ("freddy", "general"):
        return dict(row)
    if role != row["validator_role"]:
        raise HTTPException(403, "not the validator for this row")
    if (row["submitted_by_email"] or "").lower() == email:
        raise HTTPException(403, "cannot validate your own submission")
    assigned = await request.app.state.db.fetchval(
        "SELECT 1 FROM op_assignments WHERE op_id = $1 AND email = $2 AND role = $3",
        row["op_id"], email, role,
    )
    if not assigned:
        raise HTTPException(403, "not assigned to this op as validator")
    return dict(row)


@router.get("/pending")
async def list_pending(request: Request, claims: dict = Depends(validator_or_admin)):
    """Pending attendance visible to the caller.

    Captains/chiefs see rows where validator_role matches their role, they're assigned
    to the op, and they didn't submit the row themselves. Admins see everything pending.
    """
    role = claims.get("role")
    email = (claims.get("email") or "").lower()
    db = request.app.state.db

    if role in ("freddy", "general"):
        rows = await db.fetch(
            """
            SELECT a.*, o.factory_name, o.shift
              FROM attendance a
              JOIN operations  o ON o.op_id = a.op_id
             WHERE a.status = 'pending'
             ORDER BY a.report_date DESC, a.op_id, a.full_name
            """
        )
    else:
        rows = await db.fetch(
            """
            SELECT a.*, o.factory_name, o.shift
              FROM attendance a
              JOIN operations  o ON o.op_id = a.op_id
              JOIN op_assignments asn
                ON asn.op_id = a.op_id AND asn.email = $1 AND asn.role = $2
             WHERE a.status = 'pending'
               AND a.validator_role = $2
               AND COALESCE(LOWER(a.submitted_by_email), '') <> $1
             ORDER BY a.report_date DESC, a.op_id, a.full_name
            """,
            email, role,
        )

    return {"rows": [_row_out(r) for r in rows]}


@router.post("/{att_id}/confirm")
async def confirm(att_id: int, request: Request, claims: dict = Depends(validator_or_admin)):
    await _load_for_action(request, claims, att_id)
    email = (claims.get("email") or "").lower()
    row = await request.app.state.db.fetchrow(
        """
        UPDATE attendance
           SET status = 'confirmed',
               confirmed_by_email = $2,
               confirmed_at = $3,
               rejected_by_email = NULL,
               rejected_at = NULL,
               reject_reason = NULL
         WHERE id = $1 AND status = 'pending'
         RETURNING *
        """,
        att_id, email, datetime.utcnow(),
    )
    if not row:
        raise HTTPException(409, "row is no longer pending")
    return _row_out(row)


@router.post("/{att_id}/reject")
async def reject(
    att_id: int,
    body: RejectIn,
    request: Request,
    claims: dict = Depends(validator_or_admin),
):
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(400, "reject reason required")
    if len(reason) > 500:
        raise HTTPException(400, "reject reason too long (max 500 chars)")

    await _load_for_action(request, claims, att_id)
    email = (claims.get("email") or "").lower()
    row = await request.app.state.db.fetchrow(
        """
        UPDATE attendance
           SET status = 'rejected',
               rejected_by_email = $2,
               rejected_at = $3,
               reject_reason = $4,
               confirmed_by_email = NULL,
               confirmed_at = NULL
         WHERE id = $1 AND status = 'pending'
         RETURNING *
        """,
        att_id, email, datetime.utcnow(), reason,
    )
    if not row:
        raise HTTPException(409, "row is no longer pending")
    return _row_out(row)


@router.get("/counts")
async def counts(request: Request, claims: dict = Depends(validator_or_admin)):
    """Badge count for the dashboard - number of rows awaiting the caller's action."""
    role = claims.get("role")
    email = (claims.get("email") or "").lower()
    db = request.app.state.db

    if role in ("freddy", "general"):
        n = await db.fetchval("SELECT COUNT(*) FROM attendance WHERE status = 'pending'")
    else:
        n = await db.fetchval(
            """
            SELECT COUNT(*)
              FROM attendance a
              JOIN op_assignments asn
                ON asn.op_id = a.op_id AND asn.email = $1 AND asn.role = $2
             WHERE a.status = 'pending'
               AND a.validator_role = $2
               AND COALESCE(LOWER(a.submitted_by_email), '') <> $1
            """,
            email, role,
        )
    return {"pending": int(n or 0)}
