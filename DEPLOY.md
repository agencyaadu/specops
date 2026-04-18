# Deploying SpecOps

Two services to stand up, plus a few one-time wiring steps.

## 1 — Backend to Render

1. Go to https://dashboard.render.com/ → **New** → **Blueprint** → point it at this repo (`main` branch). Render will read `render.yaml` and create the `specops-api` web service.
2. After it creates, open the service and fill in the env vars marked `sync: false`:
   - `DATABASE_URL` — Supabase transaction-pooler URL (**password must be URL-encoded**; literal `@` → `%40`)
   - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` — from Supabase → API
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_REDIRECT_URI` — `https://<your-render-subdomain>.onrender.com/auth/google/callback`
   - `JWT_SECRET` — any 32+ char random string
   - `ENCRYPTION_KEY` — must match the local value if you want to decrypt rows created locally; otherwise any 32+ char random string
   - `ADMIN_PASSWORD` — strong random password
   - `FRONTEND_URL` — `https://<your-netlify-subdomain>.netlify.app`
   - `ALLOWED_ORIGINS` — same as `FRONTEND_URL` (comma-separate if you have more)
3. Deploy. First boot takes ~2–3 min (pip install). Confirm `GET /health` returns `{"status":"ok"}`.

## 2 — Frontend to Vercel

1. Edit `frontend/config.js` → replace `REPLACE-WITH-RENDER-URL.onrender.com` with your actual Render URL (no trailing slash). `config.js` is served with `Cache-Control: no-cache` so future edits take effect immediately.
2. Commit & push.
3. On https://vercel.com/new → **Import Git Repository** → pick `Kawwshall/specops`. Vercel reads `vercel.json` and serves from `frontend/` — no build step needed (`framework: null`, `buildCommand: null`).
4. Deploy. You get a `<name>.vercel.app` URL (plus preview URLs per-branch).
5. Go back to Render and set `FRONTEND_URL` and `ALLOWED_ORIGINS` to the final Vercel URL (e.g. `https://specops.vercel.app`).

## 3 — Google OAuth

In Google Cloud Console → Credentials → your OAuth 2.0 Client → **Authorized redirect URIs**, add:

```
https://<your-render-subdomain>.onrender.com/auth/google/callback
```

Save. No code change needed.

## 4 — Smoke test

- Open `https://<vercel>/` → fill the form → submit → welcome screen shows → row appears at `https://<vercel>/admin.html`
- Open `https://<vercel>/admin.html` → sign in with `ADMIN_PASSWORD` → rows list loads → click a row → plaintext PAN/account visible → files preview

## Notes

- The backend's free Render plan sleeps after 15 min of inactivity — first request after sleep takes ~30s. Upgrade to Starter ($7/mo) to keep it warm.
- Render's default request body limit is 100 MB. The intro-video upload cap (200 MB) will fail on free tier until you upgrade or lower the cap in `backend/routers/submissions.py`.
- `render.yaml` has `JWT_SECRET` and `ENCRYPTION_KEY` marked `sync: false`, meaning Render will prompt for them and never echo them back in the dashboard. They stay in Render's encrypted store.
- **Do not** regenerate `ENCRYPTION_KEY` against a populated DB — existing encrypted columns become unreadable.
