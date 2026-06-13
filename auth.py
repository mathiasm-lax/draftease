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
import os
import re

import bcrypt
from sqlalchemy import Boolean, DateTime, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

DB_URL = os.environ.get("DRAFTEASE_DB_URL", "sqlite:///draftease.db")
_engine = create_engine(DB_URL, echo=False, future=True)

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


def init_db() -> None:
    Base.metadata.create_all(_engine)


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
