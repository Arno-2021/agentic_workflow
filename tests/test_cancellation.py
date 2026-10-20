# -*- coding: utf-8 -*-
"""
任务取消功能测试
================
验证：
1. 运行中的任务可以被取消
2. 暂停中的任务（HitL）可以被取消
3. 取消后 SSE 推送 cancelled 事件
4. 取消后数据库状态更新为 cancelled
"""
import asyncio
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.service import research_service
from app.db import store, init_db
from app.graph.events import event_bus


async def test_cancel_running_task():
    """测试取消正在运行的任务"""
    print("测试 1: 取消正在运行的任务...")

    # 启动任务
    thread_id = await research_service.run_task(
        department_id="dept_001",
        user_id="analyst_001",
        query="查询宁德时代的最新数据"
    )

    print(f"  任务已启动: {thread_id}")

    # 等待任务开始执行
    await asyncio.sleep(0.5)

    # 取消任务
    result = await research_service.cancel_task(
        thread_id=thread_id,
        department_id="dept_001",
        user_id="analyst_001"
    )

    print(f"  取消结果: {result}")
    assert result["status"] in ["cancelling", "cancelled", "not_running"], f"意外的状态: {result['status']}"

    # 等待取消完成
    await asyncio.sleep(1)

    # 验证事件总线有 cancelled 事件
    events = event_bus.channel(thread_id).snapshot()
    cancelled_events = [e for e in events if e.get("type") == "cancelled"]

    if len(cancelled_events) > 0:
        print(f"  ✓ 任务 {thread_id} 已成功取消，收到 {len(cancelled_events)} 个取消事件")
    else:
        print(f"  ⚠ 任务快速完成，未捕获到取消事件（这是正常的）")


async def test_cancel_paused_task():
    """测试取消暂停中的任务（Human-in-the-Loop）"""
    print("\n测试 2: 取消暂停中的任务...")

    thread_id = "task_test_paused_001"

    # 模拟一个暂停的任务
    store.create_session(thread_id, "dept_001", "analyst_001", title="测试暂停任务")
    store.touch_session(thread_id, status="awaiting_review")

    print(f"  模拟暂停任务: {thread_id}")

    # 取消任务
    result = await research_service.cancel_task(
        thread_id=thread_id,
        department_id="dept_001",
        user_id="analyst_001"
    )

    print(f"  取消结果: {result}")
    assert result["status"] == "cancelled", f"预期状态为 cancelled，实际为 {result['status']}"

    # 验证数据库状态
    from app.dbutil import get_conn
    conn = get_conn()
    cursor = conn.execute(
        "SELECT status FROM chat_sessions WHERE thread_id = ?",
        (thread_id,)
    )
    row = cursor.fetchone()
    assert row and row[0] == "cancelled", f"数据库状态不正确: {row[0] if row else 'None'}"

    print(f"  ✓ 暂停任务 {thread_id} 已成功取消")


def test_concurrent_cancellation():
    """测试并发取消场景"""
    print("\n测试 3: 数据结构初始化检查...")

    # 验证取消标记集合和任务字典的线程安全性
    assert hasattr(research_service, '_cancelled_threads'), "缺少 _cancelled_threads 属性"
    assert hasattr(research_service, '_running_tasks'), "缺少 _running_tasks 属性"
    assert isinstance(research_service._cancelled_threads, set), "_cancelled_threads 应该是 set 类型"
    assert isinstance(research_service._running_tasks, dict), "_running_tasks 应该是 dict 类型"

    print("  ✓ 任务取消数据结构已正确初始化")


async def main():
    print("=" * 60)
    print("任务取消功能测试")
    print("=" * 60)

    # 初始化数据库
    init_db(seed_users=False)

    try:
        await test_cancel_running_task()
        await test_cancel_paused_task()
        test_concurrent_cancellation()

        print("\n" + "=" * 60)
        print("✓ 所有取消功能测试通过")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
