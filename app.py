"""
Draftease — web app: full marketing site + app shell (ported from the mockup),
with professional auth (Postgres + bcrypt + CSRF) and a wizard wired to the real
deterministic redline engine.
"""
import io
import json
import os
import re
import secrets
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from starlette.middleware.sessions import SessionMiddleware

import ai_extract
import auth
import tagger
from redline_engine import extract_tokens, generate_redline, generate_redline_direct

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_BYTES = 15 * 1024 * 1024
ALLOW_SIGNUP = os.environ.get("DRAFTEASE_ALLOW_SIGNUP", "true").lower() == "true"
HTTPS_ONLY = os.environ.get("DRAFTEASE_HTTPS_ONLY", "false").lower() == "true"

# --- Stripe (billing) ---
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
BILLING_ENABLED = bool(STRIPE_SECRET_KEY)
if BILLING_ENABLED:
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

# plan -> Stripe Checkout config (amounts in cents). Prices defined inline so you
# only need a secret key — no need to pre-create Products in the Stripe dashboard.
PLANS = {
    "single": {"label": "Single lease", "amount": 1000, "mode": "payment",
               "credits": 1, "name": "Draftease — single lease"},
    "payg": {"label": "Pay as you go", "amount": 5000, "mode": "payment",
             "credits": 1, "name": "Draftease — one contract redline"},
    "unlimited": {"label": "Unlimited (monthly)", "amount": 19900, "mode": "subscription",
                  "name": "Draftease Unlimited"},
}


def _secret_key() -> str:
    key = os.environ.get("DRAFTEASE_SECRET_KEY")
    if key:
        return key
    # Prefer a DB-persisted secret so redeploys don't log everyone out.
    try:
        return auth.get_or_create_app_secret()
    except Exception:  # noqa: BLE001 — fall back to a local file (dev) if DB isn't ready
        path = ".draftease_secret"
        if os.path.exists(path):
            return open(path).read().strip()
        key = secrets.token_urlsafe(48)
        with open(path, "w") as fh:
            fh.write(key)
        return key


app = FastAPI(title="Draftease")
app.add_middleware(SessionMiddleware, secret_key=_secret_key(),
                   https_only=HTTPS_ONLY, same_site="lax", max_age=60 * 60 * 12)


@app.on_event("startup")
def _startup():
    auth.init_db()


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
# styling (ported verbatim from the mockup, plus auth styles)
# --------------------------------------------------------------------------- #
CSS = """
:root{--bg:#fff;--bg-soft:#f5f7fb;--bg-softer:#fafbfe;--ink:#0e1626;--ink-2:#384156;--muted:#697089;--line:#e6e9f2;--line-2:#eef0f7;--brand:#4338ca;--brand-2:#6366f1;--brand-soft:#eef0ff;--brand-ink:#3730a3;--teal:#0d9488;--teal-soft:#e3f6f3;--green:#0f9d6e;--green-soft:#e6f7f0;--amber:#b97900;--amber-soft:#fdf3e1;--red:#d6455d;--shadow:0 1px 2px rgba(14,22,38,.04),0 10px 28px rgba(14,22,38,.07);--shadow-lg:0 18px 50px rgba(14,22,38,.14);--radius:16px;--radius-sm:11px;}
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased;line-height:1.5}
a{color:inherit;text-decoration:none}button{font-family:inherit;cursor:pointer;border:none;background:none}
.hidden{display:none!important}
.btn{display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:14px;padding:10px 17px;border-radius:11px;transition:.15s;white-space:nowrap}
.btn-primary{background:var(--brand);color:#fff}.btn-primary:hover{background:#3730a3}
.btn-ghost{color:var(--ink-2)}.btn-ghost:hover{color:var(--ink)}
.btn-outline{border:1px solid var(--line);color:var(--ink);background:#fff}.btn-outline:hover{border-color:#c9cee0;background:var(--bg-soft)}
.btn-lg{padding:14px 24px;font-size:15px;border-radius:12px}.btn-sm{padding:7px 13px;font-size:13px;border-radius:9px}.btn-block{width:100%;justify-content:center}
.btn:disabled{opacity:.5;cursor:default;pointer-events:none}
.startwrap{min-height:100vh;background:var(--bg-soft)}.startwrap .inner{max-width:860px;margin:0 auto;padding:26px 20px 70px}
.gin{width:100%;font-size:14px;border:1px solid var(--line);border-radius:10px;padding:11px 12px;margin-top:2px}.gin:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
.planpick{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:8px 0 18px}
.planopt{border:1.5px solid var(--line);border-radius:12px;padding:14px;cursor:pointer;font-size:14px;font-weight:600}
.planopt.sel{border-color:var(--brand);background:var(--brand-soft)}
.planopt .pa{font-size:22px;font-weight:800;margin-top:6px}.planopt .pa span{font-size:13px;color:var(--muted);font-weight:500}
.mkt{max-width:1140px;margin:0 auto;padding:0 24px}
.nav{position:sticky;top:0;z-index:40;background:rgba(255,255,255,.82);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.nav-in{max-width:1140px;margin:0 auto;padding:14px 24px;display:flex;align-items:center;gap:32px}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;font-size:19px;letter-spacing:-.025em}
.logo .mark{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,var(--brand),#7c3aed);display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:16px;box-shadow:0 4px 12px rgba(67,56,202,.35)}
.nav-links{display:flex;gap:26px;margin-left:8px}.nav-links a{color:var(--ink-2);font-size:14px;font-weight:500}.nav-links a:hover{color:var(--ink)}
.nav-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.hero{padding:88px 0 64px;text-align:center}
.pill{display:inline-flex;align-items:center;gap:9px;background:var(--brand-soft);color:var(--brand-ink);font-weight:600;font-size:13px;padding:7px 15px;border-radius:999px;margin-bottom:26px}
.pill .dot{width:7px;height:7px;border-radius:50%;background:var(--teal)}
h1.hero-h{font-size:60px;line-height:1.04;letter-spacing:-.04em;font-weight:800;margin:0 auto 22px;max-width:900px}
h1.hero-h .grad{background:linear-gradient(120deg,var(--brand),#7c3aed 55%,#0d9488);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.hero-sub{font-size:20px;color:var(--ink-2);max-width:660px;margin:0 auto 34px}
.hero-cta{display:flex;gap:12px;justify-content:center;margin-bottom:16px}
.hero-note{font-size:13px;color:var(--muted)}.hero-note b{color:var(--ink-2);font-weight:600}
.hero-shot{margin-top:58px;border-radius:20px;border:1px solid var(--line);background:var(--bg-soft);box-shadow:var(--shadow-lg);overflow:hidden}
.hero-shot .bar{display:flex;align-items:center;gap:7px;padding:13px 16px;border-bottom:1px solid var(--line);background:#fff}
.hero-shot .bar i{width:11px;height:11px;border-radius:50%;background:#e2e5ee;display:inline-block}
.hero-shot .bar .u{margin-left:14px;font-size:12px;color:var(--muted);font-weight:500}
.hero-shot .body{padding:26px;display:grid;grid-template-columns:1.05fr 1fr;gap:22px;text-align:left}
.shot-doc{background:#fff;border:1px solid var(--line);border-radius:13px;padding:24px;font-size:13px;line-height:1.95}
.shot-doc h5{margin:0 0 14px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.shot-doc .del{background:#ffe3e9;color:#b3243c;text-decoration:line-through;padding:0 3px;border-radius:3px}
.shot-doc .ins{background:#dff6ea;color:#0a7a52;padding:0 3px;border-radius:3px;font-weight:600}
.shot-terms{background:#fff;border:1px solid var(--line);border-radius:13px;padding:7px}
.shot-terms .th{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:700;padding:11px 14px 4px}
.shot-terms .tr{display:flex;justify-content:space-between;align-items:center;padding:11px 14px;border-radius:9px;font-size:13.5px}
.shot-terms .tr:nth-child(even){background:var(--bg-softer)}
.shot-terms .tr .k{color:var(--muted)}.shot-terms .tr .v{font-weight:600}
.trust{margin-top:60px;display:flex;flex-direction:column;align-items:center;gap:18px}
.trust-label{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);font-weight:600}
.trust-row{display:flex;gap:40px;flex-wrap:wrap;justify-content:center;opacity:.55}
.trust-row span{font-weight:700;font-size:17px;color:var(--ink-2);letter-spacing:-.01em}
.section{padding:80px 0}.section.alt{background:var(--bg-soft);border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.sec-head{text-align:center;max-width:680px;margin:0 auto 52px}
.sec-tag{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--brand-2);font-weight:700;margin-bottom:14px}
.sec-head h2{font-size:40px;letter-spacing:-.03em;margin:0 0 14px;line-height:1.1}.sec-head p{font-size:18px;color:var(--ink-2);margin:0}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
.step{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:28px;position:relative}
.step .n{width:36px;height:36px;border-radius:10px;background:var(--brand-soft);color:var(--brand-ink);display:flex;align-items:center;justify-content:center;font-weight:800;margin-bottom:18px}
.step h3{margin:0 0 9px;font-size:18px;letter-spacing:-.01em}.step p{margin:0;color:var(--ink-2);font-size:14.5px}
.diff{display:grid;grid-template-columns:repeat(3,1fr);gap:22px}
.dcard{padding:26px;border-radius:var(--radius);border:1px solid var(--line);background:#fff}
.dcard .ic{width:42px;height:42px;border-radius:11px;display:flex;align-items:center;justify-content:center;font-size:20px;margin-bottom:16px;background:var(--teal-soft)}
.dcard h3{margin:0 0 8px;font-size:16.5px}.dcard p{margin:0;color:var(--ink-2);font-size:14px}
.security{display:grid;grid-template-columns:1fr 1fr;gap:48px;align-items:center}
.security h2{font-size:36px;letter-spacing:-.03em;margin:0 0 16px;line-height:1.12}.security p.lead{font-size:17px;color:var(--ink-2);margin:0 0 24px}
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
.cta-band{text-align:center;padding:86px 24px;background:linear-gradient(135deg,#4338ca,#7c3aed 70%,#0d9488);color:#fff}
.cta-band h2{font-size:42px;letter-spacing:-.03em;margin:0 0 14px}.cta-band p{font-size:19px;opacity:.92;margin:0 0 28px}
.cta-band .btn-primary{background:#fff;color:var(--brand)}.cta-band .btn-primary:hover{background:#f1f1ff}
footer{background:var(--ink);color:#aab3d4;padding:54px 0 40px}
.foot-in{max-width:1140px;margin:0 auto;padding:0 24px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:30px}
.foot-in .logo{color:#fff}.foot-in p{max-width:280px;font-size:13.5px;margin-top:14px}
.foot-cols{display:flex;gap:60px;flex-wrap:wrap}
.foot-col h5{color:#fff;font-size:13px;margin:0 0 14px;letter-spacing:.04em}
.foot-col a{display:block;font-size:13.5px;margin-bottom:9px;color:#aab3d4}.foot-col a:hover{color:#fff}
.disclaimer{max-width:780px;margin:14px auto 0;padding:0 24px;font-size:11.5px;color:#7b85a8;text-align:center;line-height:1.6}
.foot-bottom{max-width:1140px;margin:32px auto 0;padding:22px 24px 0;border-top:1px solid rgba(255,255,255,.08);font-size:12.5px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px}
.app{display:grid;min-height:100vh;grid-template-columns:250px 1fr;background:var(--bg-soft)}
.side{background:#fff;border-right:1px solid var(--line);padding:18px 14px;display:flex;flex-direction:column;position:sticky;top:0;height:100vh}
.side .logo{padding:6px 8px 18px}.new-btn{margin:0 4px 16px}
.nav-group{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:8px 12px 6px;font-weight:600}
.side-link{display:flex;align-items:center;gap:11px;padding:10px 12px;border-radius:10px;color:var(--ink-2);font-weight:500;font-size:14px;cursor:pointer;margin-bottom:2px}
.side-link:hover{background:var(--bg-soft)}.side-link.active{background:var(--brand-soft);color:var(--brand-ink);font-weight:600}.side-link .si{width:18px;text-align:center}
.side-foot{margin-top:auto;border-top:1px solid var(--line-2);padding-top:12px}
.side-acct{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:10px}
.avatar{width:33px;height:33px;border-radius:10px;background:linear-gradient(135deg,var(--brand),#7c3aed);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;flex:none}
.side-acct .nm{font-size:13px;font-weight:600;line-height:1.2}.side-acct .nm small{display:block;color:var(--muted);font-weight:400;font-size:12px}
.main{padding:0;overflow-y:auto;height:100vh}
.topbar{position:sticky;top:0;z-index:10;background:rgba(245,247,251,.86);backdrop-filter:blur(8px);padding:18px 32px;display:flex;align-items:center;gap:16px;border-bottom:1px solid var(--line)}
.topbar h1{font-size:21px;margin:0;letter-spacing:-.02em}.topbar .sub{color:var(--muted);font-size:13.5px;margin-top:2px}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.search{display:flex;align-items:center;gap:8px;background:#fff;border:1px solid var(--line);border-radius:10px;padding:8px 12px;color:var(--muted);font-size:13px;width:220px}
.search input{border:none;outline:none;font-family:inherit;font-size:13px;width:100%;background:none}
.content{padding:28px 32px 60px;max-width:1080px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:26px}
.stat{background:#fff;border:1px solid var(--line);border-radius:var(--radius-sm);padding:18px}
.stat .lbl{font-size:12.5px;color:var(--muted);font-weight:500}.stat .val{font-size:30px;font-weight:800;letter-spacing:-.02em;margin-top:8px}
.stat .delta{font-size:12px;font-weight:600;margin-top:4px}.delta.up{color:var(--green)}.delta.flat{color:var(--muted)}
.panel{background:#fff;border:1px solid var(--line);border-radius:var(--radius);margin-bottom:24px}
.panel-head{padding:18px 22px;border-bottom:1px solid var(--line-2);display:flex;align-items:center;gap:12px}
.panel-head h3{margin:0;font-size:16px;letter-spacing:-.01em}.panel-head .btn{margin-left:auto}.panel-body{padding:6px 6px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;padding:12px 16px}
td{padding:13px 16px;font-size:14px;border-top:1px solid var(--line-2)}
tr.row{cursor:pointer}tr.row:hover td{background:var(--bg-softer)}
.tag{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:4px 10px;border-radius:999px}
.tag .d{width:6px;height:6px;border-radius:50%}
.tag.done{background:var(--green-soft);color:var(--green)}.tag.done .d{background:var(--green)}
.tag.review{background:var(--amber-soft);color:var(--amber)}.tag.review .d{background:var(--amber)}
.tag.draft{background:var(--bg-soft);color:var(--muted)}.tag.draft .d{background:var(--muted)}
.muted{color:var(--muted)}
.tgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
.tcard{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:20px;cursor:pointer;transition:.15s}
.tcard:hover{border-color:#c9cee0;box-shadow:var(--shadow)}
.tcard .top{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.ficon{width:34px;height:34px;border-radius:9px;background:var(--brand-soft);color:var(--brand-ink);display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;flex:none}
.tcard h4{margin:0;font-size:15.5px}.tcard .addr{font-size:12.5px;color:var(--muted)}
.tcard .meta{display:flex;justify-content:space-between;align-items:center;font-size:12.5px;color:var(--muted);border-top:1px solid var(--line-2);padding-top:14px;margin-top:4px}
.tcard.add{border-style:dashed;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;color:var(--muted);min-height:172px;gap:8px}
.tcard.add .plus{width:42px;height:42px;border-radius:12px;background:var(--brand-soft);color:var(--brand-ink);display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:600}
.tcard.add b{color:var(--ink)}
.wizard{max-width:840px}
.steps-bar{display:flex;align-items:center;margin-bottom:30px}
.sbubble{display:flex;align-items:center;gap:10px}
.sbubble .num{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13.5px;background:#fff;border:1.5px solid var(--line);color:var(--muted)}
.sbubble .stxt{font-size:13.5px;color:var(--muted);font-weight:500}
.sbubble.active .num{background:var(--brand);border-color:var(--brand);color:#fff}.sbubble.active .stxt{color:var(--ink);font-weight:600}
.sbubble.done .num{background:var(--green);border-color:var(--green);color:#fff}.sbubble.done .stxt{color:var(--ink)}
.sline{flex:1;height:1.5px;background:var(--line);margin:0 14px}.sline.fill{background:var(--green)}
.wbox{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:28px}
.wbox h2{margin:0 0 6px;font-size:20px;letter-spacing:-.02em}.wbox .wsub{color:var(--muted);font-size:14px;margin:0 0 22px}
.choose{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.choice{border:1.5px solid var(--line);border-radius:13px;padding:16px;cursor:pointer;display:flex;gap:13px;align-items:center;transition:.12s}
.choice:hover{border-color:#c2c8dd}.choice.sel{border-color:var(--brand);background:var(--brand-soft)}
.choice .ficon{width:38px;height:38px}.choice h4{margin:0;font-size:14.5px}.choice .addr{font-size:12.5px;color:var(--muted)}
.choice .rd{margin-left:auto;width:20px;height:20px;border-radius:50%;border:2px solid var(--line)}
.choice.sel .rd{border-color:var(--brand);background:var(--brand);box-shadow:inset 0 0 0 3px #fff}
.drop{border:2px dashed var(--line);border-radius:15px;padding:48px 24px;text-align:center;background:var(--bg-softer);cursor:pointer;transition:.15s}
.drop:hover{border-color:var(--brand-2);background:var(--brand-soft)}
.drop .dic{width:56px;height:56px;border-radius:15px;background:#fff;border:1px solid var(--line);display:flex;align-items:center;justify-content:center;font-size:24px;margin:0 auto 16px}
.drop h4{margin:0 0 6px;font-size:16px}.drop p{margin:0;color:var(--muted);font-size:13.5px}
.uploaded{display:flex;align-items:center;gap:13px;border:1px solid var(--line);border-radius:13px;padding:15px 16px;margin-top:14px}
.uploaded .fi{width:38px;height:38px;border-radius:9px;background:#fde9ec;color:#d6455d;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12px;flex:none}
.uploaded .nm{font-weight:600;font-size:14px}.uploaded .mt{color:var(--muted);font-size:12.5px}.uploaded .ok{margin-left:auto;color:var(--green);font-weight:600;font-size:13px}
.extract-note{display:flex;gap:10px;align-items:flex-start;background:var(--teal-soft);border-radius:12px;padding:13px 15px;font-size:13px;color:#0a6b60;margin-bottom:18px}
.terms-table .tr{display:grid;grid-template-columns:1.1fr 1.4fr 90px;gap:12px;align-items:center;padding:11px 4px;border-top:1px solid var(--line-2)}
.terms-table .tr:first-child{border-top:none}.terms-table .lbl{font-size:13px;color:var(--muted);font-weight:500}
.terms-table input{font-family:inherit;font-size:14px;font-weight:600;color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:9px 11px;width:100%;outline:none}
.terms-table input:focus{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
.conf{font-size:11.5px;font-weight:600;padding:4px 9px;border-radius:999px;text-align:center}
.conf.hi{background:var(--green-soft);color:var(--green)}.conf.med{background:var(--amber-soft);color:var(--amber)}
.redline-doc{border:1px solid var(--line);border-radius:13px;padding:30px 34px;font-size:14px;line-height:2;background:#fff;max-height:420px;overflow-y:auto}
.redline-doc h4{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:22px 0 8px}.redline-doc h4:first-child{margin-top:0}
.del{background:#ffe3e9;color:#b3243c;text-decoration:line-through;padding:0 3px;border-radius:3px}
.ins{background:#dff6ea;color:#0a7a52;padding:0 3px;border-radius:3px;font-weight:600}
.wfoot{display:flex;gap:10px;margin-top:22px}.wfoot .btn-ghost{margin-right:auto}
.warn-band{display:flex;gap:11px;align-items:flex-start;background:var(--amber-soft);border:1px solid #f3dcae;border-radius:12px;padding:13px 16px;font-size:13px;color:#8a5a00;margin-bottom:22px}
.set-row{display:flex;justify-content:space-between;align-items:center;padding:16px 0;border-top:1px solid var(--line-2)}.set-row:first-child{border-top:none}
.set-row .k{font-weight:600;font-size:14px}.set-row .k small{display:block;color:var(--muted);font-weight:400;font-size:12.5px;margin-top:2px}
.plan-box{display:flex;align-items:center;gap:18px;background:linear-gradient(135deg,#4338ca,#7c3aed);color:#fff;border-radius:15px;padding:22px 24px}
.plan-box .pname{font-size:20px;font-weight:800}.plan-box .ppr{opacity:.9;font-size:14px}.plan-box .btn{margin-left:auto;background:#fff;color:var(--brand)}
.bar-track{height:8px;background:var(--line);border-radius:99px;overflow:hidden;margin-top:8px}.bar-fill{height:100%;background:var(--brand);border-radius:99px}
.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg-soft);padding:40px 16px}
.auth{width:100%;max-width:410px;background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:34px;box-shadow:var(--shadow)}
.auth .logo{margin-bottom:20px}.auth h1{font-size:22px;letter-spacing:-.02em;margin:0 0 4px}.auth .sub{color:var(--muted);font-size:14px;margin:0 0 22px}
.auth label{display:block;font-weight:600;font-size:13px;margin:16px 0 6px}
.auth input{width:100%;font-size:14px;border:1px solid var(--line);border-radius:10px;padding:11px 12px}
.auth input:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
.auth button[type=submit]{margin-top:22px;width:100%;background:var(--brand);color:#fff;font-weight:600;font-size:15px;padding:13px;border-radius:11px}
.auth button[type=submit]:hover{background:#3730a3}
.muted-link{font-size:13.5px;color:var(--muted);margin-top:18px;text-align:center}.muted-link a{color:var(--brand);font-weight:600}
.err{background:#fdecef;color:#b3243c;border:1px solid #f6c9d2;border-radius:10px;padding:11px 13px;font-size:13.5px;margin-bottom:4px}
.xbtn{background:none;border:none;color:var(--muted);cursor:pointer;font-size:13px;width:24px;height:24px;border-radius:7px;flex:none}.xbtn:hover{background:#fdecef;color:var(--red)}
.toast{position:fixed;bottom:26px;left:50%;transform:translateX(-50%) translateY(16px);background:var(--ink);color:#fff;padding:12px 20px;border-radius:11px;font-size:14px;font-weight:500;box-shadow:var(--shadow-lg);opacity:0;pointer-events:none;transition:.25s;z-index:200}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.modal-overlay{position:fixed;inset:0;background:rgba(14,22,38,.45);display:none;align-items:center;justify-content:center;z-index:300;padding:20px}
.modal-overlay.show{display:flex}
.modal{background:#fff;border-radius:16px;padding:26px 28px;width:100%;max-width:470px;box-shadow:var(--shadow-lg);max-height:90vh;overflow-y:auto}
.modal h3{margin:0 0 4px;font-size:19px;letter-spacing:-.01em}
.modal input[type=text],.modal select{width:100%;font-size:14px;border:1px solid var(--line);border-radius:10px;padding:11px 12px;background:#fff}
.modal input:focus,.modal select:focus{outline:none;border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
.filepick{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:24px}
.modal-err{background:#fdecef;color:#b3243c;border:1px solid #f6c9d2;border-radius:10px;padding:10px 12px;font-size:13px;margin-top:12px}
.legal{max-width:760px;margin:0 auto;padding:60px 24px}.legal h1{font-size:30px;letter-spacing:-.02em}.legal h2{font-size:18px;margin-top:30px}.legal p{color:var(--ink-2);font-size:15px;line-height:1.7}.legal a.back{color:var(--brand);font-weight:600;font-size:14px}
.field-label{display:block;font-weight:600;font-size:13.5px;margin:16px 0 8px}.field-label:first-of-type{margin-top:0}
.content select{font-family:inherit}
@media(max-width:900px){.nav-links{display:none}h1.hero-h{font-size:40px}.hero-shot .body,.steps,.diff,.pricing,.security,.cards,.tgrid,.choose{grid-template-columns:1fr}.app{grid-template-columns:1fr}.side{display:none}}
"""

