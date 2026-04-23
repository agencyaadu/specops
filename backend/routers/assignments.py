from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import Literal

from deps import require_current_role, has_op_access

router = APIRouter()

general_or_chief = require_current_role("freddy", "general", "chief")

class AssignIn(BaseModel):
    email: EmailStr
    role: Literal["chief", "captain"]

def _row_out(row) -> dict:
    d = dict(row)
    if d.get("added_at") is not None:
        d["added_at"] = d["added_at"].isoformat()
    return d

async def _ensure_op_exists(request: Request, op_id: str) -> None:
    ok = await request.app.state.db.fetchval(
        "SELECT 1 FROM operations WHERE op_id = $1", op_id,
    )
    if not ok:
        raise HTTPException(404, "operation not found")

async def _caller_can_manage(request: Request, claims: dict, op_id: str, target_role: str) -> bool:
    caller_role = claims["role"]
    if caller_role in ("freddy", "general"):
        # Owner/general can manage either role on any op.
        return True
    if caller_role == "chief":
        # Chief can manage captains ONLY, and ONLY for ops they're assigned to.
        if target_role != "captain":
            return False
        return await has_op_access(request, claims, op_id)
    return False

@router.get("/{op_id}/assignments")
async def list_assignments(op_id: str, request: Request, claims: dict = Depends(general_or_chief)):
    await _ensure_op_exists(request, op_id)
    # Generals see all. Chiefs must be assigned to see this op's roster.
    if claims["role"] not in ("freddy", "general") and not await has_op_access(request, claims, op_id):
        raise HTTPException(403, "not assigned to this operation")
    rows = await request.app.state.db.fetch(
        """
        SELECT email, role, assigned_by_email, added_at
          FROM op_assignments
         WHERE op_id = $1
         ORDER BY role, email
        """,
        op_id,
    )
    return {"op_id": op_id, "rows": [_row_out(r) for r in rows]}

@router.post("/{op_id}/assignments")
async def add_assignment(
    op_id: str, body: AssignIn, request: Request, claims: dict = Depends(general_or_chief),
):
    await _ensure_op_exists(request, op_id)
    if not await _caller_can_manage(request, claims, op_id, body.role):
        raise HTTPException(403, "cannot assign this role here")

    email = body.email.lower()
    caller_email = (claims.get("email") or "").lower()

    async with request.app.state.db.acquire() as conn:
        async with conn.transaction():
            # Upsert bot_roles so the target can sign in.
            # Don't downgrade a general; otherwise pick the higher of (existing, body.role).
            await conn.execute(
                """
                INSERT INTO bot_roles (email, role) VALUES ($1, $2)
                ON CONFLICT (email) DO UPDATE SET
                    role = CASE
                        WHEN bot_roles.role = 'general' THEN bot_roles.role
                        WHEN bot_roles.role = 'chief' AND EXCLUDED.role = 'captain' THEN 'chief'
                        ELSE EXCLUDED.role
                    END
                """,
                email, body.role,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO op_assignments (op_id, email, role, assigned_by_email)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (op_id, email) DO UPDATE SET role = EXCLUDED.role
                RETURNING email, role, assigned_by_email, added_at
                """,
                op_id, email, body.role, caller_email,
            )
    return _row_out(row)

@router.delete("/{op_id}/assignments/{email}")
async def remove_assignment(
    op_id: str, email: str, request: Request, claims: dict = Depends(general_or_chief),
):
    await _ensure_op_exists(request, op_id)
    target = email.lower()
    row = await request.app.state.db.fetchrow(
        "SELECT role FROM op_assignments WHERE op_id = $1 AND email = $2",
        op_id, target,
    )
    if not row:
        raise HTTPException(404, "assignment not found")
    if not await _caller_can_manage(request, claims, op_id, row["role"]):
        raise HTTPException(403, "cannot remove this assignment")
    await request.app.state.db.execute(
        "DELETE FROM op_assignments WHERE op_id = $1 AND email = $2",
        op_id, target,
    )
    return {"ok": True, "removed": {"op_id": op_id, "email": target}}
