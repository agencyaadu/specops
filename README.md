# SpecOps Onboarding

A minimal onboarding form. **Supabase** stores everything — Postgres for data, Storage for files.

## Structure

```
specops/
  backend/          FastAPI app
  frontend/         Single HTML file
```

## 1. Supabase Setup

1. Create a project at https://supabase.com
2. **Database** → copy the connection string from *Project Settings → Database → Connection string → "Transaction pooler"*. Put it in `DATABASE_URL`.
3. **Storage** → create a bucket named `submissions` (or whatever you set in `SUPABASE_BUCKET`). Make it **public** so the stored URLs are viewable.
4. **API** → copy `Project URL` → `SUPABASE_URL`, and `service_role` key → `SUPABASE_SERVICE_ROLE_KEY`. The service-role key bypasses RLS and is safe here because only the backend holds it.

The `submissions` table is created automatically on first boot by [backend/db.py](backend/db.py).

## 2. Google OAuth (sign-in on the form)

1. Create a Google Cloud project
2. Enable **OAuth 2.0**
3. Create an OAuth 2.0 Web Client → add `http://localhost:8000/auth/google/callback` as an authorized redirect URI
4. Copy Client ID / Secret into `.env`

## 3. Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in all values
uvicorn main:app --reload
```

## 4. Frontend

Open `frontend/index.html` directly, or serve it. Point it at your backend:

```html
<script>window.BACKEND_URL = 'https://your-api.example.com'</script>
```

## Environment Variables

| Variable | What it is |
|---|---|
| DATABASE_URL | Supabase Postgres connection string (transaction pooler URL, port 6543) |
| SUPABASE_URL | `https://<project>.supabase.co` |
| SUPABASE_SERVICE_ROLE_KEY | Service-role key (backend-only) |
| SUPABASE_BUCKET | Storage bucket name, e.g. `submissions` |
| GOOGLE_CLIENT_ID | From Google Cloud Console |
| GOOGLE_CLIENT_SECRET | Same |
| GOOGLE_REDIRECT_URI | Must match what's registered in GCP |
| JWT_SECRET | Any long random string |
| ENCRYPTION_KEY | Any long random string — derives the AES key for PAN / account number |
| FRONTEND_URL | Where `index.html` is served from |
| ALLOWED_ORIGINS | Comma-separated CORS origins |

## Data Flow

```
form submit
  → FastAPI
      → encrypt PAN + account number (AES via Fernet)
      → upload files to Supabase Storage
      → INSERT into Supabase Postgres
  → return { ok: true, id: N }
```

## What's Stored Where

- **Postgres (`submissions` table):** every form field. Sensitive fields (PAN, account number) are AES-encrypted at rest.
- **Storage (`submissions` bucket):** the PAN card image/PDF and the intro video. The DB row keeps the public URL.

## Notes

- `statement_cache_size=0` in [backend/main.py](backend/main.py) keeps asyncpg safe behind Supabase's transaction pooler.
- To rotate the encryption key you need to decrypt old rows with the old key and re-encrypt with the new one — don't just change `ENCRYPTION_KEY` on a populated DB.