FONT = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">')


def page(body: str, title: str = "Draftease") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title>{FONT}<style>{CSS}</style></head><body>{body}</body></html>')


# --------------------------------------------------------------------------- #
# marketing landing (logged out)
# --------------------------------------------------------------------------- #
MARKETING = """
<nav class="nav"><div class="nav-in">
  <div class="logo"><span class="mark">D</span> Draftease</div>
  <div class="nav-links"><a href="#how">How it works</a><a href="#why">Why Draftease</a><a href="#security">Security</a><a href="#pricing">Pricing</a></div>
  <div class="nav-right"><a class="btn btn-ghost" href="/login">Log in</a><a class="btn btn-primary" href="/start">Start free trial</a></div>
</div></nav>
<div class="mkt"><section class="hero">
  <div class="pill"><span class="dot"></span> Built for landlords &amp; their brokers · Your docs stay in your cloud</div>
  <h1 class="hero-h">Drop in a signed LOI. Get back a <span class="grad">lease redline</span>.</h1>
  <p class="hero-sub">Draftease applies the terms from a signed letter of intent to your property's own form lease and returns a clean, tracked-changes first draft — ready for your attorney. No setup project, no platform to learn.</p>
  <div class="hero-cta"><a class="btn btn-primary btn-lg" href="/start">Start free trial</a><a class="btn btn-outline btn-lg" href="#how">See how it works →</a></div>
  <div class="hero-note"><b>No credit card.</b> Be drafting in minutes — not after a 6-week implementation.</div>
  <div class="hero-shot">
    <div class="bar"><i></i><i></i><i></i><span class="u">app.draftease.com · 350 Park Ave — Lockton Advisors</span></div>
    <div class="body">
      <div class="shot-doc"><h5>Redline · your form lease</h5>
        Base Rent shall be <span class="del">$00.00</span> <span class="ins">$72.50</span> per rentable square foot.<br>
        The Term shall be <span class="del">[NN]</span> <span class="ins">87</span> months commencing <span class="del">[DATE]</span> <span class="ins">Sept 1, 2026</span>.<br>
        Tenant shall receive <span class="del">[NN]</span> <span class="ins">4</span> months of abated Base Rent.<br>
        Landlord's improvement allowance shall be <span class="del">$00.00</span> <span class="ins">$95.00</span> per RSF.</div>
      <div class="shot-terms"><div class="th">Terms read from the LOI</div>
        <div class="tr"><span class="k">Base rent / RSF</span><span class="v">$72.50</span></div>
        <div class="tr"><span class="k">Term</span><span class="v">87 months</span></div>
        <div class="tr"><span class="k">Commencement</span><span class="v">Sept 1, 2026</span></div>
        <div class="tr"><span class="k">Free rent</span><span class="v">4 months</span></div>
        <div class="tr"><span class="k">TI allowance</span><span class="v">$95.00 / RSF</span></div>
        <div class="tr"><span class="k">Renewal option</span><span class="v">One 5-yr</span></div></div>
    </div>
  </div>
  <div class="trust"><div class="trust-label">Trusted by owners, leasing teams &amp; their counsel</div>
    <div class="trust-row"><span>MERIDIAN</span><span>Hartwell RE</span><span>Northgate</span><span>BluePoint</span><span>Caldera</span></div></div>
</section></div>
<section class="section alt" id="how"><div class="mkt">
  <div class="sec-head"><div class="sec-tag">How it works</div><h2>From LOI to redline in three steps</h2><p>Set up each property's form lease once. Every deal after that is a few clicks.</p></div>
  <div class="steps">
    <div class="step"><div class="n">1</div><h3>Upload the signed LOI</h3><p>Pick the property and drop in the executed letter of intent — any format. Draftease reads the business terms inside your own cloud.</p></div>
    <div class="step"><div class="n">2</div><h3>Confirm the terms</h3><p>Review the extracted rent, term, free rent, TI and options side-by-side with the LOI. Edit anything before a draft is generated.</p></div>
    <div class="step"><div class="n">3</div><h3>Download the redline</h3><p>Get a native Word tracked-changes document showing exactly how your form lease was modified — ready to send to your attorney.</p></div>
  </div>
</div></section>
<section class="section" id="why"><div class="mkt">
  <div class="sec-head"><div class="sec-tag">Why Draftease</div><h2>Not a platform you move into. A tool that does one job.</h2><p>Other systems make you re-encode your forms and learn a new drafting suite. Draftease works on the lease you already use.</p></div>
  <div class="diff">
    <div class="dcard"><div class="ic">⚡</div><h3>Zero-setup onboarding</h3><p>Upload your form, tag where the terms go, and you're live the same day. No implementation project, no professional-services bill.</p></div>
    <div class="dcard"><div class="ic">📄</div><h3>Your form, redlined</h3><p>We don't impose a template. The output is your exact lease with tracked changes — the document your attorney already knows.</p></div>
    <div class="dcard"><div class="ic">🔒</div><h3>Private by design</h3><p>Your lease never touches an external AI. Redlines are generated by deterministic software in infrastructure you control.</p></div>
  </div>
</div></section>
<section class="section alt" id="security"><div class="mkt"><div class="security">
  <div><div class="sec-tag">Security &amp; privacy</div><h2>Your leases stay in your cloud.</h2>
    <p class="lead">Draftease is built so your confidential documents are never sent to a third-party AI service. The form lease and the redline are processed by deterministic software on infrastructure you control.</p>
    <div class="sec-list">
      <div class="sec-item"><div class="chk">✓</div><div><b>No documents sent to external LLMs</b><span>Redline generation is pure software — no AI ever touches your lease.</span></div></div>
      <div class="sec-item"><div class="chk">✓</div><div><b>Private term extraction</b><span>LOIs are read by a model running inside your own tenant, isolated per customer.</span></div></div>
      <div class="sec-item"><div class="chk">✓</div><div><b>Encrypted &amp; access-controlled</b><span>Encryption at rest and in transit, with per-tenant data isolation.</span></div></div>
      <div class="sec-item"><div class="chk">✓</div><div><b>We never train on your data</b><span>Your documents are yours. Deleted on request, exported on demand.</span></div></div>
    </div></div>
  <div class="sec-card"><div class="flow">
    <div class="flow-node your"><div class="fic">📄</div><div><b>Signed LOI</b><small>Uploaded by your team</small></div></div>
    <div class="flow-arrow">↓</div>
    <div class="flow-node your"><div class="fic">🔒</div><div><b>Private extraction</b><small>Model in your own cloud tenant</small></div></div>
    <div class="flow-arrow">↓</div>
    <div class="flow-node your"><div class="fic">⚙️</div><div><b>Merge &amp; redline engine</b><small>Deterministic — no AI, your server</small></div></div>
    <div class="flow-arrow">↓</div>
    <div class="flow-node your"><div class="fic">✅</div><div><b>Tracked-changes lease</b><small>Stays in your storage</small></div></div>
  </div></div>
</div></div></section>
<section class="section" id="pricing"><div class="mkt">
  <div class="sec-head"><div class="sec-tag">Pricing</div><h2>Pay per deal, or go unlimited</h2><p>Start with a single lease. No setup fees, no onboarding project, no surprises.</p></div>
  <div class="pricing">
    <div class="price"><h3>Single lease</h3><div class="desc">Try it on one deal — no commitment.</div><div class="amt">$10<span>/lease</span></div>
      <ul><li>1 lease redline</li><li>Your own form template</li><li>Word tracked-changes output</li><li>Attorney-ready first draft</li></ul><a class="btn btn-outline btn-block" href="/signup">Start with one lease</a></div>
    <div class="price"><h3>Pay as you go</h3><div class="desc">For occasional deals across properties.</div><div class="amt">$50<span>/contract</span></div>
      <ul><li>$50 per redline, any contract</li><li>Unlimited templates</li><li>No monthly fee</li><li>Email support</li></ul><a class="btn btn-outline btn-block" href="/signup">Start pay-as-you-go</a></div>
    <div class="price pop"><div class="badge">Best value</div><h3>Unlimited</h3><div class="desc">For active owners closing regularly.</div><div class="amt">$199<span>/mo</span></div>
      <ul><li>Unlimited contracts &amp; redlines</li><li>Unlimited templates &amp; seats</li><li>Audit log &amp; exports</li><li>Priority support</li></ul><a class="btn btn-primary btn-block" href="/signup">Go unlimited</a></div>
  </div>
  <div style="max-width:780px;margin:34px auto 0;text-align:center;color:var(--muted);font-size:14px;line-height:1.65">
    <b style="color:var(--ink-2)">How we compare:</b> the leading lease-drafting platform charges <b>$250 per lease</b> plus <b>$100 per amendment</b>, after a roughly six-week onboarding. Draftease starts at <b>$10</b>, never charges more than <b>$50 per contract</b> pay-as-you-go, and goes fully unlimited at $199/mo — well over 30% cheaper at every level, with no onboarding project.
  </div>
</div></section>
<div class="cta-band"><h2>Stop paying for blank-page drafting.</h2><p>Generate your first lease redline today.</p><a class="btn btn-primary btn-lg" href="/start">Create a redline</a></div>
<footer>
  <div class="foot-in">
    <div><div class="logo"><span class="mark">D</span> Draftease</div><p>Lease redlines from your LOIs — securely, in your own cloud.</p></div>
    <div class="foot-cols">
      <div class="foot-col"><h5>Product</h5><a href="#how">How it works</a><a href="#why">Why Draftease</a><a href="#security">Security</a><a href="#pricing">Pricing</a></div>
      <div class="foot-col"><h5>Company</h5><a href="/signup">Get started</a><a href="/login">Log in</a></div>
      <div class="foot-col"><h5>Legal</h5><a href="/legal">Terms</a><a href="/legal">Privacy</a><a href="/legal">Disclaimer</a></div>
    </div>
  </div>
  <div class="disclaimer">Draftease is a document-drafting tool, not a law firm, and does not provide legal advice. Generated redlines are first drafts intended for review and finalization by a licensed attorney. Use of Draftease does not create an attorney-client relationship.</div>
  <div class="foot-bottom"><span>© 2026 Draftease, Inc.</span><span>SOC 2 Type II · GDPR-ready · Encrypted at rest</span></div>
</footer>
"""

