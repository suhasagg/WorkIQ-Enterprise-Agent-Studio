"""Authentication (JWT in httpOnly cookie or Bearer header), principals and role-based authorization."""
from __future__ import annotations

import time
from dataclasses import dataclass

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

from app import db
from app.config import settings

COOKIE_NAME = "wiq_session"
ROLES = ("viewer", "operator", "approver", "admin")
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "viewer": frozenset({"workiq:read"}),
    "operator": frozenset({"workiq:read", "workiq:write", "agent:run", "knowledge:write", "eval:run"}),
    "approver": frozenset({"workiq:read", "workiq:write", "agent:run", "knowledge:write", "eval:run", "approval:decide"}),
    "admin": frozenset({"workiq:read", "workiq:write", "agent:run", "knowledge:write", "eval:run",
                        "approval:decide", "connector:admin", "audit:read"}),
}


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: str  # workspace id; name kept from the original codebase
    scopes: frozenset[str]
    role: str = "viewer"
    name: str = ""
    email: str = ""

    @property
    def workspace_id(self) -> str:
        return self.tenant

    def has(self, scope: str) -> bool:
        return scope in self.scopes

    def to_dict(self) -> dict:
        return {"subject": self.subject, "tenant": self.tenant, "role": self.role,
                "scopes": sorted(self.scopes), "name": self.name, "email": self.email}

    @classmethod
    def from_dict(cls, d: dict) -> Principal:
        return cls(d["subject"], d["tenant"], frozenset(d.get("scopes", [])), d.get("role", "viewer"),
                   d.get("name", ""), d.get("email", ""))


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def issue_token(user: dict) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": user["id"], "tenant": user["workspace_id"], "role": user["role"], "name": user["name"],
         "email": user["email"], "iat": now, "exp": now + settings.jwt_ttl_minutes * 60},
        settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> Principal:
    x = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    role = x.get("role", "viewer")
    if role not in ROLE_SCOPES:
        raise jwt.InvalidTokenError("unknown role")
    return Principal(str(x["sub"]), str(x["tenant"]), ROLE_SCOPES[role], role, x.get("name", ""), x.get("email", ""))


async def principal(request: Request) -> Principal:
    token = None
    auth = request.headers.get("authorization")
    if auth and auth.startswith("Bearer "):
        token = auth[7:]
    elif request.cookies.get(COOKIE_NAME):
        token = request.cookies[COOKIE_NAME]
    if not token:
        raise HTTPException(401, "authentication required")
    try:
        p = decode_token(token)
    except Exception:
        raise HTTPException(401, "invalid or expired session") from None
    request.state.principal = p
    return p


def require(scope: str):
    async def dep(p: Principal = Depends(principal)) -> Principal:
        if not p.has(scope):
            raise HTTPException(403, f"missing permission: {scope}")
        return p
    return dep


async def authenticate(email: str, password: str) -> dict | None:
    user = await db.fetch_one("SELECT * FROM users WHERE lower(email)=lower(%s)", (email,))
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return user
