# -*- coding: utf-8 -*-
"""认证路由：登录 / 当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import mock_data
from ..security import create_token, current_user

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/login")
async def login(payload: dict):
    """登录：mock 用户体系（analyst_001 研究员 / fm_001 基金经理 / ic_001 投委会）。"""
    user_id = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    user = next((u for u in mock_data.USERS
                 if u["user_id"] == user_id and u["password"] == password), None)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(user)
    return {"token": token, "user": {k: v for k, v in user.items() if k != "password"}}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    return user