# --------------------------------------------------------------------------- #
# app shell (logged in) — sentinels replaced per request
# --------------------------------------------------------------------------- #
APP_SHELL = """
<div class="app" id="app">
  <aside class="side">
    <div class="logo" style="cursor:pointer" onclick="go('dashboard')"><span class="mark">D</span> Draftease</div>
    <button class="btn btn-primary new-btn" onclick="go('wizard')">+ New redline</button>
    <div class="nav-group">Workspace</div>
    <div class="side-link active" data-nav="dashboard" onclick="go('dashboard')"><span class="si">▦</span> Dashboard</div>
    <div class="side-link" data-nav="redlines" onclick="go('redlines')"><span class="si">✍</span> Redlines</div>
    <div class="side-link" data-nav="templates" onclick="go('templates')"><span class="si">📁</span> Templates</div>
    <div class="nav-group">Account</div>
    <div class="side-link" data-nav="settings" onclick="go('settings')"><span class="si">⚙</span> Settings &amp; billing</div>
    <div class="side-foot">
      <div class="side-acct"><div class="avatar">__INITIALS__</div><div class="nm">__NAME__<small>Signed in</small></div></div>
      <form method="post" action="/logout" style="margin:6px 0 0"><input type="hidden" name="csrf" value="__CSRF__">
        <button class="side-link" type="submit" style="width:100%;text-align:left"><span class="si">←</span> Log out</button></form>
    </div>
  </aside>
  <div class="main">
    <div class="topbar">
      <div><h1 id="pageTitle">Dashboard</h1><div class="sub" id="pageSub">Welcome back — here's your deal activity.</div></div>
      <div class="topbar-right"><div class="search">🔎 <input id="searchBox" placeholder="Search deals, templates…" oninput="setQuery(this.value)"></div>
        <button class="btn btn-primary" onclick="go('wizard')">+ New redline</button></div>
    </div>
    <div class="content" id="appContent"></div>
  </div>
</div>
<div id="toast" class="toast"></div>
<div id="modal" class="modal-overlay">
  <div class="modal">
    <h3>Add a property template</h3>
    <div class="muted" style="font-size:13px;margin-bottom:2px">Upload your form lease — we’ll detect its {{tokens}} automatically.</div>
    <label class="field-label">Property / template name</label>
    <input id="upName" type="text" placeholder="e.g. 350 Park Avenue" autocomplete="off">
    <label class="field-label">Type</label>
    <select id="upKind"><option>Office</option><option>Retail</option><option>Industrial</option><option>Mixed-use</option><option>Other</option></select>
    <label class="field-label">Form lease (.docx with {{tokens}})</label>
    <div class="filepick">
      <button type="button" class="btn btn-outline" onclick="document.getElementById('upFile').click()">Choose .docx file…</button>
      <span id="upFileName" class="muted" style="font-size:13px">No file chosen</span>
    </div>
    <input id="upFile" type="file" accept=".docx" style="display:none" onchange="onUpFile()">
    <div class="hint" style="margin-top:8px">No file handy? <a href="/sample-lease">Download a sample</a> to try it.</div>
    <div id="upErr" class="modal-err" style="display:none"></div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeUploadModal()">Cancel</button>
      <button class="btn btn-primary" id="upBtn" onclick="uploadTemplate()">Upload template</button>
    </div>
  </div>
</div>
<div id="tagmodal" class="modal-overlay">
  <div class="modal" style="max-width:580px">
    <h3 id="tgTitle">Tag a clean lease</h3>
    <div class="muted" id="tgIntro" style="font-size:13px;margin-bottom:2px">For a lease with no {{tokens}}. We detect standard terms; you confirm the exact text, and we insert the tokens for you.</div>
    <div id="tgNewOnly">
    <label class="field-label">Template name</label>
    <input id="tgName" type="text" placeholder="e.g. 606 - 1155 Lease" autocomplete="off">
    <label class="field-label">Type</label>
    <select id="tgKind"><option>Office</option><option>Retail</option><option>Industrial</option><option>Mixed-use</option><option>Other</option></select>
    <label class="field-label">Lease file (.docx)</label>
    <div class="filepick">
      <button type="button" class="btn btn-outline" onclick="document.getElementById('tgFile').click()">Choose .docx file…</button>
      <span id="tgFileName" class="muted" style="font-size:13px">No file chosen</span>
      <button type="button" class="btn btn-primary btn-sm" id="tgScanBtn" onclick="scanLease()">Scan for terms</button>
    </div>
    <input id="tgFile" type="file" accept=".docx" style="display:none" onchange="onTagFile()">
    </div>
    <div id="tgRows" style="margin-top:14px;max-height:46vh;overflow-y:auto"></div>
    <div id="tgErr" class="modal-err" style="display:none"></div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closeTagModal()">Cancel</button>
      <button class="btn btn-primary" id="tgCreate" onclick="createTagged()" style="opacity:.5;pointer-events:none">Create tagged template</button>
    </div>
  </div>
</div>
<script>
const CSRF="__CSRF__";
const FULLTERMS=__FULLTERMS__;
let STATE={templates:[],deals:[],loaded:false};
let view='dashboard',selTpl=null,wizardStep=1,q='';
function toast(msg){const t=document.getElementById('toast');if(!t)return;t.textContent=msg;t.classList.add('show');clearTimeout(window._tt);window._tt=setTimeout(()=>t.classList.remove('show'),2600);}
function setQuery(v){q=(v||'').trim().toLowerCase();if(view==='dashboard'||view==='redlines'||view==='templates')render();}
function matchq(s){return !q||String(s).toLowerCase().includes(q);}
function filteredDeals(){return q?STATE.deals.filter(d=>matchq(d.name)||matchq(d.prop)):STATE.deals;}
function filteredTemplates(){return q?STATE.templates.filter(t=>matchq(t.name)||matchq(t.kind)):STATE.templates;}
const TITLES={dashboard:["Dashboard","Your deals and templates at a glance."],redlines:["Redlines","Every draft you've generated."],templates:["Templates","Your property form leases."],settings:["Settings & billing","Manage your account and security."],plans:["Plans & billing","Pay per deal, or go unlimited."],wizard:["New redline","Turn a deal's terms into a tracked-changes draft."]};
function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function prettify(t){return String(t).replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());}
function statusTag(s){const m={done:["done","Ready"],review:["review","In review"],draft:["draft","Draft"]};const x=m[s]||m.done;return `<span class="tag ${x[0]}"><span class="d"></span>${x[1]}</span>`;}
async function loadData(){try{const[t,d,b]=await Promise.all([fetch('/api/templates').then(r=>r.json()),fetch('/api/deals').then(r=>r.json()),fetch('/api/billing').then(r=>r.json())]);STATE.templates=Array.isArray(t)?t:[];STATE.deals=Array.isArray(d)?d:[];STATE.billing=b||{};}catch(e){}STATE.loaded=true;render();}
async function startCheckout(plan){if(!(STATE.billing&&STATE.billing.enabled)){toast('Online checkout is not set up yet.');return;}const fd=new FormData();fd.append('plan',plan);fd.append('csrf',CSRF);try{const r=await fetch('/api/checkout',{method:'POST',body:fd});const j=await r.json();if(r.ok&&j.url){window.location=j.url;}else{toast(j.detail||'Could not start checkout.');}}catch(e){toast('Checkout error.');}}
function go(v){view=v;if(v==='wizard'){wizardStep=1;selTpl=null;}document.querySelectorAll('.side-link[data-nav]').forEach(l=>l.classList.toggle('active',l.dataset.nav===v));const t=TITLES[v]||TITLES.dashboard;document.getElementById('pageTitle').textContent=t[0];document.getElementById('pageSub').textContent=t[1];render();document.querySelector('.main').scrollTo(0,0);}
function render(){document.getElementById('appContent').innerHTML=({dashboard:vDashboard,redlines:vRedlines,templates:vTemplates,settings:vSettings,plans:vPlans,wizard:vWizard}[view]||vDashboard)();}
function dealRows(list){if(!STATE.loaded)return `<tr><td colspan="4" class="muted" style="padding:22px 16px">Loading…</td></tr>`;if(!list.length)return `<tr><td colspan="4" class="muted" style="padding:22px 16px">No redlines yet. Click <b>New redline</b> to create your first.</td></tr>`;
  return list.map(d=>`<tr><td style="font-weight:600">${esc(d.name)}</td><td class="muted">${esc(d.prop)}</td><td class="muted">${d.date}</td><td><div style="display:flex;align-items:center;gap:12px">${statusTag(d.status)}<button class="xbtn" title="Delete" onclick="delDeal(${d.id})">✕</button></div></td></tr>`).join('');}
function vDashboard(){const d=STATE.deals;return `
  <div class="cards">
    <div class="stat"><div class="lbl">Total redlines</div><div class="val">${d.length}</div><div class="delta flat">all time</div></div>
    <div class="stat"><div class="lbl">Templates</div><div class="val">${STATE.templates.length}</div><div class="delta flat">form leases</div></div>
    <div class="stat"><div class="lbl">Ready</div><div class="val">${d.filter(x=>x.status==='done').length}</div><div class="delta up">finalized drafts</div></div>
    <div class="stat"><div class="lbl">In review</div><div class="val">${d.filter(x=>x.status==='review').length}</div><div class="delta flat">with counsel</div></div></div>
  <div class="panel"><div class="panel-head"><h3>Recent redlines</h3><button class="btn btn-primary btn-sm" onclick="go('wizard')">+ New redline</button></div>
    <div class="panel-body"><table><thead><tr><th>Deal</th><th>Property</th><th>Created</th><th>Status</th></tr></thead><tbody>${dealRows(filteredDeals().slice(0,8))}</tbody></table></div></div>`;}
function vRedlines(){return `<div class="panel"><div class="panel-head"><h3>All redlines</h3><button class="btn btn-primary btn-sm" onclick="go('wizard')">+ New redline</button></div>
  <div class="panel-body"><table><thead><tr><th>Deal</th><th>Property</th><th>Created</th><th>Status</th></tr></thead><tbody>${dealRows(filteredDeals())}</tbody></table></div></div>`;}
function vTemplates(){const cards=filteredTemplates().map(t=>`<div class="tcard" style="cursor:pointer" title="Click to open / download this template" onclick="openTemplate(${t.id})"><div class="top"><div class="ficon">${esc((t.kind||'O').slice(0,1))}</div><div><h4>${esc(t.name)}</h4><div class="addr">${esc(t.kind)} · ${t.tokens.length} fields</div></div><button class="xbtn" style="margin-left:auto" title="Delete" onclick="event.stopPropagation();delTemplate(${t.id})">✕</button></div>
    <div class="meta"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:130px">${esc(t.filename)}</span><button class="btn btn-outline btn-sm" title="Auto-detect &amp; tag standard fields" onclick="event.stopPropagation();tagExisting(${t.id})">🏷️ ${t.tokens.length?'Re-tag':'Tag fields'}</button></div>${t.tokens.length?'':'<div class="muted" style="font-size:12px;margin-top:6px;color:var(--brand)">No fields yet — click Tag fields to make it redline-ready.</div>'}</div>`).join('');
  const empty=(!STATE.templates.length&&STATE.loaded)?`<div class="muted" style="grid-column:1/-1;font-size:14px;margin-bottom:2px">No templates yet — upload your first property form lease.</div>`:'';
  return `<div id="tplMsg"></div><div class="tgrid">${empty}${cards}
    <div class="tcard add" onclick="showUpload()"><div class="plus">+</div><b>Add a property template</b><span>Upload a form lease that already has {{tokens}}</span></div>
    <div class="tcard add" onclick="showTagModal()"><div class="plus">🏷️</div><b>Tag a clean lease</b><span>No tokens? Auto-detect &amp; tag standard terms</span></div></div>`;}
function showUpload(){const m=document.getElementById('modal');document.getElementById('upName').value='';document.getElementById('upKind').value='Office';document.getElementById('upFile').value='';const lbl=document.getElementById('upFileName');lbl.textContent='No file chosen';lbl.style.color='';const e=document.getElementById('upErr');e.style.display='none';e.textContent='';document.getElementById('upBtn').textContent='Upload template';m.classList.add('show');setTimeout(()=>document.getElementById('upName').focus(),50);}
function closeUploadModal(){document.getElementById('modal').classList.remove('show');}
function onUpFile(){const f=document.getElementById('upFile').files[0];const lbl=document.getElementById('upFileName');if(f){lbl.textContent=f.name;lbl.style.color='var(--ink)';const n=document.getElementById('upName');if(!n.value.trim()){n.value=f.name.replace(/\\.docx$/i,'');}}else{lbl.textContent='No file chosen';lbl.style.color='';}}
async function uploadTemplate(){const name=document.getElementById('upName').value.trim();const kind=document.getElementById('upKind').value;const f=document.getElementById('upFile').files[0];const err=document.getElementById('upErr');const btn=document.getElementById('upBtn');
  const showErr=(m)=>{err.textContent=m;err.style.display='block';};
  if(!name){showErr('Please enter a name.');return;}if(!f){showErr('Please choose a .docx file.');return;}
  const fd=new FormData();fd.append('name',name);fd.append('kind',kind);fd.append('file',f);fd.append('csrf',CSRF);
  err.style.display='none';btn.textContent='Uploading…';btn.style.opacity='.7';
  try{const r=await fetch('/api/templates',{method:'POST',body:fd});if(!r.ok){showErr(await r.text());btn.textContent='Upload template';btn.style.opacity='1';return;}
    const t=await r.json();closeUploadModal();btn.style.opacity='1';btn.textContent='Upload template';await loadData();go('templates');
    if(t&&(t.tokens||[]).length===0){toast('No {{tokens}} found — let\\'s tag the standard fields.');tagExisting(t.id);}
    else toast('Template uploaded — '+((t.tokens||[]).length)+' fields ✓');
  }catch(e){showErr(''+e);btn.textContent='Upload template';btn.style.opacity='1';}}
async function delTemplate(id){if(!confirm('Delete this template? This cannot be undone.'))return;const fd=new FormData();fd.append('id',id);fd.append('csrf',CSRF);await fetch('/api/templates/delete',{method:'POST',body:fd});await loadData();toast('Template deleted');}
async function openTemplate(id){try{const r=await fetch('/api/templates/'+id+'/file');if(!r.ok){toast('Could not open that file.');return;}const b=await r.blob();const t=STATE.templates.find(x=>x.id===id);const url=URL.createObjectURL(b);const a=document.createElement('a');a.href=url;a.download=(((t&&t.name)||'template').replace(/[^a-z0-9]+/gi,'_'))+'.docx';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);toast('Template downloaded');}catch(e){toast('Could not open that file.');}}
let _tgFile=null, _retagId=null;
function showTagModal(){_tgFile=null;_retagId=null;document.getElementById('tgTitle').textContent='Tag a clean lease';document.getElementById('tgIntro').textContent='For a lease with no {{tokens}}. We detect standard terms; you confirm the exact text, and we insert the tokens for you.';document.getElementById('tgNewOnly').style.display='';document.getElementById('tgName').value='';document.getElementById('tgKind').value='Office';document.getElementById('tgFile').value='';const lbl=document.getElementById('tgFileName');lbl.textContent='No file chosen';lbl.style.color='';document.getElementById('tgScanBtn').textContent='Scan for terms';document.getElementById('tgRows').innerHTML='<div class="muted" style="font-size:13px">Choose your lease, then click <b>Scan for terms</b>.</div>';const e=document.getElementById('tgErr');e.style.display='none';const c=document.getElementById('tgCreate');c.textContent='Create tagged template';c.style.opacity='.5';c.style.pointerEvents='none';document.getElementById('tagmodal').classList.add('show');setTimeout(()=>document.getElementById('tgName').focus(),50);}
function closeTagModal(){document.getElementById('tagmodal').classList.remove('show');_retagId=null;document.getElementById('tgNewOnly').style.display='';}
function tagRowsHtml(j){return (j.standard||[]).map(s=>{const v=((j.suggestions||{})[s.key]||'').replace(/"/g,'&quot;');return `<div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center;padding:5px 0;border-top:1px solid var(--line-2)"><div style="font-size:13px;color:var(--muted)">${s.label}</div><input data-tk="${s.key}" value="${v}" placeholder="(not found — paste exact text or leave blank)" style="width:100%;font-size:13px;border:1px solid var(--line);border-radius:8px;padding:8px 10px"></div>`;}).join('');}
async function tagExisting(id){const t=STATE.templates.find(x=>x.id===id);_retagId=id;_tgFile=null;
  document.getElementById('tgTitle').textContent='Tag fields — '+esc((t&&t.name)||'template');
  document.getElementById('tgIntro').textContent='We auto-detect standard terms in this lease. Confirm the exact current text for each, then we insert the tokens in place. Detection is best-effort — please verify.';
  document.getElementById('tgNewOnly').style.display='none';
  const rows=document.getElementById('tgRows');rows.innerHTML='<div class="muted" style="font-size:13px">Scanning the lease…</div>';
  const e=document.getElementById('tgErr');e.style.display='none';
  const c=document.getElementById('tgCreate');c.textContent='Apply tags';c.style.opacity='.5';c.style.pointerEvents='none';
  document.getElementById('tagmodal').classList.add('show');
  try{const j=await fetch('/api/templates/'+id+'/autotag').then(r=>{if(!r.ok)throw new Error('scan failed');return r.json();});
    rows.innerHTML='<div class="muted" style="font-size:12.5px;margin-bottom:6px">Confirm the exact current text for each term (edit or clear any). We replace these with {{tokens}}.</div>'+tagRowsHtml(j);
    c.style.opacity='1';c.style.pointerEvents='auto';
  }catch(err){rows.innerHTML='';e.textContent='Could not scan this template: '+err;e.style.display='block';}}
function onTagFile(){const f=document.getElementById('tgFile').files[0];_tgFile=f||null;const lbl=document.getElementById('tgFileName');if(f){lbl.textContent=f.name;lbl.style.color='var(--ink)';const n=document.getElementById('tgName');if(!n.value.trim())n.value=f.name.replace(/\\.docx$/i,'');}else{lbl.textContent='No file chosen';lbl.style.color='';}}
async function scanLease(){const err=document.getElementById('tgErr');const show=(m)=>{err.textContent=m;err.style.display='block';};if(!_tgFile){show('Please choose a .docx file first.');return;}
  const fd=new FormData();fd.append('file',_tgFile);fd.append('csrf',CSRF);err.style.display='none';const sb=document.getElementById('tgScanBtn');sb.textContent='Scanning…';
  try{const r=await fetch('/api/autotag',{method:'POST',body:fd});if(!r.ok){show(await r.text());sb.textContent='Scan for terms';return;}const j=await r.json();sb.textContent='Re-scan';
    const rows=(j.standard||[]).map(s=>{const v=((j.suggestions||{})[s.key]||'').replace(/"/g,'&quot;');return `<div style="display:grid;grid-template-columns:140px 1fr;gap:10px;align-items:center;padding:5px 0;border-top:1px solid var(--line-2)"><div style="font-size:13px;color:var(--muted)">${s.label}</div><input data-tk="${s.key}" value="${v}" placeholder="(not found — paste exact text or leave blank)" style="width:100%;font-size:13px;border:1px solid var(--line);border-radius:8px;padding:8px 10px"></div>`;}).join('');
    document.getElementById('tgRows').innerHTML='<div class="muted" style="font-size:12.5px;margin-bottom:6px">Confirm the exact current text for each term (edit or clear any). We will replace these with tokens.</div>'+rows;
    const c=document.getElementById('tgCreate');c.style.opacity='1';c.style.pointerEvents='auto';
  }catch(e){show(''+e);sb.textContent='Scan for terms';}}
async function createTagged(){const err=document.getElementById('tgErr');const show=(m)=>{err.textContent=m;err.style.display='block';};
  const map={};document.querySelectorAll('#tgRows [data-tk]').forEach(i=>{const v=i.value.trim();if(v)map[i.dataset.tk]=v;});
  if(_retagId){
    if(!Object.keys(map).length){show('Add at least one term to tag.');return;}
    const c=document.getElementById('tgCreate');const fd=new FormData();fd.append('mapping',JSON.stringify(map));fd.append('csrf',CSRF);
    err.style.display='none';c.textContent='Applying…';c.style.opacity='.7';
    try{const r=await fetch('/api/templates/'+_retagId+'/retag',{method:'POST',body:fd});if(!r.ok){show(await r.text());c.textContent='Apply tags';c.style.opacity='1';return;}const t=await r.json();closeTagModal();await loadData();go('templates');toast('Tagged '+(t.tagged||0)+' field'+(t.tagged===1?'':'s')+' ✓');}catch(e){show(''+e);c.textContent='Apply tags';c.style.opacity='1';}
    return;
  }
  const name=document.getElementById('tgName').value.trim();if(!name){show('Please enter a name.');return;}if(!_tgFile){show('Please choose a file.');return;}
  if(!Object.keys(map).length){show('Add at least one term to tag (or use Add a property template instead).');return;}
  const fd=new FormData();fd.append('name',name);fd.append('kind',document.getElementById('tgKind').value);fd.append('file',_tgFile);fd.append('mapping',JSON.stringify(map));fd.append('csrf',CSRF);
  err.style.display='none';const c=document.getElementById('tgCreate');c.textContent='Tagging…';c.style.opacity='.7';
  try{const r=await fetch('/api/templates/tag',{method:'POST',body:fd});if(!r.ok){show(await r.text());c.textContent='Create tagged template';c.style.opacity='1';return;}const t=await r.json();closeTagModal();await loadData();go('templates');toast('Tagged template created — '+(t.tagged||0)+' tokens inserted ✓');}catch(e){show(''+e);c.textContent='Create tagged template';c.style.opacity='1';}}
async function delDeal(id){if(!confirm('Delete this redline record? This cannot be undone.'))return;const fd=new FormData();fd.append('id',id);fd.append('csrf',CSRF);await fetch('/api/deals/delete',{method:'POST',body:fd});await loadData();toast('Redline deleted');}
function vSettings(){return `
  <div class="plan-box"><div><div class="pname">Free trial</div><div class="ppr">Upgrade anytime · no card on file</div></div><button class="btn" onclick="go('plans')">Manage plan</button></div>
  <div class="panel" style="margin-top:22px"><div class="panel-head"><h3>Your workspace</h3></div><div class="panel-body" style="padding:6px 22px 14px">
    <div class="set-row"><div class="k">Templates <small>Property form leases uploaded</small></div><span class="muted">${STATE.templates.length}</span></div>
    <div class="set-row"><div class="k">Redlines <small>Drafts generated</small></div><span class="muted">${STATE.deals.length}</span></div>
    <div class="set-row"><div class="k">Redline engine <small>Deterministic — no AI on your documents</small></div><span class="tag done"><span class="d"></span>Enabled</span></div>
    <div class="set-row"><div class="k">Single sign-on (SSO) <small>SAML / Okta</small></div><button class="btn btn-outline btn-sm" onclick="toast('SSO configuration is coming soon.')">Configure</button></div>
  </div></div>`;}
function vPlans(){const b=STATE.billing||{plan:'trial',credits:0,enabled:false};
  const plans=[
    {id:'single',n:"Single lease",a:"$10",u:"/lease",d:"Try it on one deal — no commitment.",f:["1 lease redline","Your own form template","Word tracked-changes output"],pop:false},
    {id:'payg',n:"Pay as you go",a:"$50",u:"/contract",d:"For occasional deals across properties.",f:["$50 per redline, any contract","Unlimited templates","No monthly fee"],pop:false},
    {id:'unlimited',n:"Unlimited",a:"$199",u:"/mo",d:"For active owners closing regularly.",f:["Unlimited contracts & redlines","Unlimited templates & seats","Audit log & exports"],pop:true}];
  const planName=b.plan==='unlimited'?'Unlimited':(b.plan==='payg'?'Pay as you go':'Free trial');
  const status=b.plan==='unlimited'?'active — unlimited redlines':`${b.credits} redline credit${b.credits===1?'':'s'} remaining`;
  const note=b.enabled?'':' <span style="color:var(--amber)">· Online checkout is not set up yet (add your Stripe keys — see the setup guide).</span>';
  return `<div style="margin-bottom:18px;color:var(--muted);font-size:14px">Current plan: <b style="color:var(--ink)">${planName}</b> · ${status}.${note}</div>
  <div class="tgrid">${plans.map(p=>`<div class="tcard" style="cursor:default${p.pop?';border:2px solid var(--brand)':''}">
      <h4 style="margin:0 0 4px;font-size:16px">${p.n}${p.pop?' <span class="tag done" style="font-size:10px;vertical-align:middle"><span class="d"></span>Best value</span>':''}</h4>
      <div style="font-size:32px;font-weight:800;letter-spacing:-.02em">${p.a}<span style="font-size:14px;color:var(--muted);font-weight:500">${p.u}</span></div>
      <div class="addr" style="margin:6px 0 14px">${p.d}</div>
      <div style="font-size:13px;color:var(--ink-2);margin-bottom:16px">${p.f.map(x=>`<div style="margin-bottom:7px">✓ ${x}</div>`).join('')}</div>
      <button class="btn ${p.pop?'btn-primary':'btn-outline'} btn-block" onclick="startCheckout('${p.id}')">${p.id==='unlimited'?'Subscribe':'Buy'} — ${p.a}${p.u}</button></div>`).join('')}</div>
  <div class="panel" style="margin-top:22px"><div class="panel-body" style="padding:16px 20px;color:var(--muted);font-size:13.5px;line-height:1.6"><b style="color:var(--ink-2)">How we compare:</b> the leading lease-drafting platform charges $250 per lease plus $100 per amendment after a ~6-week onboarding. Draftease is well over 30% cheaper at every level, with no onboarding project.</div></div>`;}
function vWizard(){
  if(STATE.loaded&&!STATE.templates.length)return `<div class="wbox"><h2>Add a template first</h2><p class="wsub">Upload your property's form lease so Draftease has something to redline.</p><button class="btn btn-primary" onclick="go('templates')">Go to Templates →</button></div>`;
  const labels=["Property","Terms","Redline"];const bar=`<div class="steps-bar">${labels.map((s,i)=>{const n=i+1;const cls=n<wizardStep?'done':(n===wizardStep?'active':'');const line=i<labels.length-1?`<div class="sline ${n<wizardStep?'fill':''}"></div>`:'';return `<div class="sbubble ${cls}"><div class="num">${n<wizardStep?'✓':n}</div><div class="stxt">${s}</div></div>${line}`;}).join('')}</div>`;
  return `<div class="wizard">${bar}<div id="wstep">${wStepBody()}</div></div>`;}
function wStepBody(){
  if(wizardStep===1)return `<div class="wbox"><h2>Which property is this deal for?</h2><p class="wsub">We'll redline that property's stored form lease.</p>
    <div class="choose">${STATE.templates.map(t=>`<div class="choice ${selTpl===t.id?'sel':''}" onclick="pickTpl(${t.id})"><div class="ficon">${esc((t.kind||'O').slice(0,1))}</div><div><h4>${esc(t.name)}</h4><div class="addr">${esc(t.kind)} · ${t.tokens.length} fields</div></div><div class="rd"></div></div>`).join('')}</div>
    <div class="wfoot"><button class="btn btn-ghost" onclick="go('dashboard')">Cancel</button><button class="btn btn-primary" ${selTpl?'':'disabled style="opacity:.5;pointer-events:none"'} onclick="wNext()">Continue →</button></div></div>`;
  if(wizardStep===2){const t=STATE.templates.find(x=>x.id===selTpl)||{tokens:[]};const toks=t.tokens.length?t.tokens:Object.keys(FULLTERMS);
    return `<div class="wbox"><h2>Confirm the deal terms</h2><p class="wsub">Fill the values for ${esc(t.name||'')}. These would be read from the signed LOI.</p>
    <div class="extract-note">🔒 <div>The redline is generated by deterministic software in your cloud — your lease is never sent to an external AI.</div></div>
    <div class="terms-table"><div class="tr"><div class="lbl">Term</div><div class="lbl">Value</div><div class="lbl" style="text-align:center">Field</div></div>
      ${toks.map(tok=>`<div class="tr"><div class="lbl">${esc(prettify(tok))}</div><input data-token="${esc(tok)}" value="${esc(FULLTERMS[tok]||'')}"><div class="conf hi" style="font-size:10px">${esc(tok)}</div></div>`).join('')}</div>
    <div class="wfoot"><button class="btn btn-ghost" onclick="wBack()">← Back</button><button class="btn btn-primary" onclick="genFromTemplate(event)">Generate redline →</button></div></div>`;}
  return `<div class="wbox"><h2>Redline ready ✓</h2><p class="wsub">Your tracked-changes Word document has downloaded, and the deal was added to your dashboard.</p>
    <div class="warn-band">⚠️ <div>This is an automated <b>first draft</b>. Have a licensed attorney review and finalize before execution.</div></div>
    <div class="wfoot"><button class="btn btn-outline" onclick="go('wizard')">+ Another redline</button><button class="btn btn-primary" onclick="go('dashboard')">Go to dashboard →</button></div></div>`;}
function pickTpl(id){selTpl=id;document.getElementById('wstep').innerHTML=wStepBody();}
function wNext(){wizardStep=Math.min(3,wizardStep+1);document.getElementById('appContent').innerHTML=vWizard();}
function wBack(){wizardStep=Math.max(1,wizardStep-1);document.getElementById('appContent').innerHTML=vWizard();}
async function genFromTemplate(e){const btn=e.target;const inputs=document.querySelectorAll('#wstep [data-token]');const terms={};inputs.forEach(i=>terms[i.dataset.token]=i.value);
  const old=btn.textContent;btn.textContent='Generating…';btn.style.opacity='.7';
  try{const fd=new FormData();fd.append('template_id',selTpl);fd.append('terms',JSON.stringify(terms));fd.append('csrf',CSRF);
    const r=await fetch('/api/redline-from-template',{method:'POST',body:fd});
    if(!r.ok){const msg=await r.text();btn.textContent=old;btn.style.opacity='1';if(r.status===402){toast('You are out of credits — choose a plan.');go('plans');}else{alert('Error: '+msg);}return;}
    const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');const t=STATE.templates.find(x=>x.id===selTpl);
    a.href=url;a.download=(t?t.name.replace(/[^a-z0-9]+/gi,'_'):'lease')+'_redline.docx';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
    await loadData();view='wizard';wizardStep=3;document.getElementById('appContent').innerHTML=vWizard();toast('Redline generated ✓');
  }catch(err){alert('Error: '+err);btn.textContent=old;btn.style.opacity='1';}}
loadData();
if(location.search.indexOf('billing=success')>=0){toast('Payment received — updating your plan…');setTimeout(loadData,2500);go('plans');history.replaceState({},'','/');}
else if(location.search.indexOf('billing=cancel')>=0){history.replaceState({},'','/');}
</script>
"""


