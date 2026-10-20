# -*- coding: utf-8 -*-
"""JWT 认证助手：登录签发 Token，解析部门/用户/角色（对应文档 4.1 注册与登录）。"""
from __future__ import annotations

import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_bearer = HTTPBearer(auto_error=False)


def create_token(user: dict) -> str:
    payload = {
        "user_id": user["user_id"],
        "name": user.get("name", ""),
        "role": user["role"],
        "department_id": user["department_id"],
        "exp": int(time.time()) + settings.jwt_ttl_seconds,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Token 无效或过期：{e}")


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="缺少 Authorization Header")
    return decode_token(credentials.credentials)


def require_roles(*roles: str):
    def _dep(user: dict = Depends(current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return user
    return _dep
