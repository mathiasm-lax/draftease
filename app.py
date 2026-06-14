"""
Draftease — web app with professional authentication and a polished UI.

* Users in a database (Postgres in prod, SQLite locally), bcrypt-hashed passwords.
* Signed, HttpOnly, SameSite=Lax session cookies (Starlette SessionMiddleware).
* CSRF tokens on every state-changing form.
* Logged-out visitors see a marketing landing page; the redline tool is gated.

Run locally:
    pip install -r requirements.txt
    uvicorn app:app --reload
    open http://127.0.0.1:8000
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
    SessionMiddleware, secret_key=_secret_key(),
    https_only=HTTPS_ONLY, same_site="lax", max_age=60 * 60 * 12,
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
# styling
# --------------------------------------------------------------------------- #
CSS = """
:root{
 --bg:#fff;--bg-soft:#f5f7fb;--bg-softer:#fafbfe;--ink:#0e1626;--ink-2:#384156;--muted:#697089;
 --line:#e6e9f2;--line-2:#eef0f7;--brand:#4338ca;--brand-2:#6366f1;--brand-soft:#eef0ff;--brand-ink:#3730a3;
 --teal:#0d9488;--teal-soft:#e3f6f3;--green:#0f9d6e;--green-soft:#e6f7f0;--amber:#b97900;--amber-soft:#fdf3e1;
 --red:#d6455d;--shadow:0 1px 2px rgba(14,22,38,.04),0 10px 28px rgba(14,22,38,.07);
 --shadow-lg:0 18px 50px rgba(14,22,38,.14);--radius:16px;--radius-sm:11px;
}
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--ink);
 background:var(--bg);-webkit-font-smoothing:antialiased;line-height:1.5}