def app_shell(request: Request, user) -> str:
    name = (user.name or user.email)
    initials = "".join(p[0] for p in (user.name or user.email).replace("@", " ").split()[:2]).upper() or "U"
    body = (APP_SHELL
            .replace("__CSRF__", csrf_token(request))
            .replace("__FULLTERMS__", json.dumps(SAMPLE_TERMS))
            .replace("__INITIALS__", initials)
            .replace("__NAME__", name))
    return page(body, "Draftease")


def login_page(request: Request, error: str = "") -> str:
    err = f'<div class="err">{error}</div>' if error else ""
    signup_link = ('<p class="muted-link">No account? <a href="/signup">Create one</a></p>' if ALLOW_SIGNUP else "")
    return page(f"""<div class="auth-wrap"><div class="auth">
      <a class="logo" href="/"><span class="mark">D</span> Draftease</a>
      <h1>Sign in</h1><p class="sub">Welcome back.</p>{err}
      <form method="post" action="/login"><input type="hidden" name="csrf" value="{csrf_token(request)}">
        <label>Email</label><input type="email" name="email" required autofocus>
        <label>Password</label><input type="password" name="password" required>
        <button type="submit">Sign in</button></form>{signup_link}
    </div></div>""", "Sign in · Draftease")


def signup_page(request: Request, error: str = "") -> str:
    err = f'<div class="err">{error}</div>' if error else ""
    return page(f"""<div class="auth-wrap"><div class="auth">
      <a class="logo" href="/"><span class="mark">D</span> Draftease</a>
      <h1>Create your account</h1><p class="sub">Start drafting in minutes.</p>{err}
      <form method="post" action="/signup"><input type="hidden" name="csrf" value="{csrf_token(request)}">
        <label>Name</label><input type="text" name="name">
        <label>Work email</label><input type="email" name="email" required autofocus>
        <label>Password</label><input type="password" name="password" required>
        <button type="submit">Create account</button></form>
      <p class="muted-link">Already have an account? <a href="/login">Sign in</a></p>
    </div></div>""", "Create account · Draftease")


