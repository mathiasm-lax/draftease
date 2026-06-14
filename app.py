"""
Draftease — web app: full marketing site + app shell (ported from the mockup),
with professional auth (Postgres + bcrypt + CSRF) and a wizard wired to the real
deterministic redline engine.
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
  <div class="nav-right"><a class="btn btn-ghost" href="/login">Log in</a><a class="btn btn-primary" href="/signup">Start free trial</a></div>
</div></nav>
<div class="mkt"><section class="hero">
  <div class="pill"><span class="dot"></span> Built for landlords &amp; their brokers · Your docs stay in your cloud</div>
  <h1 class="hero-h">Drop in a signed LOI. Get back a <span class="grad">lease redline</span>.</h1>
  <p class="hero-sub">Draftease applies the terms from a signed letter of intent to your property's own form lease and returns a clean, tracked-changes first draft — ready for your attorney. No setup project, no platform to learn.</p>
  <div class="hero-cta"><a class="btn btn-primary btn-lg" href="/signup">Start free trial</a><a class="btn btn-outline btn-lg" href="/signup">Watch the 60-second demo →</a></div>
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
  <div class="sec-head"><div class="sec-tag">Pricing</div><h2>Simple monthly plans</h2><p>Save the first-draft hours. Start free, upgrade when you're ready.</p></div>
  <div class="pricing">
    <div class="price"><h3>Starter</h3><div class="desc">For a single owner or small brokerage getting started.</div><div class="amt">$49<span>/mo</span></div>
      <ul><li>1 property template</li><li>Up to 10 redlines / mo</li><li>2 seats</li><li>Email support</li></ul><a class="btn btn-outline btn-block" href="/signup">Start free</a></div>
    <div class="price pop"><div class="badge">Most popular</div><h3>Professional</h3><div class="desc">For active owners managing multiple buildings.</div><div class="amt">$199<span>/mo</span></div>
      <ul><li>Up to 15 property templates</li><li>Unlimited redlines</li><li>10 seats + outside-broker access</li><li>Audit log &amp; exports</li><li>Priority support</li></ul><a class="btn btn-primary btn-block" href="/signup">Start free trial</a></div>
    <div class="price"><h3>Enterprise</h3><div class="desc">For portfolios with security &amp; isolation needs.</div><div class="amt">Custom</div>
      <ul><li>Unlimited templates &amp; seats</li><li>Private cloud / dedicated isolation</li><li>SSO &amp; SOC 2 reporting</li><li>Custom DPA</li><li>Dedicated success manager</li></ul><a class="btn btn-outline btn-block" href="/signup">Contact sales</a></div>
  </div>
</div></section>
<div class="cta-band"><h2>Stop paying for blank-page drafting.</h2><p>Generate your first lease redline today.</p><a class="btn btn-primary btn-lg" href="/signup">Start your free trial</a></div>
<footer>
  <div class="foot-in">
    <div><div class="logo"><span class="mark">D</span> Draftease</div><p>Lease redlines from your LOIs — securely, in your own cloud.</p></div>
    <div class="foot-cols">
      <div class="foot-col"><h5>Product</h5><a href="#how">How it works</a><a href="#why">Why Draftease</a><a href="#security">Security</a><a href="#pricing">Pricing</a></div>
      <div class="foot-col"><h5>Company</h5><a href="/signup">Get started</a><a href="/login">Log in</a></div>
      <div class="foot-col"><h5>Legal</h5><a href="#">Terms</a><a href="#">Privacy</a><a href="#">DPA</a></div>
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
    <div class="logo"><span class="mark">D</span> Draftease</div>
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
      <div class="topbar-right"><div class="search">🔎 <input placeholder="Search deals, properties…"></div>
        <button class="btn btn-primary" onclick="go('wizard')">+ New redline</button></div>
    </div>
    <div class="content" id="appContent"></div>
  </div>
</div>
<script>
const CSRF="__CSRF__";
const FULLTERMS=__FULLTERMS__;
const PROPERTIES=[
  {id:1,name:"350 Park Avenue",addr:"Suite 1200 · Office",type:"O",redlines:24},
  {id:2,name:"One Harbor Point",addr:"Retail pad · Stamford, CT",type:"R",redlines:11},
  {id:3,name:"Gateway Logistics Ctr",addr:"Building C · Industrial",type:"I",redlines:7},
  {id:4,name:"The Whitman",addr:"Ground-floor retail · NYC",type:"R",redlines:5},
  {id:5,name:"Tribeca Commons",addr:"Floors 3-5 · Office",type:"O",redlines:3}];
const REDLINES=[
  {deal:"Lockton Advisors",prop:"350 Park Avenue",date:"Jun 9, 2026",by:"A. Reyes",status:"done"},
  {deal:"Blue Bottle Coffee",prop:"One Harbor Point",date:"Jun 8, 2026",by:"J. Okafor",status:"review"},
  {deal:"Vanta Logistics",prop:"Gateway Logistics Ctr",date:"Jun 6, 2026",by:"A. Reyes",status:"done"},
  {deal:"Sweetgreen",prop:"The Whitman",date:"Jun 3, 2026",by:"Outside broker",status:"draft"},
  {deal:"Held & Pierce LLP",prop:"Tribeca Commons",date:"May 28, 2026",by:"You",status:"done"}];
const TERMS=[
  {lbl:"Base rent / RSF",token:"base_rent_psf",val:"$72.50",conf:"hi"},
  {lbl:"Rentable square feet",token:"rentable_sf",val:"18,400",conf:"hi"},
  {lbl:"Lease term (months)",token:"term_months",val:"87",conf:"hi"},
  {lbl:"Commencement date",token:"commencement_date",val:"September 1, 2026",conf:"med"},
  {lbl:"Free rent",token:"free_rent_months",val:"four (4)",conf:"hi"},
  {lbl:"TI allowance / RSF",token:"ti_allowance_psf",val:"$95.00",conf:"hi"},
  {lbl:"Security deposit",token:"security_deposit",val:"$220,000.00",conf:"med"},
  {lbl:"Renewal option",token:"renewal_option",val:"one (1) option to renew for five (5) years at fair market value",conf:"med"}];
let wizardStep=1,selProp=null,leaseFile=null,editedTerms={};
const TITLES={dashboard:["Dashboard","Welcome back - here's your deal activity."],redlines:["Redlines","Every draft generated across your properties."],templates:["Templates","Your form lease for each property."],settings:["Settings & billing","Manage your plan, seats and security."],wizard:["New redline","Turn a signed LOI into a tracked-changes draft."]};
function go(v){
  document.querySelectorAll('.side-link[data-nav]').forEach(l=>l.classList.toggle('active',l.dataset.nav===v));
  const t=TITLES[v]||TITLES.dashboard;document.getElementById('pageTitle').textContent=t[0];document.getElementById('pageSub').textContent=t[1];
  if(v==='wizard'){wizardStep=1;selProp=null;leaseFile=null;editedTerms={};}
  document.getElementById('appContent').innerHTML=({dashboard:vDashboard,redlines:vRedlines,templates:vTemplates,settings:vSettings,wizard:vWizard}[v]||vDashboard)();
  document.querySelector('.main').scrollTo(0,0);
}
function statusTag(s){const m={done:["done","Ready"],review:["review","In review"],draft:["draft","Draft"]};return `<span class="tag ${m[s][0]}"><span class="d"></span>${m[s][1]}</span>`;}
function vDashboard(){return `
  <div class="cards">
    <div class="stat"><div class="lbl">Redlines this month</div><div class="val">38</div><div class="delta up">▲ 24% vs. May</div></div>
    <div class="stat"><div class="lbl">Active templates</div><div class="val">5</div><div class="delta flat">across 5 properties</div></div>
    <div class="stat"><div class="lbl">Avg. draft time</div><div class="val">3m</div><div class="delta up">▼ from ~3 hrs</div></div>
    <div class="stat"><div class="lbl">Est. counsel hrs saved</div><div class="val">112</div><div class="delta up">▲ this quarter</div></div></div>
  <div class="panel"><div class="panel-head"><h3>Recent redlines</h3><button class="btn btn-outline btn-sm" onclick="go('redlines')">View all</button></div>
    <div class="panel-body"><table><thead><tr><th>Deal</th><th>Property</th><th>Created</th><th>By</th><th>Status</th></tr></thead><tbody>
    ${REDLINES.map(r=>`<tr class="row" onclick="go('wizard')"><td style="font-weight:600">${r.deal}</td><td class="muted">${r.prop}</td><td class="muted">${r.date}</td><td class="muted">${r.by}</td><td>${statusTag(r.status)}</td></tr>`).join('')}
    </tbody></table></div></div>
  <div class="panel"><div class="panel-head"><h3>Start a new redline</h3></div>
    <div class="panel-body" style="padding:20px"><div style="display:flex;gap:14px;align-items:center;color:var(--muted);font-size:14px">Pick a property, upload the form lease, confirm the terms, and download the tracked-changes draft.<button class="btn btn-primary" style="margin-left:auto" onclick="go('wizard')">+ New redline</button></div></div></div>`;}
function vRedlines(){return `<div class="panel"><div class="panel-head"><h3>All redlines</h3><button class="btn btn-primary btn-sm" onclick="go('wizard')">+ New redline</button></div>
  <div class="panel-body"><table><thead><tr><th>Deal</th><th>Property</th><th>Created</th><th>By</th><th>Status</th></tr></thead><tbody>
  ${REDLINES.concat(REDLINES.slice(0,2)).map(r=>`<tr class="row" onclick="go('wizard')"><td style="font-weight:600">${r.deal}</td><td class="muted">${r.prop}</td><td class="muted">${r.date}</td><td class="muted">${r.by}</td><td>${statusTag(r.status)}</td></tr>`).join('')}
  </tbody></table></div></div>`;}
function vTemplates(){const tn={O:"Office",R:"Retail",I:"Industrial"};return `<div class="tgrid">
  ${PROPERTIES.map(p=>`<div class="tcard" onclick="go('wizard')"><div class="top"><div class="ficon">${p.type}</div><div><h4>${p.name}</h4><div class="addr">${p.addr}</div></div></div>
    <div class="meta"><span>${p.redlines} redlines · ${tn[p.type]}</span><span class="tag done"><span class="d"></span>Tagged</span></div></div>`).join('')}
  <div class="tcard add" onclick="go('wizard')"><div class="plus">+</div><b>Add a property template</b><span>Upload a form lease &amp; tag its terms</span></div></div>`;}
function vSettings(){return `
  <div class="plan-box"><div><div class="pname">Free trial</div><div class="ppr">Upgrade anytime · no card on file</div></div><button class="btn">Manage plan</button></div>
  <div class="panel" style="margin-top:22px"><div class="panel-head"><h3>Usage this period</h3></div>
    <div class="panel-body" style="padding:20px 22px">
      <div style="margin-bottom:18px"><div class="k" style="font-weight:600;font-size:14px">Seats <small style="color:var(--muted);font-weight:400">10 included</small></div><div class="bar-track"><div class="bar-fill" style="width:10%"></div></div><div class="muted" style="font-size:12.5px;margin-top:6px">1 of 10 used</div></div>
      <div><div class="k" style="font-weight:600;font-size:14px">Property templates <small style="color:var(--muted);font-weight:400">15 included</small></div><div class="bar-track"><div class="bar-fill" style="width:33%"></div></div><div class="muted" style="font-size:12.5px;margin-top:6px">5 of 15 used</div></div>
    </div></div>
  <div class="panel"><div class="panel-head"><h3>Security</h3></div><div class="panel-body" style="padding:6px 22px 14px">
    <div class="set-row"><div class="k">Data region <small>Where your documents are stored</small></div><span class="muted">Your cloud tenant</span></div>
    <div class="set-row"><div class="k">Redline engine <small>Deterministic — no AI on your documents</small></div><span class="tag done"><span class="d"></span>Enabled</span></div>
    <div class="set-row"><div class="k">Single sign-on (SSO) <small>SAML / Okta</small></div><button class="btn btn-outline btn-sm">Configure</button></div>
    <div class="set-row"><div class="k">Audit log export <small>Every upload &amp; redline</small></div><button class="btn btn-outline btn-sm">Download CSV</button></div>
  </div></div>`;}
function vWizard(){const bar=`<div class="steps-bar">${[["1","Property"],["2","Form lease"],["3","Confirm terms"],["4","Redline"]].map((s,i)=>{const n=i+1;const cls=n<wizardStep?'done':(n===wizardStep?'active':'');const line=i<3?`<div class="sline ${n<wizardStep?'fill':''}"></div>`:'';return `<div class="sbubble ${cls}"><div class="num">${n<wizardStep?'✓':n}</div><div class="stxt">${s[1]}</div></div>${line}`;}).join('')}</div>`;
  return `<div class="wizard">${bar}<div id="wstep">${wStepBody()}</div></div>`;}
function wStepBody(){
  if(wizardStep===1)return `<div class="wbox"><h2>Which property is this deal for?</h2><p class="wsub">We'll use that property's form lease as the starting document.</p>
    <div class="choose">${PROPERTIES.map(p=>`<div class="choice ${selProp===p.id?'sel':''}" onclick="pickProp(${p.id})"><div class="ficon">${p.type}</div><div><h4>${p.name}</h4><div class="addr">${p.addr}</div></div><div class="rd"></div></div>`).join('')}</div>
    <div class="wfoot"><button class="btn btn-ghost" onclick="go('dashboard')">Cancel</button><button class="btn btn-primary" ${selProp?'':'disabled style="opacity:.5;pointer-events:none"'} onclick="wNext()">Continue →</button></div></div>`;
  if(wizardStep===2)return `<div class="wbox"><h2>Upload your form lease</h2><p class="wsub">Upload the property's form lease (a .docx containing {{tokens}}). It stays in your cloud.</p>
    <input type="file" id="leaseInput" accept=".docx" style="display:none" onchange="onLease(this)">
    <div class="drop" onclick="document.getElementById('leaseInput').click()"><div class="dic">⬆️</div><h4>Drop your form lease here</h4><p>or click to browse · .docx</p></div>
    ${leaseFile?`<div class="uploaded"><div class="fi">DOCX</div><div><div class="nm">${leaseFile.name}</div><div class="mt">${Math.max(1,Math.round(leaseFile.size/1024))} KB · ready</div></div><div class="ok">✓ Ready</div></div>`:''}
    <div style="font-size:12.5px;color:var(--muted);margin-top:13px">No file handy? <a style="color:var(--brand);font-weight:600" href="#" onclick="useSampleLease(event)">Use the sample form lease</a> &nbsp;·&nbsp; <a style="color:var(--brand);font-weight:600" href="/sample-lease">download sample lease</a> &nbsp;·&nbsp; <a style="color:var(--brand);font-weight:600" href="/sample-loi">sample LOI (PDF)</a></div>
    <div class="wfoot"><button class="btn btn-ghost" onclick="wBack()">← Back</button><button class="btn btn-primary" ${leaseFile?'':'disabled style="opacity:.5;pointer-events:none"'} onclick="wNext()">Confirm terms →</button></div></div>`;
  if(wizardStep===3)return `<div class="wbox"><h2>Confirm the deal terms</h2><p class="wsub">These would be read from the signed LOI. Review and edit before drafting — nothing is auto-filed.</p>
    <div class="extract-note">🔒 <div>The redline is generated by deterministic software in your cloud — your lease is never sent to an external AI.</div></div>
    <div class="terms-table"><div class="tr"><div class="lbl">Term</div><div class="lbl">Value (editable)</div><div class="lbl" style="text-align:center">Confidence</div></div>
      ${TERMS.map(t=>`<div class="tr"><div class="lbl">${t.lbl}</div><input data-token="${t.token}" value="${t.val}"><div class="conf ${t.conf}">${t.conf==='hi'?'High':'Review'}</div></div>`).join('')}</div>
    <div class="wfoot"><button class="btn btn-ghost" onclick="wBack()">← Back</button><button class="btn btn-primary" onclick="wNext()">Generate redline →</button></div></div>`;
  const p=PROPERTIES.find(x=>x.id===selProp)||PROPERTIES[0];const g=(k,d)=>editedTerms[k]||d;
  return `<div class="wbox"><h2>Redline ready</h2><p class="wsub">${p.name} form lease, modified to the deal terms. Download the tracked-changes Word document below.</p>
    <div class="warn-band">⚠️ <div>This is an automated <b>first draft</b>. Have a licensed attorney review and finalize before execution.</div></div>
    <div class="redline-doc">
      <h4>Article 1 — Premises &amp; Rent</h4>
      1.1  The Premises consist of <span class="del">[NN]</span><span class="ins">${g('rentable_sf','18,400')}</span> rentable square feet.<br>
      1.2  Base Rent shall be <span class="del">$00.00</span><span class="ins">${g('base_rent_psf','$72.50')}</span> per rentable square foot per annum.<br>
      1.3  The Term shall be <span class="del">[NN] months</span><span class="ins">${g('term_months','87')} months</span>, commencing <span class="del">[DATE]</span><span class="ins">${g('commencement_date','September 1, 2026')}</span>.
      <h4>Article 2 — Concessions</h4>
      2.1  Tenant shall receive <span class="del">[NN]</span><span class="ins">${g('free_rent_months','four (4)')}</span> months of abated Base Rent.<br>
      2.2  Landlord shall provide an improvement allowance of <span class="del">$00.00</span><span class="ins">${g('ti_allowance_psf','$95.00')}</span> per RSF.
      <h4>Article 3 — Security &amp; Options</h4>
      3.1  Tenant shall deposit <span class="del">$00,000</span><span class="ins">${g('security_deposit','$220,000.00')}</span> as security.<br>
      3.2  Tenant shall have <span class="del">[none]</span><span class="ins">${g('renewal_option','one (1) option to renew for five (5) years at fair market value')}</span>.
    </div>
    <div class="wfoot"><button class="btn btn-ghost" onclick="wBack()">← Back</button>
      <button class="btn btn-primary" onclick="genDownload(event)">⬇ Download .docx redline</button></div></div>`;
}
function pickProp(id){selProp=id;document.getElementById('wstep').innerHTML=wStepBody();}
function onLease(inp){leaseFile=inp.files[0]||null;document.getElementById('wstep').innerHTML=wStepBody();}
async function useSampleLease(e){e.preventDefault();try{const r=await fetch('/sample-lease');const b=await r.blob();leaseFile=new File([b],'sample_form_lease.docx',{type:b.type});document.getElementById('wstep').innerHTML=wStepBody();}catch(err){alert('Could not load sample: '+err);}}
function captureTerms(){document.querySelectorAll('#wstep [data-token]').forEach(i=>{editedTerms[i.dataset.token]=i.value;});}
function renderWizard(){document.getElementById('appContent').innerHTML=vWizard();}
function wNext(){if(wizardStep===3)captureTerms();wizardStep=Math.min(4,wizardStep+1);renderWizard();}
function wBack(){wizardStep=Math.max(1,wizardStep-1);renderWizard();}
async function genDownload(e){const btn=e.target;if(!leaseFile){alert('Please upload a form lease first (step 2).');return;}
  const old=btn.textContent;btn.textContent='Generating…';btn.style.opacity='.7';
  try{const fd=new FormData();fd.append('lease',leaseFile);fd.append('terms',JSON.stringify(Object.assign({},FULLTERMS,editedTerms)));fd.append('csrf',CSRF);
    const r=await fetch('/redline',{method:'POST',body:fd});
    if(!r.ok){const t=await r.text();alert('Error generating redline: '+t);btn.textContent=old;btn.style.opacity='1';return;}
    const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');
    const base=leaseFile.name.endsWith('.docx')?leaseFile.name.slice(0,-5):leaseFile.name;
    a.href=url;a.download=base+'_redline.docx';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
    btn.textContent='✓ Downloaded';btn.style.opacity='1';
  }catch(err){alert('Error: '+err);btn.textContent=old;btn.style.opacity='1';}
}
go('dashboard');
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
# routes
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    return HTMLResponse(app_shell(request, user) if user
                        else page(MARKETING, "Draftease — Lease redlines from your LOIs"))


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
