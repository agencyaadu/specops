"""Returning-user surface for /onboard.

When a previously-onboarded person signs back in via Google we don't
want to put them through the full form again. The onboarding page calls
these endpoints to:

  1. /api/me/profile     - "do we know this email already?"
  2. /api/me/active-ops  - dropdown source: factories + chiefs/captains
  3. /api/me/assignment  - upsert the person's self-reported assignment
                           for today (op + role + chief). Truth is later
                           verified by the chief/captain at attendance.

We never derive deployment from the DB here - we just ask the person and
log whatever they tell us, dated. Daily.py will eventually also write
person_assignments rows with source='attendance' so we get a second
opinion against which to compare.
"""
import os
from datetime import datetime
from typing import Optional

import jwt
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

router = APIRouter()
IST = ZoneInfo("Asia/Kolkata")
JWT_SECRET = os.environ["JWT_SECRET"]

ALLOWED_SELF_ROLES = {"operator", "captain", "chief"}


def _decode(authorization: Optional[str]) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token")


def _row_dict(row) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    for k in ("created_at",):
        if d.get(k) is not None and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    if d.get("assignment_date") is not None and hasattr(d["assignment_date"], "isoformat"):
        d["assignment_date"] = d["assignment_date"].isoformat()
    return d


@router.get("/profile")
async def me_profile(request: Request, authorization: Optional[str] = Header(None)):
    claims = _decode(authorization)
    email = (claims.get("email") or "").lower()
    if not email:
        raise HTTPException(400, "no email in token")
    db = request.app.state.db

    sub = await db.fetchrow(
        """
        SELECT id, full_name, email, whatsapp, created_at
          FROM submissions
         WHERE email = $1
         ORDER BY id DESC
         LIMIT 1
        """,
        email,
    )
    role_row = await db.fetchrow(
        "SELECT role FROM bot_roles WHERE email = $1", email,
    )
    latest = await db.fetchrow(
        """
        SELECT pa.*, o.factory_name, o.shift, o.location
          FROM person_assignments pa
          LEFT JOIN operations o ON o.op_id = pa.op_id
         WHERE pa.email = $1
         ORDER BY pa.assignment_date DESC, pa.created_at DESC
         LIMIT 1
        """,
        email,
    )
    return {
        "email":                  email,
        "name":                   claims.get("name"),
        "onboarded":              sub is not None,
        "submission":             _row_dict(sub),
        "system_role":            role_row["role"] if role_row else None,
        "latest_self_assignment": _row_dict(latest),
    }


@router.get("/active-ops")
async def me_active_ops(request: Request, authorization: Optional[str] = Header(None)):
    """Return active ops with their assigned chiefs + captains so the
    onboarding update form can build cascading dropdowns without making
    one network round-trip per op."""
    _decode(authorization)
    db = request.app.state.db
    ops = await db.fetch(
        """
        SELECT op_id, factory_name, shift, location, sales_team_name
          FROM operations
         WHERE is_active
         ORDER BY factory_name, shift
        """
    )
    asn = await db.fetch(
        "SELECT op_id, email, role FROM op_assignments ORDER BY op_id, role, email"
    )
    by_op: dict[str, list[dict]] = {}
    for a in asn:
        by_op.setdefault(a["op_id"], []).append({"email": a["email"], "role": a["role"]})
    rows = []
    for o in ops:
        d = dict(o)
        d["assignments"] = by_op.get(d["op_id"], [])
        rows.append(d)
    return {"rows": rows}


class AssignmentIn(BaseModel):
    op_id: str
    role: str
    reports_to_chief_email: Optional[str] = None
    notes: Optional[str] = None


@router.post("/assignment")
async def me_post_assignment(
    body: AssignmentIn,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    claims = _decode(authorization)
    email = (claims.get("email") or "").lower()
    if not email:
        raise HTTPException(400, "no email in token")
    if body.role not in ALLOWED_SELF_ROLES:
        raise HTTPException(400, f"role must be one of {sorted(ALLOWED_SELF_ROLES)}")

    db = request.app.state.db
    op_active = await db.fetchval(
        "SELECT is_active FROM operations WHERE op_id = $1", body.op_id,
    )
    if op_active is None:
        raise HTTPException(400, "unknown op_id")
    if not op_active:
        raise HTTPException(400, "operation is inactive")

    chief_email = (body.reports_to_chief_email or "").strip().lower() or None
    notes = (body.notes or "").strip() or None
    today = datetime.now(IST).date()

    row = await db.fetchrow(
        """
        INSERT INTO person_assignments (
            email, assignment_date, op_id, role,
            reports_to_chief_email, notes, source
        ) VALUES ($1, $2, $3, $4, $5, $6, 'self')
        ON CONFLICT (email, assignment_date, source) DO UPDATE SET
            op_id                  = EXCLUDED.op_id,
            role                   = EXCLUDED.role,
            reports_to_chief_email = EXCLUDED.reports_to_chief_email,
            notes                  = EXCLUDED.notes,
            created_at             = NOW()
        RETURNING *
        """,
        email, today, body.op_id, body.role, chief_email, notes,
    )
    return _row_dict(row)