# --------------------------------------------------------------------------- #
# sample LOI pdf
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
    for label, value in (("Date", t["lease_date"]), ("Property", f"{t['property_name']}, Suite {t['suite']}"),
                         ("Landlord", t["landlord"]), ("Tenant", t["tenant"])):
        pdf.set_font("Helvetica", "B", 10); pdf.write(6, f"{label}: ")
        pdf.set_font("Helvetica", "", 10); pdf.write(6, value); pdf.ln(7)
    pdf.ln(1); pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(W, 6, "The parties propose to enter into a lease on the following principal terms:")
    pdf.ln(2)
    for i, (k, v) in enumerate([
        ("Premises", f"Approximately {t['rentable_sf']} rentable square feet on the {t['floor']} floor"),
        ("Lease Term", f"{t['term_months']} months"), ("Commencement Date", t["commencement_date"]),
        ("Base Rent", f"{t['base_rent_psf']} per RSF per annum ({t['monthly_rent']} per month)"),
        ("Annual Escalation", t["annual_escalation"]), ("Free Rent", f"{t['free_rent_months']} months of abated Base Rent"),
        ("Security Deposit", t["security_deposit"]), ("TI Allowance", f"{t['ti_allowance_psf']} per RSF"),
        ("Renewal Option", t["renewal_option"]), ("Permitted Use", t["permitted_use"])], 1):
        pdf.set_font("Helvetica", "B", 10); pdf.write(6, f"{i}. {k}: ")
        pdf.set_font("Helvetica", "", 10); pdf.write(6, v); pdf.ln(7)
    pdf.ln(2); pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(W, 5, "This Letter of Intent is non-binding and is intended solely to outline the principal "
                         "business terms for a definitive lease to be prepared and reviewed by the parties' counsel.")
    pdf.ln(8); pdf.set_font("Helvetica", "", 10)
    pdf.cell(W, 6, "Agreed and accepted:", new_x="LMARGIN", new_y="NEXT"); pdf.ln(5)
    pdf.cell(W, 6, f"LANDLORD: {t['landlord']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(W, 6, "By: ____________________    Title: ____________", new_x="LMARGIN", new_y="NEXT"); pdf.ln(5)
    pdf.cell(W, 6, f"TENANT: {t['tenant']}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(W, 6, "By: ____________________    Title: ____________", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


# --------------------------------------------------------------------------- #
# public 3-step funnel ( /start )
# --------------------------------------------------------------------------- #
START_PAGE = """
<style>
.flow-top{position:sticky;top:0;z-index:30;display:flex;align-items:center;justify-content:space-between;padding:14px 26px;background:rgba(255,255,255,.85);backdrop-filter:saturate(140%) blur(8px);border-bottom:1px solid var(--line)}
.flow-top .logo{display:inline-flex;align-items:center;gap:9px;font-weight:800;font-size:18px;letter-spacing:-.02em}
.flow-top .logo .mark{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,var(--brand),var(--brand-2));color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800}
.flow-top .rt{display:flex;align-items:center;gap:14px;font-size:14px;font-weight:600}
.flow-top .rt a{color:var(--ink-2)}.flow-top .rt a:hover{color:var(--ink)}
.flow-bg{min-height:100vh;background:radial-gradient(1200px 480px at 50% -8%,var(--brand-soft),transparent 60%),var(--bg-soft)}
.flow-inner{max-width:760px;margin:0 auto;padding:40px 20px 90px}
.flow-hero{text-align:center;margin-bottom:34px}
.flow-hero h1{font-size:46px;line-height:1.04;letter-spacing:-.035em;margin:0 0 12px;font-weight:850}
.flow-hero h1 .grad{background:linear-gradient(120deg,var(--brand),var(--brand-2) 60%,var(--teal));-webkit-background-clip:text;background-clip:text;color:transparent}
.flow-hero p{font-size:17px;color:var(--muted);margin:0;font-weight:500}
.stepcard{position:relative;background:#fff;border:1.5px solid var(--line);border-radius:22px;padding:22px 24px 24px;margin-bottom:18px;box-shadow:var(--shadow);transition:.18s}
.stepcard.active{border-color:var(--brand-2);box-shadow:0 0 0 4px var(--brand-soft),var(--shadow)}
.stepcard.done{border-color:#bfe7d4}
.stepcard.locked{opacity:.55}
.stepcard .shead{display:flex;align-items:center;gap:14px;margin-bottom:16px}
.stepnum{flex:none;width:42px;height:42px;border-radius:13px;display:flex;align-items:center;justify-content:center;font-weight:850;font-size:19px;background:linear-gradient(135deg,var(--brand),var(--brand-2));color:#fff;box-shadow:0 6px 16px rgba(67,56,202,.32)}
.stepcard.done .stepnum{background:linear-gradient(135deg,var(--green),#15b886);box-shadow:0 6px 16px rgba(15,157,110,.3)}
.stepcard.locked .stepnum{background:#cdd2e4;box-shadow:none}
.shead .stitle{font-size:19px;font-weight:800;letter-spacing:-.02em}
.shead .ssub{font-size:13px;color:var(--muted);font-weight:500;margin-top:1px}
.shead .schip{margin-left:auto;font-size:12.5px;font-weight:700;color:var(--green);background:var(--green-soft);padding:5px 11px;border-radius:999px}
.ddwrap{position:relative}
.ddbtn{width:100%;display:flex;align-items:center;gap:12px;text-align:left;border:2px solid var(--line);border-radius:14px;padding:15px 16px;font-size:15.5px;font-weight:650;color:var(--ink);background:var(--bg-softer);transition:.15s}
.ddbtn:hover{border-color:#c4c9de;background:#fff}
.ddbtn.set{border-color:var(--brand-2);background:#fff}
.ddbtn .dlab{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ddbtn .dph{color:var(--muted);font-weight:600}
.ddbtn .dchev{flex:none;color:var(--muted);font-size:13px}
.ddmenu{position:absolute;left:0;right:0;top:calc(100% + 8px);z-index:40;background:#fff;border:1.5px solid var(--line);border-radius:14px;box-shadow:var(--shadow-lg);padding:6px;max-height:330px;overflow:auto}
.ddopt{display:flex;align-items:center;gap:10px;padding:12px 13px;border-radius:10px;font-size:14.5px;font-weight:600;cursor:pointer}
.ddopt:hover{background:var(--bg-soft)}
.ddopt .meta{margin-left:auto;font-size:12.5px;color:var(--muted);font-weight:600}
.ddopt.add{color:var(--brand-ink);font-weight:750}.ddopt.add:hover{background:var(--brand-soft)}
.ddopt .ic{width:26px;height:26px;flex:none;border-radius:8px;background:var(--bg-soft);display:flex;align-items:center;justify-content:center;font-size:14px}
.ddopt.add .ic{background:var(--brand-soft)}
.ddsep{height:1px;background:var(--line-2);margin:5px 4px}
.ddback{position:fixed;inset:0;z-index:35}
.termwrap{margin-top:18px;border-top:1px dashed var(--line);padding-top:16px}
.termhd{font-size:13px;font-weight:800;letter-spacing:.02em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}
.trow{display:grid;gap:10px;align-items:center;padding:6px 0}
.trow .tl{font-size:13.5px;color:var(--ink-2);font-weight:650}
.trow input{width:100%;font-size:14px;border:1.5px solid var(--line);border-radius:10px;padding:10px 12px;font-weight:550}
.trow input:focus{outline:none;border-color:var(--brand-2);box-shadow:0 0 0 3px var(--brand-soft)}
.bigcreate{width:100%;justify-content:center;font-size:16px;font-weight:750;padding:16px;border-radius:14px;background:linear-gradient(135deg,var(--brand),var(--brand-2));color:#fff;display:inline-flex;align-items:center;gap:9px;transition:.15s;box-shadow:0 10px 26px rgba(67,56,202,.3)}
.bigcreate:hover{filter:brightness(1.05)}
.bigcreate:disabled{background:#c8cce0;box-shadow:none;cursor:default;opacity:1}
.flownote{background:var(--amber-soft);border:1px solid #fbe2b8;color:#8a5a00;border-radius:12px;padding:12px 14px;font-size:13.5px;font-weight:600;margin-bottom:14px}
.planrow{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:4px 0 16px}
.planopt2{border:2px solid var(--line);border-radius:14px;padding:14px;cursor:pointer;font-weight:750;transition:.15s}
.planopt2:hover{border-color:#c4c9de}
.planopt2.sel{border-color:var(--brand-2);background:var(--brand-soft)}
.planopt2 .pp{font-size:20px;font-weight:850;margin-top:3px;letter-spacing:-.02em}.planopt2 .pp span{font-size:12.5px;color:var(--muted);font-weight:600}
.fld{width:100%;font-size:15px;border:1.5px solid var(--line);border-radius:12px;padding:12px 13px;margin-bottom:10px;font-weight:550}.fld:focus{outline:none;border-color:var(--brand-2);box-shadow:0 0 0 3px var(--brand-soft)}
@media(max-width:560px){.flow-hero h1{font-size:34px}.trow{grid-template-columns:1fr!important}}
</style>
<div class="flow-bg">
  <div class="flow-top">
    <a class="logo" href="/"><span class="mark">D</span> Draftease</a>
    <div class="rt">__NAVRIGHT__</div>
  </div>
  <div class="flow-inner">
    <div class="flow-hero">
      <h1>Create a <span class="grad">redline</span></h1>
      <p>Pick a template, drop in the LOI, get a tracked-changes draft.</p>
    </div>
    <div id="steps"></div>
  </div>
</div>
<input type="file" id="tplFileIn" accept=".docx" style="display:none" onchange="onTplFile()">
<input type="file" id="loiFileIn" accept=".pdf,.docx" style="display:none" onchange="onLoiFile()">
<div id="toast" class="toast"></div>
<script>
let CSRF="__CSRF__"; const LOGGED_IN=__LOGGED_IN__; const AI_ON=__AI__;
let STATE={templates:[],lois:[]};
let baseMode='', baseTid=null, leaseFile=null, terms=[], loiMode='', loiId=null, loiFile=null, plan='payg', openMenu='';
let reg={name:'',email:'',pass:''};
function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function prettify(t){return String(t).replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());}
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');clearTimeout(window._tt);window._tt=setTimeout(()=>t.classList.remove('show'),2800);}
function download(blob,nm){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=nm;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);}
async function loadStart(){try{const[t,l]=await Promise.all([fetch('/api/templates').then(r=>r.ok?r.json():[]),fetch('/api/lois').then(r=>r.ok?r.json():[])]);STATE.templates=Array.isArray(t)?t:[];STATE.lois=Array.isArray(l)?l:[];}catch(e){}render();}
function setMenu(id){openMenu=(openMenu===id?'':id);render();}
function closeMenu(){openMenu='';render();}

/* ---------- step 1: base template ---------- */
function baseLabel(){if(baseMode==='template'){const t=STATE.templates.find(x=>x.id===baseTid);return t?esc(t.name):'Choose a template';}if(baseMode==='upload'){return leaseFile?esc(leaseFile.name):'New template';}return '';}
function baseDone(){return (baseMode==='template'&&baseTid)||(baseMode==='upload'&&leaseFile&&terms.length);}
function tplMenu(){
  let items='<div class="ddopt add" onclick="addTemplate()"><span class="ic">＋</span> Add new template</div>';
  if(STATE.templates.length) items+='<div class="ddsep"></div>';
  items+=STATE.templates.map(t=>`<div class="ddopt" onclick="chooseTpl(${t.id})"><span class="ic">📄</span><span>${esc(t.name)}</span><span class="meta">${t.tokens.length} fields</span></div>`).join('');
  return `<div class="ddback" onclick="closeMenu()"></div><div class="ddmenu">${items}</div>`;}
function addTemplate(){openMenu='';document.getElementById('tplFileIn').value='';document.getElementById('tplFileIn').click();}
function chooseTpl(id){baseMode='template';baseTid=id;leaseFile=null;openMenu='';const t=STATE.templates.find(x=>x.id===id);terms=((t&&t.tokens)||[]).map(tok=>({token:tok,label:prettify(tok),value:''}));render();}
async function onTplFile(){const f=document.getElementById('tplFileIn').files[0];if(!f)return;baseMode='upload';baseTid=null;leaseFile=f;terms=[];render();
  const fd=new FormData();fd.append('file',f);
  try{const r=await fetch('/api/scan',{method:'POST',body:fd});if(!r.ok){toast('Could not read that .docx: '+(await r.text()));return;}const j=await r.json();
    terms=(j.standard||[]).map(s=>({key:s.key,label:s.label,current:((j.suggestions||{})[s.key]||''),nw:''}));toast('Template added — '+terms.length+' standard terms detected.');
  }catch(e){toast('Could not read that file.');}render();}

/* ---------- step 2: LOI / term sheet ---------- */
function loiLabel(){if(loiMode==='loi'){const l=STATE.lois.find(x=>x.id===loiId);return l?esc(l.name):'Saved LOI';}if(loiMode==='upload')return loiFile?esc(loiFile.name):'New LOI';if(loiMode==='manual')return 'Enter terms manually';return '';}
function loiMenu(){
  let items='<div class="ddopt add" onclick="addLoi()"><span class="ic">＋</span> Add new LOI / term sheet</div>';
  items+='<div class="ddopt" onclick="chooseManual()"><span class="ic">✎</span> No LOI — enter terms manually</div>';
  if(STATE.lois.length) items+='<div class="ddsep"></div>'+STATE.lois.map(l=>`<div class="ddopt" onclick="chooseLoi(${l.id})"><span class="ic">📑</span><span>${esc(l.name)}</span></div>`).join('');
  return `<div class="ddback" onclick="closeMenu()"></div><div class="ddmenu">${items}</div>`;}
function addLoi(){openMenu='';document.getElementById('loiFileIn').value='';document.getElementById('loiFileIn').click();}
function chooseManual(){loiMode='manual';loiId=null;loiFile=null;openMenu='';render();}
function chooseLoi(id){loiMode='loi';loiId=id;loiFile=null;openMenu='';render();if(AI_ON)extractLoi();}
function onLoiFile(){const f=document.getElementById('loiFileIn').files[0];if(!f)return;loiMode='upload';loiFile=f;render();if(AI_ON)extractLoi();else toast('LOI attached — enter the new terms below.');}
function applyExtracted(d){if(!d)return 0;let hit=0;terms.forEach(t=>{const k=t.token||t.key;const v=(d[k]||'').trim();if(!v)return;hit++;if(baseMode==='template'){t.value=v;}else{t.nw=v;}});return hit;}
async function extractLoi(){const fd=new FormData();if(loiMode==='upload'&&loiFile){fd.append('file',loiFile);}else if(loiMode==='loi'&&loiId){fd.append('loi_id',loiId);}else{return;}fd.append('csrf',CSRF);
  toast('Reading the LOI…');
  try{const r=await fetch('/api/extract-loi',{method:'POST',body:fd});if(!r.ok){toast(r.status===503?'AI reading not enabled — enter terms manually.':'Could not read LOI — enter terms manually.');return;}
    const j=await r.json();const hit=applyExtracted(j.terms||{});render();toast(hit?('Pre-filled '+hit+' term'+(hit===1?'':'s')+' — please verify.'):'No matching terms found — enter them manually.');
  }catch(e){toast('Could not read LOI — enter terms manually.');}}
function termsPanel(){
  if(!baseMode||!terms.length) return '';
  let rows;
  if(baseMode==='template'){
    rows=terms.map((t,i)=>`<div class="trow" style="grid-template-columns:1fr 1.5fr"><div class="tl">${esc(t.label)}</div><input data-ti="${i}" data-f="value" value="${esc(t.value)}" placeholder="blank = unchanged" oninput="terms[${i}].value=this.value;render()"></div>`).join('');
  }else{
    rows=`<div class="trow" style="grid-template-columns:1fr 1fr 1fr"><div class="tl" style="font-size:12px;text-transform:uppercase;color:var(--muted)">Term</div><div class="tl" style="font-size:12px;text-transform:uppercase;color:var(--muted)">Current</div><div class="tl" style="font-size:12px;text-transform:uppercase;color:var(--muted)">New</div></div>`+terms.map((t,i)=>`<div class="trow" style="grid-template-columns:1fr 1fr 1fr"><div class="tl">${esc(t.label)}</div><input data-ti="${i}" data-f="current" value="${esc(t.current)}" oninput="terms[${i}].current=this.value;render()"><input data-ti="${i}" data-f="nw" value="${esc(t.nw)}" placeholder="blank = no change" oninput="terms[${i}].nw=this.value;render()"></div>`).join('');
  }
  return `<div class="termwrap"><div class="termhd">Terms to apply${AI_ON?' · auto-filled from the LOI where found':''}</div>${rows}</div>`;}

/* ---------- step 3: create ---------- */
function changeCount(){if(baseMode==='template'){return terms.filter(t=>t.value&&t.value.trim()).length;}return terms.filter(t=>t.nw&&t.nw.trim()&&t.current&&t.current.trim()&&t.nw.trim()!==t.current.trim()).length;}
function dlname(){if(baseMode==='template'){const t=STATE.templates.find(x=>x.id===baseTid);return (((t&&t.name)||'lease').replace(/[^a-z0-9]+/gi,'_'))+'_redline.docx';}return (leaseFile?leaseFile.name.replace(/\\.docx$/i,''):'lease')+'_redline.docx';}
function doGenerate(){if(baseMode==='template'){const tt={};terms.forEach(x=>{if(x.value&&x.value.trim())tt[x.token]=x.value.trim();});const fd=new FormData();fd.append('template_id',baseTid);fd.append('terms',JSON.stringify(tt));fd.append('csrf',CSRF);return fetch('/api/redline-from-template',{method:'POST',body:fd});}
  const ch=terms.filter(x=>x.nw&&x.nw.trim()&&x.current&&x.current.trim()&&x.nw.trim()!==x.current.trim()).map(x=>[x.current.trim(),x.nw.trim()]);const fd=new FormData();fd.append('lease',leaseFile);fd.append('changes',JSON.stringify(ch));fd.append('csrf',CSRF);return fetch('/api/guest-redline',{method:'POST',body:fd});}

/* ---------- render ---------- */
function card(n,cls,title,sub,chip,body){return `<div class="stepcard ${cls}"><div class="shead"><div class="stepnum">${cls.indexOf('done')>=0?'✓':n}</div><div><div class="stitle">${title}</div><div class="ssub">${sub}</div></div>${chip?`<div class="schip">${chip}</div>`:''}</div>${body}</div>`;}
function render(){
  const s=document.getElementById('steps');
  // preserve focus + caret across re-render (term inputs use data-ti/data-f)
  let restore=null;const a=document.activeElement;
  if(a&&a.dataset&&a.dataset.ti!==undefined){restore={ti:a.dataset.ti,f:a.dataset.f,pos:a.selectionStart};}
  // Step 1
  const b1=`<div class="ddwrap"><button class="ddbtn ${baseMode?'set':''}" onclick="setMenu('tpl')"><span class="dlab ${baseMode?'':'dph'}">${baseMode?baseLabel():'Choose a template…'}</span><span class="dchev">▾</span></button>${openMenu==='tpl'?tplMenu():''}</div>`;
  const c1=card(1, baseDone()?'done':'active', 'Base template', 'The form lease to redline from', baseDone()?'Ready':'', b1);
  // Step 2
  const locked2=!baseMode;
  const b2=`<div class="ddwrap"><button class="ddbtn ${loiMode?'set':''}" ${locked2?'disabled style=\\'opacity:.6\\'':''} onclick="${locked2?'':'setMenu(\\'loi\\')'}"><span class="dlab ${loiMode?'':'dph'}">${loiMode?loiLabel():'Choose LOI / term sheet…'}</span><span class="dchev">▾</span></button>${openMenu==='loi'?loiMenu():''}</div>${termsPanel()}`;
  const c2=card(2, locked2?'locked':(changeCount()?'done':'active'), 'LOI / term sheet', 'Terms to apply to the lease', changeCount()?changeCount()+' set':'', b2);
  // Step 3
  const c3=card(3, 'active', 'Create redline', 'Generate the tracked-changes draft', '', step3Body());
  s.innerHTML=c1+c2+c3;
  if(restore){const el=document.querySelector(`[data-ti="${restore.ti}"][data-f="${restore.f}"]`);if(el){el.focus();try{el.setSelectionRange(restore.pos,restore.pos);}catch(e){}}}
}
function noTermsNote(){return `<div class="flownote">No terms entered yet — choose your LOI (or "enter terms manually") above and give at least one term a new value.</div>`;}
function step3Body(){
  const n=changeCount();
  if(LOGGED_IN){
    return `${n?'':noTermsNote()}<button class="bigcreate" ${n?'':'disabled'} onclick="gen(event)">Create redline${n?' — '+n+' change'+(n===1?'':'s'):''} →</button>`;
  }
  const plans=[['payg','Single use','$50','per redline'],['unlimited','Monthly','$199','unlimited']];
  return `${n?'':noTermsNote()}
    <div class="planrow">${plans.map(p=>`<div class="planopt2 ${plan===p[0]?'sel':''}" onclick="plan='${p[0]}';render()">${p[1]}<div class="pp">${p[2]} <span>${p[3]}</span></div></div>`).join('')}</div>
    <input id="gName" class="fld" type="text" placeholder="Your name" autocomplete="name" value="${esc(reg.name)}" oninput="reg.name=this.value">
    <input id="gEmail" class="fld" type="email" placeholder="Work email" autocomplete="email" value="${esc(reg.email)}" oninput="reg.email=this.value">
    <input id="gPass" class="fld" type="password" placeholder="Password (8+ characters)" autocomplete="new-password" value="${esc(reg.pass)}" oninput="reg.pass=this.value">
    <div id="s3err" class="modal-err" style="display:none;margin-bottom:10px"></div>
    <button class="bigcreate" ${n?'':'disabled'} onclick="registerAndGen(event)">Create account &amp; redline →</button>
    <div class="hint" style="text-align:center;margin-top:10px">Already have an account? <a href="/login" style="color:var(--brand);font-weight:700">Sign in</a></div>`;
}
async function gen(e){const btn=e.target.closest('button');if(!changeCount()){toast('Add at least one new term.');return;}const old=btn.innerHTML;btn.innerHTML='Generating…';btn.disabled=true;
  try{const r=await doGenerate();if(!r.ok){const m=await r.text();toast(r.status===402?'Out of credits — choose a plan.':'Error: '+m);btn.innerHTML=old;btn.disabled=false;return;}download(await r.blob(),dlname());btn.innerHTML='✓ Downloaded';toast('Redline generated ✓');}catch(err){toast('Error: '+err);btn.innerHTML=old;btn.disabled=false;}}
async function registerAndGen(e){const btn=e.target.closest('button');const err=document.getElementById('s3err');const show=(m)=>{err.textContent=m;err.style.display='block';btn.innerHTML='Create account &amp; redline →';btn.disabled=false;};
  const name=document.getElementById('gName').value.trim();const email=document.getElementById('gEmail').value.trim();const pass=document.getElementById('gPass').value;
  if(!email||!pass){show('Enter your email and a password.');return;}
  if(baseMode==='template'){show('Saved templates need an account — please sign in, or add a new template file in Step 1.');return;}
  err.style.display='none';btn.innerHTML='Creating account…';btn.disabled=true;
  try{const fd=new FormData();fd.append('name',name);fd.append('email',email);fd.append('password',pass);fd.append('csrf',CSRF);
    const r=await fetch('/signup',{method:'POST',body:fd});
    if(!(r.redirected||r.ok)){show('Could not create the account — the email may already be registered. Try signing in.');return;}
    try{const cr=await fetch('/api/csrf');CSRF=(await cr.json()).csrf;}catch(x){}
    btn.innerHTML='Generating…';const r2=await doGenerate();
    if(!r2.ok){show('Account created, but the redline failed: '+await r2.text());return;}
    download(await r2.blob(),dlname());btn.innerHTML='✓ Done';btn.disabled=false;btn.onclick=function(){location.href='/';};toast('Account created — redline downloaded ✓');
  }catch(err2){show(''+err2);}}
loadStart();
</script>
"""


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/csrf")
def api_csrf(request: Request):
    return JSONResponse({"csrf": csrf_token(request)})


@app.post("/api/scan")
async def api_scan(file: UploadFile = File(...)):
    """Public: detect standard terms / tokens in an uploaded lease. No auth, no storage."""
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Please upload a .docx lease.")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "File too large (15 MB max).")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "lease.docx")
        with open(p, "wb") as fh:
            fh.write(raw)
        try:
            sug = tagger.autodetect(p)
            toks = extract_tokens(p)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Could not read that .docx: {exc}")
    return JSONResponse({"suggestions": sug, "tokens": toks,
                         "standard": [{"key": k, "label": lbl} for k, lbl in tagger.STANDARD_TOKENS]})


@app.post("/api/guest-redline")
async def api_guest_redline(request: Request, lease: UploadFile = File(...),
                            changes: str = Form(...), csrf: str = Form(...)):
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired — reload the page.")
    if BILLING_ENABLED and not auth.has_access(u.id):
        raise HTTPException(402, "Out of credits — choose a plan to keep generating.")
    if not lease.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Please upload a .docx lease.")
    try:
        ch = json.loads(changes)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Bad changes: {exc}")
    pairs = [(c[0], c[1]) for c in ch if isinstance(c, (list, tuple)) and len(c) == 2]
    if not pairs:
        raise HTTPException(400, "No term changes provided.")
    raw = await lease.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "File too large (15 MB max).")
    with tempfile.TemporaryDirectory() as d:
        ip, op = os.path.join(d, "in.docx"), os.path.join(d, "out.docx")
        with open(ip, "wb") as fh:
            fh.write(raw)
        try:
            generate_redline_direct(ip, pairs, op, author="Draftease")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Could not redline: {exc}")
        data = open(op, "rb").read()
    if BILLING_ENABLED:
        auth.consume_credit(u.id)
    nm = os.path.splitext(lease.filename)[0] or "lease"
    auth.create_deal(u.id, "Redline — " + nm[:60], nm[:80], "done")
    fn = (re.sub(r"[^A-Za-z0-9]+", "_", nm).strip("_") or "lease") + "_redline.docx"
    return StreamingResponse(io.BytesIO(data), media_type=DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fn}"'})


def _render_flow(request: Request) -> HTMLResponse:
    u = current_user(request)
    tok = csrf_token(request)
    if u:
        nav = (f'<a href="/app">Dashboard</a>'
               f'<form method="post" action="/logout" style="margin:0;display:inline">'
               f'<input type="hidden" name="csrf" value="{tok}">'
               f'<button type="submit" style="background:none;border:none;cursor:pointer;'
               f'color:var(--ink-2);font-weight:600;font-size:14px;padding:0">Sign out</button></form>')
    else:
        nav = '<a href="/login">Log in</a>'
    body = (START_PAGE
            .replace("__CSRF__", tok)
            .replace("__LOGGED_IN__", "true" if u else "false")
            .replace("__AI__", "true" if ai_extract.ENABLED else "false")
            .replace("__NAVRIGHT__", nav))
    return HTMLResponse(page(body, "Create a redline · Draftease"))


@app.get("/start", response_class=HTMLResponse)
def start(request: Request):
    return _render_flow(request)


@app.get("/legal", response_class=HTMLResponse)
def legal():
    return HTMLResponse(page("""<div class="legal">
      <a class="logo" href="/" style="margin-bottom:24px"><span class="mark">D</span> Draftease</a>
      <h1>Terms, Privacy &amp; Disclaimer</h1>
      <p><a class="back" href="/">← Back to site</a></p>
      <h2>Not legal advice</h2>
      <p>Draftease is a document-drafting tool, not a law firm, and does not provide legal advice. Redlines it generates are automated first drafts intended for review and finalization by a licensed attorney. Using Draftease does not create an attorney-client relationship.</p>
      <h2>Your documents &amp; privacy</h2>
      <p>Your form leases and the redlines generated from them are processed by deterministic software and are not sent to any third-party AI service. We do not use your documents to train any model. You can export or delete your data on request.</p>
      <h2>Acceptable use</h2>
      <p>You are responsible for the content of the documents you upload and for ensuring you have the right to use them. Do not upload material you are not authorized to process.</p>
      <p style="margin-top:30px"><a class="back" href="/">← Back to site</a></p>
    </div>""", "Legal · Draftease"))


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    if user:
        return _render_flow(request)
    return HTMLResponse(page(MARKETING, "Draftease — Lease redlines from your LOIs"))


@app.get("/app", response_class=HTMLResponse)
def dashboard(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(app_shell(request, user))


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
    if not current_user(request):
        raise HTTPException(401, "Please sign in.")
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
    return StreamingResponse(io.BytesIO(data), media_type=DOCX_MIME, headers={
        "Content-Disposition": f'attachment; filename="{out_name}"',
        "X-Draftease-Applied": str(len(report["applied"])),
    })


# --------------------------------------------------------------------------- #
# API: templates & deals (used by the app shell)
# --------------------------------------------------------------------------- #
def _require(request: Request):
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Please sign in.")
    return u


@app.get("/api/templates")
def api_templates(request: Request):
    return JSONResponse(auth.list_templates(_require(request).id))


@app.post("/api/templates")
async def api_template_create(request: Request, name: str = Form(...),
                              kind: str = Form("Office"), csrf: str = Form(...),
                              file: UploadFile = File(...)):
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired — reload the page.")
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Please upload a .docx form lease.")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "File too large (15 MB max).")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.docx")
        with open(p, "wb") as fh:
            fh.write(raw)
        try:
            toks = extract_tokens(p)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Could not read that .docx: {exc}")
    return JSONResponse(auth.create_template(u.id, name, kind, file.filename, raw, toks))


@app.post("/api/templates/delete")
def api_template_delete(request: Request, id: int = Form(...), csrf: str = Form(...)):
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired.")
    auth.delete_template(u.id, id)
    return JSONResponse({"ok": True})


@app.get("/api/templates/{tid}/file")
def api_template_file(request: Request, tid: int):
    u = _require(request)
    blob = auth.get_template_blob(u.id, tid)
    if not blob:
        raise HTTPException(404, "Template not found.")
    name, data, _toks = blob
    fn = (re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "template") + ".docx"
    return StreamingResponse(io.BytesIO(data), media_type=DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@app.post("/api/autotag")
async def api_autotag(request: Request, csrf: str = Form(...), file: UploadFile = File(...)):
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired — reload the page.")
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Please upload a .docx lease.")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "File too large (15 MB max).")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "lease.docx")
        with open(p, "wb") as fh:
            fh.write(raw)
        try:
            suggestions = tagger.autodetect(p)
            existing = extract_tokens(p)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Could not read that .docx: {exc}")
    return JSONResponse({"suggestions": suggestions,
                         "standard": [{"key": k, "label": lbl} for k, lbl in tagger.STANDARD_TOKENS],
                         "existingTokens": existing})


@app.post("/api/templates/tag")
async def api_template_tag(request: Request, name: str = Form(...), kind: str = Form("Office"),
                           mapping: str = Form(...), csrf: str = Form(...),
                           file: UploadFile = File(...)):
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired — reload the page.")
    if not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Please upload a .docx lease.")
    try:
        mp = json.loads(mapping)
    except json.JSONDecodeError:
        raise HTTPException(400, "Bad tag mapping.")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "File too large (15 MB max).")
    with tempfile.TemporaryDirectory() as d:
        ip, op = os.path.join(d, "in.docx"), os.path.join(d, "out.docx")
        with open(ip, "wb") as fh:
            fh.write(raw)
        try:
            n = tagger.apply_tags(ip, mp, op)
            toks = extract_tokens(op)
            data = open(op, "rb").read()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Could not tag that lease: {exc}")
    t = auth.create_template(u.id, name, kind, file.filename, data, toks)
    t["tagged"] = n
    return JSONResponse(t)


@app.get("/api/templates/{tid}/autotag")
def api_template_autotag_existing(request: Request, tid: int):
    """Auto-detect standard terms in an already-saved template (for the Tag-fields button)."""
    u = _require(request)
    blob = auth.get_template_blob(u.id, tid)
    if not blob:
        raise HTTPException(404, "Template not found.")
    _name, data, _toks = blob
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.docx")
        with open(p, "wb") as fh:
            fh.write(data)
        try:
            suggestions = tagger.autodetect(p)
            existing = extract_tokens(p)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Could not read that template: {exc}")
    return JSONResponse({"suggestions": suggestions,
                         "standard": [{"key": k, "label": lbl} for k, lbl in tagger.STANDARD_TOKENS],
                         "existingTokens": existing})


@app.post("/api/templates/{tid}/retag")
async def api_template_retag(request: Request, tid: int, mapping: str = Form(...),
                            csrf: str = Form(...)):
    """Tag an already-saved (untagged) template in place from a confirmed mapping."""
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired — reload the page.")
    blob = auth.get_template_blob(u.id, tid)
    if not blob:
        raise HTTPException(404, "Template not found.")
    name, data, _toks = blob
    try:
        mp = json.loads(mapping)
    except json.JSONDecodeError:
        raise HTTPException(400, "Bad tag mapping.")
    with tempfile.TemporaryDirectory() as d:
        ip, op = os.path.join(d, "in.docx"), os.path.join(d, "out.docx")
        with open(ip, "wb") as fh:
            fh.write(data)
        try:
            n = tagger.apply_tags(ip, mp, op)
            toks = extract_tokens(op)
            newdata = open(op, "rb").read()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Could not tag that lease: {exc}")
    updated = auth.update_template(u.id, tid, newdata, toks)
    if not updated:
        raise HTTPException(404, "Template not found.")
    updated["tagged"] = n
    return JSONResponse(updated)


@app.get("/api/lois")
def api_lois(request: Request):
    return JSONResponse(auth.list_lois(_require(request).id))


@app.post("/api/lois")
async def api_loi_create(request: Request, name: str = Form(""), csrf: str = Form(...),
                         file: UploadFile = File(...)):
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired — reload the page.")
    if not file.filename.lower().endswith((".pdf", ".docx")):
        raise HTTPException(400, "Please upload a PDF or .docx LOI.")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "File too large (15 MB max).")
    return JSONResponse(auth.create_loi(u.id, name or file.filename, file.filename, raw))


