# Draftease — set up & test from a web address

The redline engine now has a small web app (`app.py`) in front of it. You upload a
form lease, paste deal terms, and download the tracked-changes redline — all in the
browser. Three ways to reach it, easiest first.

---

## 1. Local URL (2 minutes — start here)

```bash
# from the project folder
pip install -r requirements.txt
uvicorn app:app --reload
```

Open **http://127.0.0.1:8000**. You'll land on a **login page** — click
*Create one* to make your first account, and you're in. (Accounts are stored in a
local `draftease.db` with bcrypt-hashed passwords; sessions are signed cookies.)

Once signed in, the redline page is pre-filled with the sample deal terms, so to
test immediately:

```bash
python make_sample_lease.py        # creates sample_form_lease.docx
```

…then on the page, choose `sample_form_lease.docx`, leave the terms as-is, and click
**Generate redline**. Your browser downloads `sample_form_lease_redline.docx`. Open
it in Word — you'll see the tracked changes.

`--reload` restarts the server whenever you edit the code, so you can iterate live.

---

## 2. Temporary public URL (share a test link in ~1 minute)

Keep the local server running (step 1), then open a tunnel in a second terminal.
This gives anyone a public https link that points at your machine — great for a quick
demo, not for production.

**Cloudflare (no signup):**
```bash
brew install cloudflared        # mac;  or see cloudflare docs
cloudflared tunnel --url http://127.0.0.1:8000
```
It prints a `https://something.trycloudflare.com` URL. That's your web address.

**ngrok (alternative):**
```bash
ngrok http 8000
```

---

## 3. Persistent public URL (a real deployment)

There's a `Dockerfile`, so any container host works. Two simple paths:

**Render / Railway (managed, fastest):**
1. Push these files to a GitHub repo.
2. Create a new **Web Service** from the repo. They auto-detect the Dockerfile.
3. No start command needed (the Dockerfile handles `$PORT`). Deploy.
4. You get a permanent `https://your-app.onrender.com` URL with HTTPS included.

**Fly.io (also simple):**
```bash
fly launch          # detects the Dockerfile, pick a region
fly deploy
```

**Run the container anywhere yourself:**
```bash
docker build -t draftease .
docker run -p 8000:8000 draftease
# then http://localhost:8000  (or your server's IP/domain)
```

---

## 4. Your own cloud (matches the privacy story)

Because the engine sends nothing to any third party, hosting it in **your** cloud is
the on-brand choice. The same container deploys to:
- **AWS App Runner** or **ECS/Fargate** (point at the image or repo),
- **Google Cloud Run** (`gcloud run deploy --source .`),
- **Azure Container Apps**.

All terminate HTTPS for you and keep documents inside your tenant.

---

## Before real users touch it (don't skip)

This is a test harness, not a finished SaaS. Add, in roughly this order:
1. **Auth** (logins / tenants) — right now anyone with the URL can use it.
2. **Don't keep documents on the server** — it already processes in a temp dir and
   streams the result back; keep it that way, or store encrypted per-tenant.
3. **A human review gate** before a redline is treated as final (legal documents).
4. The **LOI extraction step** (in your tenant) to fill the terms automatically
   instead of pasting JSON.
5. File-type/size limits are in place (15 MB, .docx only); add rate limiting.

## Authentication

The app ships with a real login system: user accounts in a database, bcrypt-hashed
passwords, signed HttpOnly session cookies, and CSRF protection on every form. The
redline tool is gated — visiting `/` while logged out sends you to `/login`.

Configure it with environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DRAFTEASE_SECRET_KEY` | auto-generated to `.draftease_secret` | **Set this in production** to a long random string so sessions are stable and secret. |
| `DRAFTEASE_DB_URL` | `sqlite:///draftease.db` | Swap for Postgres in prod, e.g. `postgresql+psycopg://user:pass@host/db`. |
| `DRAFTEASE_ALLOW_SIGNUP` | `true` | Set `false` to close public signup once your accounts exist. |
| `DRAFTEASE_HTTPS_ONLY` | `false` | Set `true` in production so the session cookie is HTTPS-only. |

Example production start:
```bash
export DRAFTEASE_SECRET_KEY="$(openssl rand -base64 48)"
export DRAFTEASE_HTTPS_ONLY=true
export DRAFTEASE_ALLOW_SIGNUP=false
uvicorn app:app --host 0.0.0.0 --port 8000
```

What's still worth adding before real customers: email verification / password reset,
multi-tenant org accounts (so each company sees only its own templates), login rate
limiting, and an audit log. The current schema (`auth.py`) is structured so these slot
in without reworking the web layer.

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/login`, `/signup` | public | Auth pages |
| POST | `/login`, `/signup`, `/logout` | public | Auth actions (CSRF-protected) |
| GET | `/` | required | Redline tool |
| POST | `/redline` | required | `lease` (.docx) + `terms` (JSON) → redline .docx |
| GET | `/health` | public | Liveness probe |
