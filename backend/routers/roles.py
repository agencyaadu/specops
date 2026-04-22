from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import Literal, Optional

from deps import require_current_role

router = APIRouter()

general_only = require_current_role("general")

class ChiefIn(BaseModel):
    email: EmailStr
    can_create_ops: bool = False

class PermPatch(BaseModel):
    can_create_ops: Optional[bool] = None

def _row_out(row) -> dict:
    d = dict(row)
    if d.get("added_at") is not None:
        d["added_at"] = d["added_at"].isoformat()
    return d

@router.get("")
async def list_roles(request: Request, _claims: dict = Depends(general_only)):
    rows = await request.app.state.db.fetch(
        "SELECT email, role, can_create_ops, added_at FROM bot_roles ORDER BY added_at DESC"
    )
    return {"rows": [_row_out(r) for r in rows]}

@router.post("")
async def add_chief(body: ChiefIn, request: Request, _claims: dict = Depends(general_only)):
    """General adds someone as a chief (and optionally grants op-creation)."""
    email = body.email.lower()
    row = await request.app.state.db.fetchrow(
        """
        INSERT INTO bot_roles (email, role, can_create_ops) VALUES ($1, 'chief', $2)
        ON CONFLICT (email) DO UPDATE SET
            role = CASE
                WHEN bot_roles.role = 'general' THEN bot_roles.role
                ELSE 'chief'
            END,
            can_create_ops = EXCLUDED.can_create_ops
        RETURNING email, role, can_create_ops, added_at
        """,
        email, body.can_create_ops,
    )
    return _row_out(row)

@router.patch("/{email}")
async def patch_perms(email: str, body: PermPatch, request: Request, _claims: dict = Depends(general_only)):
    if body.can_create_ops is None:
        raise HTTPException(400, "nothing to update")
    row = await request.app.state.db.fetchrow(
        """
        UPDATE bot_roles SET can_create_ops = $1 WHERE email = $2
        RETURNING email, role, can_create_ops, added_at
        """,
        body.can_create_ops, email.lower(),
    )
    if not row:
        raise HTTPException(404, "email not found in bot_roles")
    return _row_out(row)

@router.delete("/{email}")
async def delete_role(email: str, request: Request, claims: dict = Depends(general_only)):
    target = email.lower()
    if target == (claims.get("email") or "").lower():
        raise HTTPException(400, "cannot remove your own role")
    row = await request.app.state.db.fetchrow(
        "SELECT role FROM bot_roles WHERE email = $1", target,
    )
    if not row:
        raise HTTPException(404, "email not found in bot_roles")
    if row["role"] == "general":
        raise HTTPException(400, "cannot remove a general role entry from here")
    deleted = await request.app.state.db.execute(
        "DELETE FROM bot_roles WHERE email = $1", target,
    )
    return {"ok": True, "removed": target}
