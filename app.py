"""
Draftease — web app with professional authentication.

Auth design
-----------
* Users in a database (SQLAlchemy / SQLite by default), bcrypt-hashed passwords.
* Signed, HttpOnly, SameSite=Lax session cookies (Starlette SessionMiddleware).
* CSRF tokens on every state-changing form.
* The redline tool ( / and /redline ) is gated behind login; /health stays open.

Run locally:
    pip install -r requirements.txt
    uvicorn app:app --reload
    open http://127.0.0.1:8000      # you'll be sent to /login
"""
import io
import json
import os
import secrets
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

import auth
from redline_engine import generate_redline

# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_BYTES = 15 * 1024 * 1024
ALLOW_SIGNUP = os.environ.get("DRAFTEASE_ALLOW_SIGNUP", "true").lower() == "true"
HTTPS_ONLY = os.environ.get("DRAFTEASE_HTTPS_ONLY", "false").lower() == "true"


def _secret_key() -> str:
    """Stable secret so sessions survive restarts. Set DRAFTEASE_SECRET_KEY in prod."""
    key = os.environ.get("DRAFTEASE_SECRET_KEY")
    if key:
        return key
    path = ".draftease_secret"
    if os.path.exists(path):
        return open(path).read().strip()
    key = secrets.token_urlsafe(48)
    with open(path, "w") as fh:
        fh.write(key)
    return key


app = FastAPI(title="Draftease")
app.add_middleware(
    SessionMiddleware,
    secret_key=_secret_key(),
    https_only=HTTPS_ONLY,
    same_site="lax",
    max_age=60 * 60 * 12,  # 12h
)


@app.on_event("startup")
def _startup():
    auth.init_db()


# --------------------------------------------------------------------------- #
# session / csrf helpers
# --------------------------------------------------------------------------- #
def current_user(request: Request):
    uid = request.session.get("uid")
    return auth.get_user_by_id(uid) if uid else None


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


def check_csrf(request: Request, submitted: str) -> bool:
    expected = request.session.get("csrf", "")
    return bool(submitted) and secrets.compare_digest(submitted, expected)


# --------------------------------------------------------------------------- #
# shared styling
# --------------------------------------------------------------------------- #
CSS = """
*{box-sizing:border-box} body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f5f7fb;
 color:#0e1626;margin:0;padding:40px 16px}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:20px;letter-spacing:-.02em}
.mark{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,#4338ca,#7c3aed);color:#fff;
 display:flex;align-items:center;justify-content:center;font-weight:800}
.card{max-width:680px;margin:0 auto;background:#fff;border:1px solid #e6e9f2;border-radius:16px;padding:30px;
 box-shadow:0 10px 28px rgba(14,22,38,.07)}
.auth{max-width:400px}
h1{font-size:21px;letter-spacing:-.02em;margin:18px 0 4px}
p.sub{color:#697089;margin:0 0 22px;font-size:14px}
label{display:block;font-weight:600;font-size:13px;margin:16px 0 6px}
input[type=email],input[type=password],input[type=text]{width:100%;font-size:14px;border:1px solid #e6e9f2;
 border-radius:10px;padding:11px 12px}
input:focus{outline:none;border-color:#4338ca;box-shadow:0 0 0 3px #eef0ff}
textarea{width:100%;min-height:220px;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;border:1px solid #e6e9f2;
 border-radius:10px;padding:12px}
button{margin-top:20px;width:100%;background:#4338ca;color:#fff;border:0;font-weight:600;font-size:15px;padding:13px 22px;
 border-radius:11px;cursor:pointer}
button:hover{background:#3730a3}
.muted{font-size:13px;color:#697089;margin-top:18px;text-align:center}
a{color:#4338ca;font-weight:600;text-decoration:none}
.err{background:#fdecef;color:#b3243c;border:1px solid #f6c9d2;border-radius:10px;padding:11px 13px;font-size:13.5px;
 margin-bottom:6px}
.topbar{max-width:680px;margin:0 auto 16px;display:flex;align-items:center}
.who{margin-left:auto;display:flex;align-items:center;gap:14px;font-size:13.5px;color:#697089}
.logout{background:none;color:#4338ca;border:1px solid #e6e9f2;width:auto;margin:0;padding:8px 14px;font-size:13px;border-radius:9px}
.logout:hover{background:#eef0ff}
code{background:#eef0ff;color:#3730a3;padding:1px 6px;border-radius:5px}
.hint{font-size:12.5px;color:#697089;margin-top:8px}
"""


def page(body: str, title: str = "Draftease") -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title><style>{CSS}</style></head><body>{body}</body></html>')


# --------------------------------------------------------------------------- #
# auth pages
# --------------------------------------------------------------------------- #
def login_page(request: Request, error: str = "") -> str:
    err = f'<div class="err">{error}</div>' if error else ""
    signup_link = ('<p class="muted">No account? <a href="/signup">Create one</a></p>'
                   if ALLOW_SIGNUP else "")
    return page(f"""
      <div class="card auth">
        <div class="brand"><span class="mark">D</span> Draftease</div>
        <h1>Sign in</h1><p class="sub">Welcome back.</p>
        {err}
        <form method="post" action="/login">
          <input type="hidden" name="csrf" value="{csrf_token(request)}">
          <label>Email</label><input type="email" name="email" required autofocus>
          <label>Password</label><input type="password" name="password" required>
          <button type="submit">Sign in</button>
        </form>
        {signup_link}
      </div>""", "Sign in · Draftease")


