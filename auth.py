"""
Draftease — authentication layer.

A small, production-shaped auth core:
  * Users persisted in SQLite via SQLAlchemy (swap the URL for Postgres in prod).
  * Passwords hashed with bcrypt (never stored in plaintext).
  * Helpers the web app uses to create users, verify credentials, and look them up.

This is intentionally storage-agnostic at the call sites so you can later move to a
managed Postgres and add multi-tenant columns without touching the web layer.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import os
import re

import bcrypt
from sqlalchemy import (Boolean, DateTime, Integer, LargeBinary, String, Text,
                        create_engine, delete, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

def _normalize_db_url(url: str) -> str:
    """Render/Heroku hand out 'postgres://' or 'postgresql://'; SQLAlchemy with the
    psycopg 3 driver wants the 'postgresql+psycopg://' prefix. Leave sqlite alone."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DB_URL = _normalize_db_url(os.environ.get("DRAFTEASE_DB_URL", "sqlite:///draftease.db"))
# pool_pre_ping keeps hosted Postgres connections healthy across idle periods.
_engine = create_engine(DB_URL, echo=False, future=True, pool_pre_ping=True)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PW_MIN = 8
PW_MAX = 128


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, default=lambda: _dt.datetime.now(_dt.timezone.utc)
    )


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40), default="Office")
    filename: Mapped[str] = mapped_column(String(260))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    tokens: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, default=lambda: _dt.datetime.now(_dt.timezone.utc))


class Deal(Base):
    __tablename__ = "deals"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(200))
    prop: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="done")
    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime, default=lambda: _dt.datetime.now(_dt.timezone.utc))


TRIAL_CREDITS = 3


class Billing(Base):
    __tablename__ = "billing"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan: Mapped[str] = mapped_column(String(20), default="trial")
    credits: Mapped[int] = mapped_column(Integer, default=TRIAL_CREDITS)
    stripe_customer_id: Mapped[str] = mapped_column(String(80), default="")
    stripe_subscription_id: Mapped[str] = mapped_column(String(80), default="")


def init_db() -> None:
    Base.metadata.create_all(_engine)


# --------------------------------------------------------------------------- #
# billing
# --------------------------------------------------------------------------- #
def _get_or_create_billing(s, user_id: int) -> "Billing":
    b = s.get(Billing, user_id)
    if not b:
        b = Billing(user_id=user_id)
        s.add(b)
        s.commit()
        s.refresh(b)
    return b


def billing_dict(user_id: int) -> dict:
    with Session(_engine) as s:
        b = _get_or_create_billing(s, user_id)
        return {"plan": b.plan, "credits": b.credits}


def has_access(user_id: int) -> bool:
    with Session(_engine) as s:
        b = _get_or_create_billing(s, user_id)
        return b.plan == "unlimited" or b.credits > 0


def consume_credit(user_id: int) -> None:
    with Session(_engine) as s:
        b = _get_or_create_billing(s, user_id)
        if b.plan != "unlimited" and b.credits > 0:
            b.credits -= 1
            s.commit()


def add_credits(user_id: int, n: int, customer_id: str = "") -> None:
    with Session(_engine) as s:
        b = _get_or_create_billing(s, user_id)
        b.credits += int(n)
        if customer_id:
            b.stripe_customer_id = customer_id
        if b.plan == "trial":
            b.plan = "payg"
        s.commit()


def set_unlimited(user_id: int, subscription_id: str = "", customer_id: str = "") -> None:
    with Session(_engine) as s:
        b = _get_or_create_billing(s, user_id)
        b.plan = "unlimited"
        if subscription_id:
            b.stripe_subscription_id = subscription_id
        if customer_id:
            b.stripe_customer_id = customer_id
        s.commit()


def cancel_unlimited_by_sub(subscription_id: str) -> None:
    if not subscription_id:
        return
    with Session(_engine) as s:
        b = s.scalar(select(Billing).where(Billing.stripe_subscription_id == subscription_id))
        if b:
            b.plan = "payg" if b.credits > 0 else "trial"
            b.stripe_subscription_id = ""
            s.commit()