@app.post("/api/lois/delete")
def api_loi_delete(request: Request, id: int = Form(...), csrf: str = Form(...)):
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired.")
    auth.delete_loi(u.id, id)
    return JSONResponse({"ok": True})


@app.post("/api/extract-loi")
async def api_extract_loi(request: Request, file: UploadFile = File(None),
                          loi_id: str = Form(""), csrf: str = Form("")):
    """Read an LOI with Claude on Bedrock and return the standard terms it states."""
    if not ai_extract.ENABLED:
        raise HTTPException(503, "AI LOI reading isn't set up yet.")
    fb = fn = None
    if file is not None and file.filename:
        fn = file.filename
        fb = await file.read()
    elif loi_id:
        u = current_user(request)
        if not u:
            raise HTTPException(401, "Sign in to use a saved LOI.")
        blob = auth.get_loi_blob(u.id, int(loi_id))
        if not blob:
            raise HTTPException(404, "LOI not found.")
        _name, fn, fb = blob
    if not fb:
        raise HTTPException(400, "No LOI provided.")
    if len(fb) > MAX_BYTES:
        raise HTTPException(413, "File too large (15 MB max).")
    try:
        terms = ai_extract.extract_terms(fb, fn)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"AI extraction failed: {exc}")
    return JSONResponse({"terms": terms})


