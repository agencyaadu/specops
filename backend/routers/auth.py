from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
import httpx
import os
import jwt
import time

router = APIRouter()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
REDIRECT_URI  = os.environ["GOOGLE_REDIRECT_URI"]
JWT_SECRET    = os.environ["JWT_SECRET"]
FRONTEND_URL  = os.environ["FRONTEND_URL"]

@router.get("/google")
async def google_login():
    params = {
        "client_id":     CLIENT_ID ,
        "redirect_uri":  REDIRECT_URI ,
        "response_type": "code" ,
        "scope":         "openid email profile" ,
        "access_type":   "offline" ,
    }
    url = GOOGLE_AUTH_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(url)

@router.get("/google/callback")
async def google_callback(code: str, request: Request):
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code":          code ,
            "client_id":     CLIENT_ID ,
            "client_secret": CLIENT_SECRET ,
            "redirect_uri":  REDIRECT_URI ,
            "grant_type":    "authorization_code" ,
        })
        token_data = token_resp.json()
        if "error" in token_data:
            raise HTTPException(400, token_data["error"])

        user_resp = await client.get(
            GOOGLE_USERINFO_URL ,
            headers={"Authorization": f"Bearer {token_data['access_token']}"}
        )
        user = user_resp.json()

    session_token = jwt.encode({
        "sub":     user["sub"] ,
        "email":   user["email"] ,
        "name":    user.get("name", "") ,
        "picture": user.get("picture", "") ,
        "exp":     int(time.time()) + 3600 ,
    }, JWT_SECRET, algorithm="HS256")

    return RedirectResponse(f"{FRONTEND_URL}?token={session_token}")

@router.get("/verify")
async def verify_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return {"valid": True, "user": payload}
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token")
