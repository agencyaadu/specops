import os
import httpx

SUPABASE_URL    = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY     = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
BUCKET          = os.environ["SUPABASE_BUCKET"]

def upload_to_storage(file_bytes: bytes, key: str, mime_type: str) -> str:
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{key}"
    headers = {
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type":  mime_type,
        "x-upsert":      "true",
    }
    r = httpx.post(url, content=file_bytes, headers=headers, timeout=120)
    r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{key}"