@app.get("/api/deals")
def api_deals(request: Request):
    return JSONResponse(auth.list_deals(_require(request).id))


@app.post("/api/deals/delete")
def api_deal_delete(request: Request, id: int = Form(...), csrf: str = Form(...)):
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired.")
    auth.delete_deal(u.id, id)
    return JSONResponse({"ok": True})


@app.post("/api/redline-from-template")
def api_redline_from_template(request: Request, template_id: int = Form(...),
                              terms: str = Form(...), csrf: str = Form(...)):
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired — reload the page.")
    if BILLING_ENABLED and not auth.has_access(u.id):
        raise HTTPException(402, "You're out of redline credits. Choose a plan to keep generating.")
    blob = auth.get_template_blob(u.id, template_id)
    if not blob:
        raise HTTPException(404, "Template not found.")
    tpl_name, data, _toks = blob
    try:
        terms_dict = json.loads(terms)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Bad terms: {exc}")
    with tempfile.TemporaryDirectory() as d:
        ip, op = os.path.join(d, "in.docx"), os.path.join(d, "out.docx")
        with open(ip, "wb") as fh:
            fh.write(data)
        try:
            generate_redline(ip, terms_dict, op, author="Draftease")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Could not process the lease: {exc}")
        out = open(op, "rb").read()
    deal_name = (terms_dict.get("tenant") or "").strip() or "New redline"
    auth.create_deal(u.id, deal_name, tpl_name, "done")
    if BILLING_ENABLED:
        auth.consume_credit(u.id)
    fn = re.sub(r"[^A-Za-z0-9]+", "_", tpl_name).strip("_") + "_redline.docx"
    return StreamingResponse(io.BytesIO(out), media_type=DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fn}"'})


