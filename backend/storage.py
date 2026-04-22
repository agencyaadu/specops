import os
import httpx

SUPABASE_URL        = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY         = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ONBOARDING_BUCKET   = os.environ["SUPABASE_BUCKET"]
ATTENDANCE_BUCKET   = os.environ["SUPABASE_ATTENDANCE_BUCKET"]

def _upload(bucket: str, file_bytes: bytes, key: str, mime_type: str) -> str:
    url = f"{SUPABASE_URL}/storage/v1/object/{bucket}/{key}"
    headers = {
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type":  mime_type,
        "x-upsert":      "true",
    }
    r = httpx.post(url, content=file_bytes, headers=headers, timeout=120)
    r.raise_for_status()
    return key

def upload_to_storage(file_bytes: bytes, key: str, mime_type: str) -> str:
    """Upload to the public onboarding bucket; return public URL."""
    _upload(ONBOARDING_BUCKET, file_bytes, key, mime_type)
    return f"{SUPABASE_URL}/storage/v1/object/public/{ONBOARDING_BUCKET}/{key}"

def upload_attendance_photo(file_bytes: bytes, key: str, mime_type: str) -> str:
    """Upload to the private attendance bucket; return the object key (sign on read)."""
    return _upload(ATTENDANCE_BUCKET, file_bytes, key, mime_type)

def sign_attendance_url(key: str, expires_in: int = 24 * 3600) -> str:
    """Return a time-limited signed URL for a private attendance-bucket object."""
    url = f"{SUPABASE_URL}/storage/v1/object/sign/{ATTENDANCE_BUCKET}/{key}"
    headers = {
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type":  "application/json",
    }
    r = httpx.post(url, json={"expiresIn": expires_in}, headers=headers, timeout=30)
    r.raise_for_status()
    signed = r.json().get("signedURL") or r.json().get("signedUrl")
    return f"{SUPABASE_URL}/storage/v1{signed}" if signed else ""
