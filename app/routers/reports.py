# -*- coding: utf-8 -*-
"""报告路由：报告列表 / 详情 / 投委会审核。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..db import store
from ..security import current_user, require_roles

router = APIRouter(prefix="/api", tags=["报告"])


@router.get("/reports")
async def list_reports(user: dict = Depends(current_user)):
    """报告列表：研究员看自己的，投委会/基金经理看部门全部（信息隔离墙）。"""
    if user["role"] == "analyst":
        reports = store.list_reports(user["department_id"], user["user_id"])
    else:
        reports = store.list_reports(user["department_id"])
    return {"reports": [{k: r[k] for k in
                         ("report_id", "title", "report_type", "audit_status",
                          "reviewer_id", "reviewer_comments", "created_at")}
                        for r in reports]}


@router.get("/reports/{report_id}")
async def report_detail(report_id: str, user: dict = Depends(current_user)):
    report = store.get_report(report_id)
    if not report:
        return {"error": "报告不存在"}
    # 隔离校验：分析师只能看本部门本人的报告
    if user["role"] == "analyst" and report["user_id"] != user["user_id"]:
        return {"error": "无权访问（信息隔离墙）"}
    return {"report": report}


@router.post("/reports/{report_id}/audit")
async def audit_report(report_id: str, payload: dict,
                       user: dict = Depends(require_roles("fund_manager", "committee"))):
    """投委会/基金经理审核报告（留痕 5 年）。"""
    decision = payload.get("decision", "approve")
    store.audit_report(report_id, user["user_id"], decision, payload.get("comments", ""))
    store.log("report_audited", user["department_id"], user["user_id"],
              {"report_id": report_id, "decision": decision})
    return {"report_id": report_id, "audit_status": decision}
