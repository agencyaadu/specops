from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = [
    "DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_BUCKET",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI",
    "JWT_SECRET", "ENCRYPTION_KEY", "ADMIN_PASSWORD", "FRONTEND_URL",
]
_missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
if _missing:
    raise SystemExit(f"FATAL: missing required env vars: {', '.join(_missing)}")

from routers import submissions, auth, admin
from db import init_db

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
    yield
    await app.state.db.close()

app = FastAPI(title="SpecOps Onboarding", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(submissions.router, prefix="/submit", tags=["submit"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])

@app.get("/health")
async def health():
    return {"status": "ok"}