# --------------------------------------------------------------------------- #
# templates
# --------------------------------------------------------------------------- #
def _tpl_dict(t: "Template") -> dict:
    return {"id": t.id, "name": t.name, "kind": t.kind, "filename": t.filename,
            "tokens": _json.loads(t.tokens or "[]"),
            "created": t.created_at.strftime("%b %d, %Y") if t.created_at else ""}


def create_template(user_id: int, name: str, kind: str, filename: str,
                    data: bytes, tokens: list) -> dict:
    with Session(_engine) as s:
        t = Template(user_id=user_id, name=name.strip() or "Untitled", kind=kind or "Office",
                     filename=filename, data=data, tokens=_json.dumps(tokens))
        s.add(t); s.commit(); s.refresh(t)
        return _tpl_dict(t)


def list_templates(user_id: int) -> list:
    with Session(_engine) as s:
        rows = s.scalars(select(Template).where(Template.user_id == user_id)
                         .order_by(Template.created_at.desc())).all()
        return [_tpl_dict(t) for t in rows]


def get_template_blob(user_id: int, tid: int):
    """Return (name, data_bytes, tokens_list) or None."""
    with Session(_engine) as s:
        t = s.get(Template, tid)
        if not t or t.user_id != user_id:
            return None
        return t.name, t.data, _json.loads(t.tokens or "[]")


def delete_template(user_id: int, tid: int) -> None:
    with Session(_engine) as s:
        s.execute(delete(Template).where(Template.id == tid, Template.user_id == user_id))
        s.commit()


# --------------------------------------------------------------------------- #
# deals
# --------------------------------------------------------------------------- #
def _deal_dict(d: "Deal") -> dict:
    return {"id": d.id, "name": d.name, "prop": d.prop, "status": d.status,
            "date": d.created_at.strftime("%b %d, %Y") if d.created_at else ""}


def create_deal(user_id: int, name: str, prop: str, status: str = "done") -> dict:
    with Session(_engine) as s:
        d = Deal(user_id=user_id, name=name.strip() or "New redline", prop=prop, status=status)
        s.add(d); s.commit(); s.refresh(d)
        return _deal_dict(d)


def list_deals(user_id: int) -> list:
    with Session(_engine) as s:
        rows = s.scalars(select(Deal).where(Deal.user_id == user_id)
                         .order_by(Deal.created_at.desc())).all()
        return [_deal_dict(d) for d in rows]


def delete_deal(user_id: int, did: int) -> None:
    with Session(_engine) as s:
        s.execute(delete(Deal).where(Deal.id == did, Deal.user_id == user_id))
        s.commit()


# --------------------------------------------------------------------------- #
# password hashing
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
class AuthError(ValueError):
    """Raised for bad input during signup/login (safe to show to the user)."""


def validate_signup(email: str, password: str) -> None:
    if not EMAIL_RE.match(email or ""):
        raise AuthError("Enter a valid email address.")
    if not (PW_MIN <= len(password or "") <= PW_MAX):
        raise AuthError(f"Password must be between {PW_MIN} and {PW_MAX} characters.")


# --------------------------------------------------------------------------- #
# user operations
# --------------------------------------------------------------------------- #
def create_user(email: str, password: str, name: str = "") -> User:
    email = (email or "").strip().lower()
    validate_signup(email, password)
    with Session(_engine) as s:
        if s.scalar(select(User).where(User.email == email)):
            raise AuthError("An account with that email already exists.")
        user = User(email=email, name=(name or "").strip(),
                    password_hash=hash_password(password))
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def authenticate(email: str, password: str) -> User | None:
    email = (email or "").strip().lower()
    with Session(_engine) as s:
        user = s.scalar(select(User).where(User.email == email))
        # Always run a hash comparison to keep timing uniform (avoid user enumeration).
        ref = user.password_hash if user else "$2b$12$" + "x" * 53
        ok = verify_password(password, ref)
        if user and user.is_active and ok:
            return user
        return None


def get_user_by_id(uid: int) -> User | None:
    with Session(_engine) as s:
        return s.get(User, uid)