# --------------------------------------------------------------------------- #
# Stripe billing
# --------------------------------------------------------------------------- #
@app.get("/api/billing")
def api_billing(request: Request):
    u = _require(request)
    b = auth.billing_dict(u.id)
    b["enabled"] = BILLING_ENABLED
    return JSONResponse(b)


@app.post("/api/checkout")
def api_checkout(request: Request, plan: str = Form(...), csrf: str = Form(...)):
    u = _require(request)
    if not check_csrf(request, csrf):
        raise HTTPException(400, "Session expired — reload the page.")
    if not BILLING_ENABLED:
        raise HTTPException(503, "Online checkout isn't set up yet.")
    cfg = PLANS.get(plan)
    if not cfg:
        raise HTTPException(400, "Unknown plan.")
    base = str(request.base_url).rstrip("/")
    price = {"currency": "usd", "unit_amount": cfg["amount"],
             "product_data": {"name": cfg["name"]}}
    if cfg["mode"] == "subscription":
        price["recurring"] = {"interval": "month"}
    try:
        session = stripe.checkout.Session.create(
            mode=cfg["mode"],
            line_items=[{"price_data": price, "quantity": 1}],
            success_url=base + "/billing/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=base + "/billing/cancel",
            client_reference_id=str(u.id),
            customer_email=u.email,
            metadata={"user_id": str(u.id), "plan": plan},
            subscription_data=({"metadata": {"user_id": str(u.id), "plan": plan}}
                               if cfg["mode"] == "subscription" else None),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Stripe error: {exc}")
    return JSONResponse({"url": session.url})


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not BILLING_ENABLED:
        return JSONResponse({"ok": True})
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Webhook signature error: {exc}")
    etype = event["type"]
    obj = event["data"]["object"]
    if etype == "checkout.session.completed":
        meta = obj.get("metadata") or {}
        try:
            uid = int(meta.get("user_id") or obj.get("client_reference_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        plan = meta.get("plan", "")
        customer = obj.get("customer", "") or ""
        if uid and plan in ("single", "payg"):
            auth.add_credits(uid, PLANS[plan]["credits"], customer)
        elif uid and plan == "unlimited":
            auth.set_unlimited(uid, obj.get("subscription", "") or "", customer)
    elif etype == "customer.subscription.deleted":
        auth.cancel_unlimited_by_sub(obj.get("id", "") or "")
    return JSONResponse({"received": True})


@app.get("/billing/success")
def billing_success():
    return RedirectResponse("/?billing=success", status_code=303)


@app.get("/billing/cancel")
def billing_cancel():
    return RedirectResponse("/?billing=cancel", status_code=303)
