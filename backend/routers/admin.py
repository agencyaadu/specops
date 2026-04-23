from fastapi import APIRouter, Request, HTTPException, Header, Query
from pydantic import BaseModel
from typing import Optional
import asyncio
import hmac
import os
import time
import jwt

from crypto import decrypt
import sheets as _sheets

router = APIRouter()

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
JWT_SECRET     = os.environ["JWT_SECRET"]
TOKEN_TTL_SEC  = 8 * 3600

# In-process rate limit for /admin/login. 5 failures within 10 min -> 10 min lock per IP.
_LOGIN_WINDOW_S  = 600
_LOGIN_MAX_FAILS = 5
_login_attempts: dict = {}   # ip -> list[float]  (failure timestamps)

def _rate_limit_check(ip: str) -> None:
    now = time.time()
    fails = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_WINDOW_S]
    _login_attempts[ip] = fails
    if len(fails) >= _LOGIN_MAX_FAILS:
        retry = int(_LOGIN_WINDOW_S - (now - fails[0]))
        raise HTTPException(429, f"too many attempts; try again in {retry}s")

def _record_failure(ip: str) -> None:
    _login_attempts.setdefault(ip, []).append(time.time())

def _clear_failures(ip: str) -> None:
    _login_attempts.pop(ip, None)

class LoginBody(BaseModel):
    password: str

def _require_admin(authorization: Optional[str]) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token")
    if payload.get("scope") != "admin":
        raise HTTPException(401, "not an admin token")
    return payload

@router.post("/login")
async def admin_login(body: LoginBody, request: Request):
    ip = (request.client.host if request.client else "unknown")
    _rate_limit_check(ip)
    # constant-time compare to avoid timing side-channel
    if not hmac.compare_digest(body.password, ADMIN_PASSWORD):
        _record_failure(ip)
        raise HTTPException(401, "wrong password")
    _clear_failures(ip)
    token = jwt.encode({
        "scope": "admin",
        "exp":   int(time.time()) + TOKEN_TTL_SEC,
    }, JWT_SECRET, algorithm="HS256")
    return {"token": token, "expires_in": TOKEN_TTL_SEC}

def _row_to_dict(row) -> dict:
    d = dict(row)
    d["pan_number"]     = decrypt(d.pop("pan_number_enc", "") or "")
    d["account_number"] = decrypt(d.pop("account_number_enc", "") or "")
    if d.get("created_at") is not None:
        d["created_at"] = d["created_at"].isoformat()
    return d

@router.get("/submissions")
async def list_submissions(
    request: Request,
    authorization: Optional[str] = Header(None),
    q: Optional[str] = Query(None, description="search name or email"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    _require_admin(authorization)
    db = request.app.state.db
    if q:
        rows = await db.fetch(
            """
            SELECT * FROM submissions
             WHERE full_name ILIKE $1 OR email ILIKE $1
             ORDER BY id DESC
             LIMIT $2 OFFSET $3
            """,
            f"%{q}%", limit, offset,
        )
    else:
        rows = await db.fetch(
            "SELECT * FROM submissions ORDER BY id DESC LIMIT $1 OFFSET $2",
            limit, offset,
        )
    total = await db.fetchval("SELECT COUNT(*) FROM submissions")
    return {"total": total, "rows": [_row_to_dict(r) for r in rows]}

@router.post("/sync-sheets")
async def sync_sheets(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    _require_admin(authorization)
    if not _sheets.sheets_enabled():
        raise HTTPException(503, "Google Sheets not configured (GOOGLE_SHEETS_ID / GOOGLE_SERVICE_ACCOUNT_JSON missing)")
    db = request.app.state.db
    rows = await db.fetch("SELECT * FROM submissions ORDER BY id ASC")
    dicts = [_row_to_dict(r) for r in rows]
    await asyncio.to_thread(_sheets.full_sync, dicts)
    return {"synced": len(dicts)}

@router.get("/submissions/{sub_id}")
async def get_submission(
    sub_id: int,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    _require_admin(authorization)
    db = request.app.state.db
    row = await db.fetchrow("SELECT * FROM submissions WHERE id = $1", sub_id)
    if not row:
        raise HTTPException(404, "not found")
    return _row_to_dict(row)
