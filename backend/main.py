from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = [
    "DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_BUCKET", "SUPABASE_ATTENDANCE_BUCKET",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
    "JWT_SECRET", "ENCRYPTION_KEY", "ADMIN_PASSWORD",
    "FRONTEND_URL", "ALLOWED_ORIGINS",
]
_missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
if _missing:
    raise SystemExit(f"FATAL: missing required env vars: {', '.join(_missing)}")

_allowed_origins = [o.strip() for o in os.environ["ALLOWED_ORIGINS"].split(",") if o.strip()]
if not _allowed_origins or "*" in _allowed_origins:
    raise SystemExit("FATAL: ALLOWED_ORIGINS must be an explicit comma-separated list (no wildcard)")

import logging
from routers import submissions, auth, admin, ops, reports, daily, roles, assignments, notes, dashboard, analytics, me, validation, reminders
from db import init_db
import sheets as _sheets

_log = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # statement_cache_size=0 keeps this safe behind Supabase's transaction pooler (pgbouncer)
    app.state.db = await asyncpg.create_pool(
        os.environ["DATABASE_URL"],
        statement_cache_size=0,
        min_size=1,
        max_size=10,
    )
    await init_db(app.state.db)
    if _sheets.sheets_enabled():
        try:
            import asyncio
            await asyncio.to_thread(_sheets.ensure_header)
        except Exception:
            _log.warning("sheets header init failed", exc_info=True)
    yield
    await app.state.db.close()

app = FastAPI(title="SPEC-OPS Onboarding", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router,        prefix="/auth",      tags=["auth"])
app.include_router(submissions.router, prefix="/submit",    tags=["submit"])
app.include_router(admin.router,       prefix="/admin",     tags=["admin"])
app.include_router(ops.router,         prefix="/api/ops",   tags=["ops"])
app.include_router(assignments.router, prefix="/api/ops",   tags=["assignments"])
app.include_router(reports.router,     prefix="/api/op",    tags=["reports"])
app.include_router(daily.router,       prefix="/api/daily", tags=["daily"])
app.include_router(roles.router,       prefix="/api/roles", tags=["roles"])
app.include_router(notes.router,       prefix="/api/notes", tags=["notes"])
app.include_router(dashboard.router,   prefix="/api/dashboard", tags=["dashboard"])
app.include_router(analytics.router,   prefix="/api/analytics", tags=["analytics"])
app.include_router(me.router,          prefix="/api/me",        tags=["me"])
app.include_router(validation.router,  prefix="/api/validation", tags=["validation"])
app.include_router(reminders.router,   prefix="/api/reminders",  tags=["reminders"])

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}
