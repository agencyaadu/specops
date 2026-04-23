from fastapi import HTTPException, Header, Request
from typing import Optional
import os
import jwt

JWT_SECRET = os.environ["JWT_SECRET"]

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

def require_role(*allowed_roles: str):
    allowed = set(allowed_roles)
    async def _dep(authorization: Optional[str] = Header(None)) -> dict:
        claims = _decode(authorization)
        if claims.get("role") not in allowed:
            raise HTTPException(403, "insufficient role")
        return claims
    return _dep

def require_current_role(*allowed_roles: str):
    """Like require_role but re-fetches the current role from bot_roles so that
    permission changes take effect without re-login. Also surfaces can_create_ops."""
    allowed = set(allowed_roles)
    async def _dep(request: Request, authorization: Optional[str] = Header(None)) -> dict:
        claims = _decode(authorization)
        email = (claims.get("email") or "").lower()
        row = await request.app.state.db.fetchrow(
            "SELECT role, can_create_ops FROM bot_roles WHERE email = $1", email,
        )
        if not row:
            raise HTTPException(403, "not in bot_roles")
        if row["role"] not in allowed:
            raise HTTPException(403, "insufficient role")
        claims["role"] = row["role"]
        claims["can_create_ops"] = bool(row["can_create_ops"])
        return claims
    return _dep

async def has_op_access(request: Request, claims: dict, op_id: str) -> bool:
    """True if the claim holder can act on the given op (owner/general, or assigned chief/captain)."""
    if claims.get("role") in ("freddy", "general"):
        return True
    email = (claims.get("email") or "").lower()
    role  = claims.get("role")
    if role not in ("chief", "captain") or not email:
        return False
    ok = await request.app.state.db.fetchval(
        "SELECT 1 FROM op_assignments WHERE op_id = $1 AND email = $2",
        op_id, email,
    )
    return bool(ok)

async def require_op_access(request: Request, claims: dict, op_id: str) -> dict:
    if not await has_op_access(request, claims, op_id):
        raise HTTPException(403, "not assigned to this operation")
    return claims
