"""Authentication, role-based access control, device signatures, audit chain and rate limiting."""
from __future__ import annotations
import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict, deque
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import settings
from .db import Audit, Business, User, get_db

_bearer = HTTPBearer(auto_error=False)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def check_password_strength(pw: str):
    if len(pw) < 10 or pw.lower() == pw or not any(c.isdigit() for c in pw):
        raise HTTPException(422, "Password must be at least 10 characters with upper case, lower case and a digit")


def make_token(user: User) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user.id), "role": user.role, "iat": now, "exp": now + settings.jwt_minutes * 60},
                      settings.jwt_secret, algorithm="HS256")


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)) -> User:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        data = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])
        user = db.get(User, int(data["sub"]))
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    return user


def require_role(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Insufficient role")
        return user
    return dep


def owned_business(business_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Business:
    """Object-level authorisation: a user may only touch their own business (admins may read any)."""
    b = db.get(Business, business_id)
    if not b or (b.owner_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Business not found")  # 404 not 403, so ids cannot be probed
    return b


# ---- device signatures ----
def device_canonical(d: str, seq: int, ts: int, v: float, pf: float, f: float, grid: int, ch: list[int]) -> str:
    return f"{d}|{seq}|{ts}|{v:.1f}|{pf:.2f}|{f:.1f}|{grid}|{','.join(str(int(round(w))) for w in ch)}"


def device_sign(secret_hex: str, canonical: str) -> str:
    return hmac.new(bytes.fromhex(secret_hex), canonical.encode(), hashlib.sha256).hexdigest()


def new_device_secret() -> str:
    return secrets.token_hex(32)


# ---- hash-chained audit log ----
def audit(db: Session, actor: str, action: str, detail: str = ""):
    last = db.execute(select(Audit).order_by(Audit.id.desc()).limit(1)).scalar_one_or_none()
    prev = last.hash if last else "0" * 64
    ts = int(time.time())
    h = hashlib.sha256(f"{prev}|{ts}|{actor}|{action}|{detail}".encode()).hexdigest()
    db.add(Audit(ts=ts, actor=actor, action=action, detail=detail[:500], prev_hash=prev, hash=h))
    db.commit()


def verify_audit_chain(db: Session) -> dict:
    prev = "0" * 64
    for row in db.execute(select(Audit).order_by(Audit.id)).scalars():
        expect = hashlib.sha256(f"{prev}|{row.ts}|{row.actor}|{row.action}|{row.detail}".encode()).hexdigest()
        if row.prev_hash != prev or row.hash != expect:
            return {"ok": False, "broken_at": row.id}
        prev = row.hash
    return {"ok": True}


# ---- rate limiting (in-memory sliding window; use a gateway or Redis for multi-process deployments) ----
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, window_s: int = 60):
    now = time.time()
    q = _hits[key]
    while q and q[0] < now - window_s:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests")
    q.append(now)


def limiter(name: str, limit: int, window_s: int = 60):
    def dep(request: Request):
        rate_limit(f"{name}:{request.client.host if request.client else 'x'}", limit, window_s)
    return dep
