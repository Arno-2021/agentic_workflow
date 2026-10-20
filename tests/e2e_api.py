# -*- coding: utf-8 -*-
"""
API 端到端测试（需要服务已启动：python run.py --port 8017）
覆盖：登录 → 发起对话 → SSE 收流 → HitL 暂停 → 基金经理审核 → 报告入库 → 看板/工具清单
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx

BASE = os.getenv("FUNDAGENT_TEST_BASE", "http://127.0.0.1:8000")


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60, trust_env=False) as c:
        # 1. 登录（研究员）
        r = await c.post("/api/auth/login", json={"username": "analyst_001", "password": "fund2026"})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        H = {"Authorization": f"Bearer {token}"}
        print("✅ 1. 研究员登录成功")

        # 2. 发起复杂任务（触发三 Agent + HitL）
        r = await c.post("/api/chat", headers=H,
                         json={"message": "帮我分析一下宁德时代的投资价值，并生成基金研究报告"})
        thread_id = r.json()["thread_id"]
        print(f"✅ 2. 任务已创建 thread_id={thread_id}")

        # 3. SSE 收流直到 paused / final
        paused = False
        async with c.stream("GET", f"/api/chat/stream?thread_id={thread_id}&token={token}") as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                e = json.loads(line[6:])
                if e["type"] == "progress":
                    print(f"    📍 [{e.get('agent')}] {e.get('node')}: {e.get('message')}")
                elif e["type"] == "paused":
                    paused = True
                    print(f"✅ 3. SSE 流式正常，任务暂停待审核：{e['message']}")
                    break
                elif e["type"] == "final":
                    print("（任务直接完成，未触发 HitL）")
                    break
        assert paused, "预期触发 Human-in-the-Loop 暂停"

        # 4. 任务状态
        r = await c.get(f"/api/tasks/{thread_id}", headers=H)
        st = r.json()
        assert st["paused"] and "analysis_agent" in str(st["next_nodes"]), st
        print(f"✅ 4. 任务状态查询：paused={st['paused']}，节点流水 {len(st['progress'])} 条")

        # 5. 基金经理登录并审核（确认）
        r = await c.post("/api/auth/login", json={"username": "fm_001", "password": "fund2026"})
        fm_token = r.json()["token"]
        r = await c.post(f"/api/tasks/{thread_id}/review",
                         headers={"Authorization": f"Bearer {fm_token}"},
                         json={"decision": "approve", "comments": "同意结论"})
        assert r.status_code == 200, r.text
        print("✅ 5. 基金经理审核通过（LangGraph 断点恢复）")

        # 6. 等待 final 事件
        final = None
        for _ in range(60):
            r = await c.get(f"/api/tasks/{thread_id}", headers=H)
            if not r.json()["paused"]:
                break
            await asyncio.sleep(0.5)
        # final 事件从 SSE 拿（重开一个流读取历史事件）
        async with c.stream("GET", f"/api/chat/stream?thread_id={thread_id}&token={token}") as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                e = json.loads(line[6:])
                if e["type"] == "final":
                    final = e
                    break
        assert final, "未收到 final 事件"
        print(f"✅ 6. 任务完成，报告号 {final.get('report_id')}")

        # 7. 报告列表与详情
        r = await c.get("/api/reports", headers=H)
        reports = r.json()["reports"]
        assert any(x["report_id"] == final["report_id"] for x in reports), reports
        print(f"✅ 7. 报告库查询：共 {len(reports)} 篇，包含刚生成的报告")

        # 8. 信息隔离墙：analyst_001 不能看别人的报告详情（若报告属于他人）
        r = await c.get(f"/api/reports/{final['report_id']}", headers=H)
        assert r.json().get("report"), r.text
        print("✅ 8. 报告详情查询正常（本部门本人）")

        # 9. 投委会审核
        r = await c.post("/api/auth/login", json={"username": "ic_001", "password": "fund2026"})
        ic_token = r.json()["token"]
        r = await c.post(f"/api/reports/{final['report_id']}/audit",
                         headers={"Authorization": f"Bearer {ic_token}"},
                         json={"decision": "approve", "comments": "投委会终审通过"})
        assert r.status_code == 200, r.text
        print("✅ 9. 投委会终审通过（留痕）")

        # 10. 看板 / 工具清单 / 跟踪 / 审计日志
        dash = (await c.get("/api/dashboard", headers=H)).json()
        tools = (await c.get("/api/tools", headers=H)).json()
        tracking = (await c.get("/api/tracking", headers=H)).json()
        logs = (await c.get("/api/audit-logs", headers=H)).json()
        print(f"✅ 10. 看板 {dash}｜工具 {tools['total']} 个｜跟踪 {len(tracking['tracking'])} 只｜"
              f"审计日志 {len(logs['logs'])} 条")

        # 11. 无 token 访问应 401
        r = await c.get("/api/dashboard")
        assert r.status_code == 401, r.status_code
        print("✅ 11. 未认证访问被拒绝（401）")

    print("\n🎉 全部 API 端到端测试通过")


if __name__ == "__main__":
    asyncio.run(main())
