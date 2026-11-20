from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app import db
from app.audit import audit
from app.config import settings
from app.security import COOKIE_NAME, ROLE_SCOPES, Principal, authenticate, issue_token, principal

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Login(BaseModel):
    email: str
    password: str


@router.post("/login")
async def login(body: Login, response: Response):
    user = await authenticate(body.email, body.password)
    if not user:
        await audit.append({"workspace_id": "-", "actor": body.email[:120], "action": "auth.login", "decision": "deny"})
        raise HTTPException(401, "invalid email or password")
    token = issue_token(user)
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=settings.cookie_secure,
                        max_age=settings.jwt_ttl_minutes * 60, path="/")
    await audit.append({"workspace_id": user["workspace_id"], "actor": user["id"], "action": "auth.login", "decision": "allow"})
    return {"token": token, "user": await _me(user["id"])}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


async def _me(user_id: str) -> dict:
    u = await db.fetch_one(
        """SELECT u.id, u.email, u.name, u.role, u.workspace_id, w.name AS workspace_name
           FROM users u JOIN workspaces w ON w.id=u.workspace_id WHERE u.id=%s""", (user_id,))
    return {**u, "scopes": sorted(ROLE_SCOPES[u["role"]])}


@router.get("/me")
async def me(p: Principal = Depends(principal)):
    return await _me(p.subject)


@router.get("/demo-users")
async def demo_users():
    """Demo sign-in shortcuts. Only available in demo mode; the password is the configured DEMO_PASSWORD."""
    if not settings.demo_mode:
        return {"demo_mode": False, "users": []}
    users = await db.fetch_all(
        """SELECT u.email, u.name, u.role, w.name AS workspace FROM users u JOIN workspaces w ON w.id=u.workspace_id
           ORDER BY w.name DESC, array_position(ARRAY['admin','approver','operator','viewer'], u.role)""")
    return {"demo_mode": True, "users": users,
            "password_hint": settings.demo_password if settings.env != "prod" else None}
