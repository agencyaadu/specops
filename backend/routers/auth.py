from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode, urlparse
from typing import Optional
import httpx
import os
import jwt
import time

router = APIRouter()

GOOGLE_AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REDIRECT_URI  = os.environ["GOOGLE_REDIRECT_URI"]
JWT_SECRET    = os.environ["JWT_SECRET"]
FRONTEND_URL  = os.environ["FRONTEND_URL"].rstrip("/")

def _safe_next(next_val: Optional[str]) -> str:
    """Accept only relative paths or URLs under FRONTEND_URL; default to FRONTEND_URL."""
    if not next_val:
        return FRONTEND_URL
    if next_val.startswith("/"):
        return FRONTEND_URL + next_val
    parsed = urlparse(next_val)
    fe = urlparse(FRONTEND_URL)
    if parsed.scheme in ("http", "https") and parsed.netloc == fe.netloc:
        return next_val
    return FRONTEND_URL

@router.get("/google")
async def google_login(next: Optional[str] = None):
    params = {
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile",
        "access_type":   "offline",
    }
    if next:
        params["state"] = next
    url = GOOGLE_AUTH_URL + "?" + urlencode(params)
    return RedirectResponse(url)

@router.get("/google/callback")
async def google_callback(code: str, request: Request, state: Optional[str] = None):
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code":          code,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        })
        token_data = token_resp.json()
        if "error" in token_data:
            raise HTTPException(400, token_data["error"])

        user_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        user = user_resp.json()

    email = (user.get("email") or "").lower()
    role = None
    db = getattr(request.app.state, "db", None)
    if db and email:
        role = await db.fetchval("SELECT role FROM bot_roles WHERE email = $1", email)

    claims = {
        "sub":     user["sub"],
        "email":   email,
        "name":    user.get("name", ""),
        "picture": user.get("picture", ""),
        "exp":     int(time.time()) + 3600,
    }
    if role:
        claims["role"] = role

    session_token = jwt.encode(claims, JWT_SECRET, algorithm="HS256")

    target = _safe_next(state)
    sep = "&" if "?" in target else "?"
    return RedirectResponse(f"{target}{sep}token={session_token}")

@router.get("/verify")
async def verify_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return {"valid": True, "user": payload}
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token")
