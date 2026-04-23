from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import Literal, Optional

from deps import require_current_role

router = APIRouter()

# Role management belongs to marshal + general. Each handler enforces the
# hierarchy inside: marshal > general > chief / viewer / captain.
freddy_or_general = require_current_role("freddy", "general")


class RoleIn(BaseModel):
    email: EmailStr
    role: Literal["freddy", "general", "chief", "viewer"] = "chief"
    can_create_ops: bool = False


class PermPatch(BaseModel):
    can_create_ops: Optional[bool] = None


def _row_out(row) -> dict:
    d = dict(row)
    if d.get("added_at") is not None:
        d["added_at"] = d["added_at"].isoformat()
    return d


def _caller_can_target(caller_role: str, target_role: str) -> bool:
    """marshal can touch anyone; general can only touch chief/viewer."""
    if caller_role == "freddy":
        return target_role in ("freddy", "general", "chief", "viewer", "captain")
    if caller_role == "general":
        return target_role in ("chief", "viewer")
    return False


@router.get("")
async def list_roles(request: Request, _claims: dict = Depends(freddy_or_general)):
    rows = await request.app.state.db.fetch(
        "SELECT email, role, can_create_ops, added_at FROM bot_roles ORDER BY added_at DESC"
    )
    return {"rows": [_row_out(r) for r in rows]}


@router.post("")
async def add_role(body: RoleIn, request: Request, claims: dict = Depends(freddy_or_general)):
    """Marshal: can add marshal / general / chief / viewer.
       General: can only add chief / viewer.
    """
    caller_role = claims["role"]
    if not _caller_can_target(caller_role, body.role):
        raise HTTPException(403, f"a {caller_role} cannot assign role={body.role}")

    email = body.email.lower()
    # Only chief / general / marshal can be flagged can_create_ops; viewer always false.
    cco = body.can_create_ops if body.role in ("chief", "general", "freddy") else False

    existing = await request.app.state.db.fetchval(
        "SELECT role FROM bot_roles WHERE email = $1", email,
    )
    if existing and not _caller_can_target(caller_role, existing):
        raise HTTPException(403, f"a {caller_role} cannot overwrite an existing {existing}")

    row = await request.app.state.db.fetchrow(
        """
        INSERT INTO bot_roles (email, role, can_create_ops) VALUES ($1, $2, $3)
        ON CONFLICT (email) DO UPDATE SET
            role = EXCLUDED.role,
            can_create_ops = EXCLUDED.can_create_ops
        RETURNING email, role, can_create_ops, added_at
        """,
        email, body.role, cco,
    )
    return _row_out(row)


@router.patch("/{email}")
async def patch_perms(email: str, body: PermPatch, request: Request, claims: dict = Depends(freddy_or_general)):
    if body.can_create_ops is None:
        raise HTTPException(400, "nothing to update")
    target_row = await request.app.state.db.fetchrow(
        "SELECT role FROM bot_roles WHERE email = $1", email.lower(),
    )
    if not target_row:
        raise HTTPException(404, "email not found in bot_roles")
    if not _caller_can_target(claims["role"], target_row["role"]):
        raise HTTPException(403, "insufficient privilege for this role")
    row = await request.app.state.db.fetchrow(
        """
        UPDATE bot_roles SET can_create_ops = $1 WHERE email = $2
        RETURNING email, role, can_create_ops, added_at
        """,
        body.can_create_ops, email.lower(),
    )
    return _row_out(row)


@router.delete("/{email}")
async def delete_role(email: str, request: Request, claims: dict = Depends(freddy_or_general)):
    target = email.lower()
    if target == (claims.get("email") or "").lower():
        raise HTTPException(400, "cannot remove your own role")
    row = await request.app.state.db.fetchrow(
        "SELECT role FROM bot_roles WHERE email = $1", target,
    )
    if not row:
        raise HTTPException(404, "email not found in bot_roles")
    if not _caller_can_target(claims["role"], row["role"]):
        raise HTTPException(403, f"a {claims['role']} cannot remove a {row['role']}")
    await request.app.state.db.execute(
        "DELETE FROM bot_roles WHERE email = $1", target,
    )
    return {"ok": True, "removed": target}