a{color:inherit;text-decoration:none}button{font-family:inherit;cursor:pointer;border:none;background:none}
.btn{display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:14px;padding:10px 17px;border-radius:11px;transition:.15s;white-space:nowrap}
.btn-primary{background:var(--brand);color:#fff}.btn-primary:hover{background:#3730a3}
.btn-ghost{color:var(--ink-2)}.btn-ghost:hover{color:var(--ink)}
.btn-outline{border:1px solid var(--line);color:var(--ink);background:#fff}.btn-outline:hover{border-color:#c9cee0;background:var(--bg-soft)}
.btn-lg{padding:14px 24px;font-size:15px;border-radius:12px}.btn-block{width:100%;justify-content:center}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:19px;letter-spacing:-.025em}
.logo .mark{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,var(--brand),#7c3aed);
 display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:16px;box-shadow:0 4px 12px rgba(67,56,202,.35)}
.mkt{max-width:1140px;margin:0 auto;padding:0 24px}
.nav{position:sticky;top:0;z-index:40;background:rgba(255,255,255,.82);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.nav-in{max-width:1140px;margin:0 auto;padding:14px 24px;display:flex;align-items:center;gap:32px}
.nav-links{display:flex;gap:26px;margin-left:8px}.nav-links a{color:var(--ink-2);font-size:14px;font-weight:500}.nav-links a:hover{color:var(--ink)}
.nav-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.hero{padding:84px 0 60px;text-align:center}
.pill{display:inline-flex;align-items:center;gap:9px;background:var(--brand-soft);color:var(--brand-ink);font-weight:600;font-size:13px;padding:7px 15px;border-radius:999px;margin-bottom:26px}
.pill .dot{width:7px;height:7px;border-radius:50%;background:var(--teal)}
h1.hero-h{font-size:58px;line-height:1.04;letter-spacing:-.04em;font-weight:800;margin:0 auto 22px;max-width:880px}
h1.hero-h .grad{background:linear-gradient(120deg,var(--brand),#7c3aed 55%,#0d9488);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.hero-sub{font-size:20px;color:var(--ink-2);max-width:650px;margin:0 auto 34px}
.hero-cta{display:flex;gap:12px;justify-content:center;margin-bottom:14px}.hero-note{font-size:13px;color:var(--muted)}
.section{padding:74px 0}.section.alt{background:var(--bg-soft);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.sec-head{text-align:center;max-width:680px;margin:0 auto 48px}
.sec-tag{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--brand-2);font-weight:700;margin-bottom:14px}
.sec-head h2{font-size:38px;letter-spacing:-.03em;margin:0 0 14px;line-height:1.1}.sec-head p{font-size:18px;color:var(--ink-2);margin:0}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
.step{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:28px}
.step .n{width:36px;height:36px;border-radius:10px;background:var(--brand-soft);color:var(--brand-ink);display:flex;align-items:center;justify-content:center;font-weight:800;margin-bottom:18px}
.step h3{margin:0 0 9px;font-size:18px;letter-spacing:-.01em}.step p{margin:0;color:var(--ink-2);font-size:14.5px}
.security{display:grid;grid-template-columns:1fr 1fr;gap:48px;align-items:center}
.security h2{font-size:34px;letter-spacing:-.03em;margin:0 0 16px;line-height:1.12}.security p.lead{font-size:17px;color:var(--ink-2);margin:0 0 24px}
.sec-list{display:flex;flex-direction:column;gap:14px}.sec-item{display:flex;gap:13px;align-items:flex-start}
.sec-item .chk{flex:none;width:22px;height:22px;border-radius:7px;background:var(--green-soft);color:var(--green);display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:800;margin-top:1px}
.sec-item b{font-size:14.5px}.sec-item span{color:var(--muted);font-size:13.5px;display:block}
.sec-card{background:var(--ink);color:#fff;border-radius:20px;padding:30px;box-shadow:var(--shadow-lg)}
.flow{display:flex;flex-direction:column;gap:11px}
.flow-node{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:14px 16px;display:flex;align-items:center;gap:12px;font-size:13.5px}
.flow-node .fic{width:32px;height:32px;border-radius:9px;background:rgba(255,255,255,.1);display:flex;align-items:center;justify-content:center;font-size:15px;flex:none}
.flow-node b{font-weight:600}.flow-node small{color:#aab3d4;display:block;font-size:12px}.flow-node.your{border-color:rgba(99,102,241,.5);background:rgba(99,102,241,.14)}
.flow-arrow{text-align:center;color:#5b6585;font-size:13px;height:6px;line-height:6px}
.pricing{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;align-items:stretch}
.price{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:30px;display:flex;flex-direction:column}
.price.pop{border:2px solid var(--brand);box-shadow:var(--shadow-lg);position:relative}
.price.pop .badge{position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:var(--brand);color:#fff;font-size:12px;font-weight:700;padding:5px 14px;border-radius:999px}
.price h3{margin:0 0 6px;font-size:18px}.price .desc{color:var(--muted);font-size:13.5px;margin-bottom:18px;min-height:38px}
.price .amt{font-size:42px;font-weight:800;letter-spacing:-.03em}.price .amt span{font-size:15px;font-weight:500;color:var(--muted)}
.price ul{list-style:none;padding:0;margin:20px 0 24px;display:flex;flex-direction:column;gap:11px}
.price li{display:flex;gap:10px;font-size:14px;color:var(--ink-2)}.price li::before{content:"✓";color:var(--green);font-weight:800}
.price .btn{margin-top:auto}
footer{background:var(--ink);color:#aab3d4;padding:50px 0 38px}
.foot-in{max-width:1140px;margin:0 auto;padding:0 24px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:30px}
.foot-in .logo{color:#fff}.foot-in p{max-width:280px;font-size:13.5px;margin-top:14px}
.disclaimer{max-width:780px;margin:14px auto 0;padding:0 24px;font-size:11.5px;color:#7b85a8;text-align:center;line-height:1.6}
.foot-bottom{max-width:1140px;margin:30px auto 0;padding:20px 24px 0;border-top:1px solid rgba(255,255,255,.08);font-size:12.5px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px}
/* auth */
.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg-soft);padding:40px 16px}
.auth{width:100%;max-width:410px;background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:34px;box-shadow:var(--shadow)}
.auth .logo{justify-content:flex-start;margin-bottom:20px}
.auth h1{font-size:22px;letter-spacing:-.02em;margin:0 0 4px}.auth .sub{color:var(--muted);font-size:14px;margin:0 0 22px}
label{display:block;font-weight:600;font-size:13px;margin:16px 0 6px}
input[type=email],input[type=password],input[type=text]{width:100%;font-size:14px;border:1px solid var(--line);border-radius:10px;padding:11px 12px;background:#fff}
input:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
.auth button[type=submit]{margin-top:22px;width:100%;background:var(--brand);color:#fff;font-weight:600;font-size:15px;padding:13px;border-radius:11px}
.auth button[type=submit]:hover{background:#3730a3}
.muted-link{font-size:13.5px;color:var(--muted);margin-top:18px;text-align:center}.muted-link a{color:var(--brand);font-weight:600}
.err{background:#fdecef;color:#b3243c;border:1px solid #f6c9d2;border-radius:10px;padding:11px 13px;font-size:13.5px;margin-bottom:4px}
/* app shell */
.app{display:grid;grid-template-columns:248px 1fr;min-height:100vh;background:var(--bg-soft)}
.side{background:#fff;border-right:1px solid var(--line);padding:18px 14px;display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
.side .logo{padding:6px 8px 18px}
.side-link{display:flex;align-items:center;gap:11px;padding:10px 12px;border-radius:10px;color:var(--ink-2);font-weight:500;font-size:14px;margin-bottom:2px}
.side-link.active{background:var(--brand-soft);color:var(--brand-ink);font-weight:600}.side-link:hover{background:var(--bg-soft)}
.side-foot{margin-top:auto;border-top:1px solid var(--line-2);padding-top:12px}
.side-acct{display:flex;align-items:center;gap:10px;padding:8px 10px}
.avatar{width:33px;height:33px;border-radius:10px;background:linear-gradient(135deg,var(--brand),#7c3aed);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;flex:none}
.side-acct .nm{font-size:13px;font-weight:600;line-height:1.2}.side-acct .nm small{display:block;color:var(--muted);font-weight:400;font-size:12px}
.logout-btn{width:100%;text-align:left;color:var(--ink-2);font-size:13px;padding:9px 12px;border-radius:9px;margin-top:6px;display:flex;gap:11px;align-items:center}
.logout-btn:hover{background:var(--bg-soft)}
.main{overflow-y:auto;height:100vh}
.topbar{position:sticky;top:0;z-index:10;background:rgba(245,247,251,.86);backdrop-filter:blur(8px);padding:18px 32px;border-bottom:1px solid var(--line)}
.topbar h1{font-size:21px;margin:0;letter-spacing:-.02em}.topbar .sub{color:var(--muted);font-size:13.5px;margin-top:2px}
.content{padding:28px 32px 60px;max-width:860px}
.card{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:28px;margin-bottom:22px}
.card h2{margin:0 0 6px;font-size:19px;letter-spacing:-.02em}.card .csub{color:var(--muted);font-size:14px;margin:0 0 22px}
.field-label{display:block;font-weight:600;font-size:13.5px;margin:18px 0 8px}
.field-label:first-of-type{margin-top:0}
input[type=file]{font-size:14px}
textarea{width:100%;min-height:230px;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;border:1px solid var(--line);border-radius:11px;padding:13px;background:var(--bg-softer)}
textarea:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
.hint{font-size:12.5px;color:var(--muted);margin-top:8px}.hint a{color:var(--brand);font-weight:600}
.gen-btn{margin-top:24px;background:var(--brand);color:#fff;font-weight:600;font-size:15px;padding:14px 24px;border-radius:12px}
.gen-btn:hover{background:#3730a3}
.note{display:flex;gap:10px;align-items:flex-start;background:var(--teal-soft);border-radius:12px;padding:13px 15px;font-size:13px;color:#0a6b60;margin:14px 0 4px}
code{background:var(--brand-soft);color:var(--brand-ink);padding:1px 6px;border-radius:5px;font-size:.92em}
@media(max-width:900px){.nav-links{display:none}h1.hero-h{font-size:40px}.steps,.pricing,.security{grid-template-columns:1fr}.app{grid-template-columns:1fr}.side{display:none}}
"""

FONT = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">')


def page(body: str, title: str = "Draftease") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title>{FONT}<style>{CSS}</style></head><body>{body}</body></html>')


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
def landing_page() -> str:
    return page("""
    <nav class="nav"><div class="nav-in">
      <div class="logo"><span class="mark">D</span> Draftease</div>
      <div class="nav-links"><a href="#how">How it works</a><a href="#security">Security</a><a href="#pricing">Pricing</a></div>
      <div class="nav-right">
        <a class="btn btn-ghost" href="/login">Log in</a>
        <a class="btn btn-primary" href="/signup">Start free</a>
      </div>
    </div></nav>
    <div class="mkt"><section class="hero">
      <div class="pill"><span class="dot"></span> Built for landlords &amp; their brokers · Your docs stay in your cloud</div>
      <h1 class="hero-h">Drop in a signed LOI. Get back a <span class="grad">lease redline</span>.</h1>
      <p class="hero-sub">Draftease applies the terms from a signed letter of intent to your property's own form lease and returns a clean, tracked-changes first draft — ready for your attorney.</p>
      <div class="hero-cta">
        <a class="btn btn-primary btn-lg" href="/signup">Start free trial</a>
        <a class="btn btn-outline btn-lg" href="/login">Log in</a>
      </div>
      <div class="hero-note">No credit card. Be drafting in minutes.</div>
    </section></div>

    <section class="section alt" id="how"><div class="mkt">
      <div class="sec-head"><div class="sec-tag">How it works</div><h2>From LOI to redline in three steps</h2>
      <p>Set up each property's form lease once. Every deal after that is a few clicks.</p></div>
      <div class="steps">
        <div class="step"><div class="n">1</div><h3>Upload the signed LOI</h3><p>Pick the property and drop in the executed letter of intent — Draftease reads the business terms.</p></div>
        <div class="step"><div class="n">2</div><h3>Confirm the terms</h3><p>Review the extracted rent, term, free rent, TI and options before a draft is generated. Nothing is auto-filed.</p></div>
        <div class="step"><div class="n">3</div><h3>Download the redline</h3><p>Get a native Word tracked-changes document showing exactly how your form lease was modified.</p></div>
      </div>
    </div></section>

    <section class="section" id="security"><div class="mkt"><div class="security">
      <div>
        <div class="sec-tag">Security &amp; privacy</div>
        <h2>Your leases stay in your cloud.</h2>
        <p class="lead">The form lease and the redline are processed by deterministic software — never sent to a third-party AI service.</p>
        <div class="sec-list">
          <div class="sec-item"><div class="chk">✓</div><div><b>No documents sent to external LLMs</b><span>Redline generation is pure software — no AI touches your lease.</span></div></div>
          <div class="sec-item"><div class="chk">✓</div><div><b>Encrypted &amp; access-controlled</b><span>Encryption at rest and in transit, per-tenant isolation.</span></div></div>
          <div class="sec-item"><div class="chk">✓</div><div><b>We never train on your data</b><span>Your documents are yours. Deleted on request, exported on demand.</span></div></div>
        </div>
      </div>
      <div class="sec-card"><div class="flow">
        <div class="flow-node your"><div class="fic">📄</div><div><b>Signed LOI</b><small>Uploaded by your team</small></div></div>
        <div class="flow-arrow">↓</div>
        <div class="flow-node your"><div class="fic">⚙️</div><div><b>Merge &amp; redline engine</b><small>Deterministic — no AI, your server</small></div></div>
        <div class="flow-arrow">↓</div>
        <div class="flow-node your"><div class="fic">✅</div><div><b>Tracked-changes lease</b><small>Stays in your storage</small></div></div>
      </div></div>
    </div></div></section>

    <section class="section alt" id="pricing"><div class="mkt">
      <div class="sec-head"><div class="sec-tag">Pricing</div><h2>Simple monthly plans</h2><p>Save the first-draft hours. Start free, upgrade when you're ready.</p></div>
      <div class="pricing">
        <div class="price"><h3>Starter</h3><div class="desc">For a single owner or small brokerage.</div><div class="amt">$49<span>/mo</span></div>
          <ul><li>1 property template</li><li>Up to 10 redlines / mo</li><li>2 seats</li></ul>
          <a class="btn btn-outline btn-block" href="/signup">Start free</a></div>
        <div class="price pop"><div class="badge">Most popular</div><h3>Professional</h3><div class="desc">For active owners managing multiple buildings.</div><div class="amt">$199<span>/mo</span></div>
          <ul><li>Up to 15 property templates</li><li>Unlimited redlines</li><li>10 seats + outside-broker access</li><li>Audit log &amp; exports</li></ul>
          <a class="btn btn-primary btn-block" href="/signup">Start free trial</a></div>
        <div class="price"><h3>Enterprise</h3><div class="desc">For portfolios with security needs.</div><div class="amt">Custom</div>
          <ul><li>Unlimited templates &amp; seats</li><li>Private cloud / dedicated isolation</li><li>SSO &amp; SOC 2 reporting</li></ul>
          <a class="btn btn-outline btn-block" href="/signup">Contact sales</a></div>
      </div>
    </div></section>

    <footer>
      <div class="foot-in">
        <div><div class="logo"><span class="mark">D</span> Draftease</div><p>Lease redlines from your LOIs — securely, in your own cloud.</p></div>
      </div>
      <div class="disclaimer">Draftease is a document-drafting tool, not a law firm, and does not provide legal advice. Generated redlines are first drafts intended for review and finalization by a licensed attorney.</div>
      <div class="foot-bottom"><span>© 2026 Draftease, Inc.</span><span>Encrypted at rest · No AI on your documents</span></div>
    </footer>
    """, "Draftease — Lease redlines from your LOIs")


def login_page(request: Request, error: str = "") -> str:
    err = f'<div class="err">{error}</div>' if error else ""
    signup_link = ('<p class="muted-link">No account? <a href="/signup">Create one</a></p>' if ALLOW_SIGNUP else "")
    return page(f"""<div class="auth-wrap"><div class="auth">
      <a class="logo" href="/"><span class="mark">D</span> Draftease</a>
      <h1>Sign in</h1><p class="sub">Welcome back.</p>{err}
      <form method="post" action="/login">
        <input type="hidden" name="csrf" value="{csrf_token(request)}">
        <label>Email</label><input type="email" name="email" required autofocus>
        <label>Password</label><input type="password" name="password" required>
        <button type="submit">Sign in</button>
      </form>{signup_link}
    </div></div>""", "Sign in · Draftease")


def signup_page(request: Request, error: str = "") -> str:
    err = f'<div class="err">{error}</div>' if error else ""
    return page(f"""<div class="auth-wrap"><div class="auth">
      <a class="logo" href="/"><span class="mark">D</span> Draftease</a>
      <h1>Create your account</h1><p class="sub">Start drafting in minutes.</p>{err}
      <form method="post" action="/signup">
        <input type="hidden" name="csrf" value="{csrf_token(request)}">
        <label>Name</label><input type="text" name="name">
        <label>Work email</label><input type="email" name="email" required autofocus>
        <label>Password</label><input type="password" name="password" required>
        <div class="hint">At least 8 characters.</div>
        <button type="submit">Create account</button>
      </form>
      <p class="muted-link">Already have an account? <a href="/login">Sign in</a></p>
    </div></div>""", "Create account · Draftease")


def tool_page(request: Request, user) -> str:
    sample = json.dumps(SAMPLE_TERMS, indent=2)
    name = user.name or user.email
    initials = "".join(p[0] for p in (user.name or user.email).replace("@", " ").split()[:2]).upper() or "U"
    csrf = csrf_token(request)
    return page(f"""<div class="app">
      <aside class="side">
        <div class="logo"><span class="mark">D</span> Draftease</div>
        <div class="side-link active">✍&nbsp; New redline</div>
        <div class="side-foot">
          <div class="side-acct"><div class="avatar">{initials}</div>
            <div class="nm">{name}<small>Signed in</small></div></div>
          <form method="post" action="/logout" style="margin:0">
            <input type="hidden" name="csrf" value="{csrf}">
            <button class="logout-btn" type="submit">←&nbsp; Log out</button>
          </form>
        </div>
      </aside>
      <div class="main">
        <div class="topbar"><h1>New redline</h1><div class="sub">Turn a signed LOI into a tracked-changes draft.</div></div>
        <div class="content">
          <div class="card">
            <h2>Generate a redline</h2>
            <p class="csub">Upload a form lease with <code>{{{{tokens}}}}</code>, confirm the deal terms, and download a Word tracked-changes redline.</p>
            <form action="/redline" method="post" enctype="multipart/form-data">
              <input type="hidden" name="csrf" value="{csrf}">
              <label class="field-label">1 · Form lease (.docx with {{{{tokens}}}})</label>
              <input type="file" name="lease" accept=".docx" required>
              <div class="hint">Need test files? Download a <a href="/sample-lease">sample form lease (.docx)</a> and a <a href="/sample-loi">sample signed LOI (PDF)</a>, then upload the lease above.</div>
              <label class="field-label">2 · Deal terms (JSON: token → value)</label>
              <div class="hint" style="margin:-2px 0 8px">These match the sample LOI. (Automatic reading of the LOI PDF is the next feature.)</div>
              <textarea name="terms" spellcheck="false">{sample}</textarea>
              <div class="note">🔒 <div>The redline is generated by deterministic software — your lease is never sent to an external AI.</div></div>
              <button class="gen-btn" type="submit">Generate redline →</button>
            </form>
          </div>
        </div>
      </div>
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
# sample test files
# --------------------------------------------------------------------------- #
def _build_sample_loi_pdf() -> bytes:
    from fpdf import FPDF

    t = SAMPLE_TERMS
    pdf = FPDF(format="Letter")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(20, 18, 20)
    pdf.add_page()
    W = pdf.epw

    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(W, 9, "LETTER OF INTENT", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(W, 6, "(Non-binding summary of principal terms)", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    for label, value in (("Date", t["lease_date"]),
                         ("Property", f"{t['property_name']}, Suite {t['suite']}"),
                         ("Landlord", t["landlord"]),
                         ("Tenant", t["tenant"])):
        pdf.set_font("Helvetica", "B", 10)
        pdf.write(6, f"{label}: ")
        pdf.set_font("Helvetica", "", 10)
        pdf.write(6, value)
        pdf.ln(7)

    pdf.ln(1)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(W, 6, "The parties propose to enter into a lease on the following principal terms:")
    pdf.ln(2)

    terms = [
        ("Premises", f"Approximately {t['rentable_sf']} rentable square feet on the {t['floor']} floor"),
        ("Lease Term", f"{t['term_months']} months"),
        ("Commencement Date", t["commencement_date"]),
        ("Base Rent", f"{t['base_rent_psf']} per RSF per annum ({t['monthly_rent']} per month)"),
        ("Annual Escalation", t["annual_escalation"]),
        ("Free Rent", f"{t['free_rent_months']} months of abated Base Rent"),
        ("Security Deposit", t["security_deposit"]),
        ("TI Allowance", f"{t['ti_allowance_psf']} per RSF"),
        ("Renewal Option", t["renewal_option"]),
        ("Permitted Use", t["permitted_use"]),
    ]
    for i, (k, v) in enumerate(terms, 1):
        pdf.set_font("Helvetica", "B", 10)
        pdf.write(6, f"{i}. {k}: ")
        pdf.set_font("Helvetica", "", 10)
        pdf.write(6, v)
        pdf.ln(7)

    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(W, 5, "This Letter of Intent is non-binding and is intended solely to outline the "
                         "principal business terms for a definitive lease to be prepared and reviewed "
                         "by the parties' counsel.")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(W, 6, "Agreed and accepted:", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    pdf.cell(W, 6, f"LANDLORD: {t['landlord']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(W, 6, "By: ____________________    Title: ____________", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    pdf.cell(W, 6, f"TENANT: {t['tenant']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(W, 6, "By: ____________________    Title: ____________", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    return HTMLResponse(tool_page(request, user) if user else landing_page())


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(login_page(request))


@app.post("/login")
def login_post(request: Request, email: str = Form(...), password: str = Form(...), csrf: str = Form(...)):
    if not check_csrf(request, csrf):
        return HTMLResponse(login_page(request, "Session expired — please try again."), 400)
    user = auth.authenticate(email, password)
    if not user:
        return HTMLResponse(login_page(request, "Invalid email or password."), 401)
    request.session["uid"] = user.id
    request.session.pop("csrf", None)
    return RedirectResponse("/", status_code=303)


@app.get("/signup", response_class=HTMLResponse)
def signup_get(request: Request):
    if not ALLOW_SIGNUP:
        return RedirectResponse("/login", status_code=303)
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(signup_page(request))


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
    return RedirectResponse("/", status_code=303)


@app.get("/sample-lease")
def sample_lease(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    import make_sample_lease
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sample_form_lease.docx")
        make_sample_lease.build(p)
        data = open(p, "rb").read()
    return StreamingResponse(io.BytesIO(data), media_type=DOCX_MIME,
        headers={"Content-Disposition": 'attachment; filename="sample_form_lease.docx"'})


@app.get("/sample-loi")
def sample_loi(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    return StreamingResponse(io.BytesIO(_build_sample_loi_pdf()), media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="sample_signed_LOI.pdf"'})


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