def signup_page(request: Request, error: str = "") -> str:
    err = f'<div class="err">{error}</div>' if error else ""
    return page(f"""
      <div class="card auth">
        <div class="brand"><span class="mark">D</span> Draftease</div>
        <h1>Create your account</h1><p class="sub">Start drafting in minutes.</p>
        {err}
        <form method="post" action="/signup">
          <input type="hidden" name="csrf" value="{csrf_token(request)}">
          <label>Name</label><input type="text" name="name">
          <label>Work email</label><input type="email" name="email" required autofocus>
          <label>Password</label><input type="password" name="password" required>
          <div class="hint">At least 8 characters.</div>
          <button type="submit">Create account</button>
        </form>
        <p class="muted">Already have an account? <a href="/login">Sign in</a></p>
      </div>""", "Create account · Draftease")


def tool_page(request: Request, user) -> str:
    sample = json.dumps(SAMPLE_TERMS, indent=2)
    name = user.name or user.email
    return page(f"""
      <div class="topbar">
        <div class="brand"><span class="mark">D</span> Draftease</div>
        <div class="who">Signed in as <b>{name}</b>
          <form method="post" action="/logout" style="margin:0">
            <input type="hidden" name="csrf" value="{csrf_token(request)}">
            <button class="logout" type="submit">Log out</button>
          </form>
        </div>
      </div>
      <div class="card">
        <h1>Generate a redline</h1>
        <p class="sub">Upload a form lease with <code>{{{{tokens}}}}</code>, paste the deal terms,
          and download a Word tracked-changes redline. No AI, no external calls.</p>
        <form action="/redline" method="post" enctype="multipart/form-data">
          <input type="hidden" name="csrf" value="{csrf_token(request)}">
          <label>1 · Form lease (.docx with {{{{tokens}}}})</label>
          <input type="file" name="lease" accept=".docx" required>
          <div class="hint">No file? Run <code>python make_sample_lease.py</code> for a sample.</div>
          <label>2 · Deal terms (JSON: token → value)</label>
          <textarea name="terms" spellcheck="false">{sample}</textarea>
          <button type="submit">Generate redline →</button>
        </form>
      </div>""", "Draftease")


SAMPLE_TERMS = {
    "property_name": "350 Park Avenue", "suite": "1200", "lease_date": "June 11, 2026",
    "landlord": "Meridian RE Holdings, LLC", "tenant": "Lockton Advisors, Inc.",
    "rentable_sf": "18,400", "floor": "12th", "term_months": "87",
    "commencement_date": "September 1, 2026", "expiration_date": "November 30, 2033",
    "base_rent_psf": "$72.50", "monthly_rent": "$111,166.67",
    "annual_escalation": "three percent (3%)", "free_rent_months": "four (4)",
    "security_deposit": "$220,000.00", "ti_allowance_psf": "$95.00",
    "renewal_option": "one (1) option to renew for five (5) years at fair market value",
    "permitted_use": "general, administrative and executive offices",
}


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return tool_page(request, user)


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return login_page(request)


@app.post("/login")
def login_post(request: Request, email: str = Form(...), password: str = Form(...),
               csrf: str = Form(...)):
    if not check_csrf(request, csrf):
        return HTMLResponse(login_page(request, "Session expired — please try again."), 400)
    user = auth.authenticate(email, password)
    if not user:
        return HTMLResponse(login_page(request, "Invalid email or password."), 401)
    request.session["uid"] = user.id
    request.session.pop("csrf", None)  # rotate CSRF after auth state change
    return RedirectResponse("/", status_code=303)


@app.get("/signup", response_class=HTMLResponse)
def signup_get(request: Request):
    if not ALLOW_SIGNUP:
        return RedirectResponse("/login", status_code=303)
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return signup_page(request)


@app.post("/signup")
def signup_post(request: Request, email: str = Form(...), password: str = Form(...),
                name: str = Form(""), csrf: str = Form(...)):
    if not ALLOW_SIGNUP:
        raise HTTPException(403, "Signups are disabled.")
    if not check_csrf(request, csrf):
        return HTMLResponse(signup_page(request, "Session expired — please try again."), 400)
    try:
        user = auth.create_user(email, password, name)
    except auth.AuthError as exc:
        return HTMLResponse(signup_page(request, str(exc)), 400)
    request.session["uid"] = user.id
    request.session.pop("csrf", None)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    if check_csrf(request, csrf):
        request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.post("/redline")
async def redline(request: Request, lease: UploadFile = File(...),
                  terms: str = Form(...), csrf: str = Form(...)):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired — reload the page and try again.")
    if not lease.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Please upload a .docx form lease.")
    try:
        terms_dict = json.loads(terms)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Deal terms are not valid JSON: {exc}")
    if not isinstance(terms_dict, dict):
        raise HTTPException(400, "Deal terms must be a JSON object of token -> value.")

    raw = await lease.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "File too large (15 MB max).")

    with tempfile.TemporaryDirectory() as d:
        in_path = os.path.join(d, "lease.docx")
        out_path = os.path.join(d, "redline.docx")
        with open(in_path, "wb") as fh:
            fh.write(raw)
        try:
            report = generate_redline(in_path, terms_dict, out_path, author="Draftease")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Could not process the lease: {exc}")
        data = open(out_path, "rb").read()

    out_name = (os.path.splitext(lease.filename)[0] or "lease") + "_redline.docx"
    headers = {
        "Content-Disposition": f'attachment; filename="{out_name}"',
        "X-Draftease-Applied": str(len(report["applied"])),
        "X-Draftease-Unmatched": str(len(report["unmatched"])),
    }
    return StreamingResponse(io.BytesIO(data), media_type=DOCX_MIME, headers=headers)
