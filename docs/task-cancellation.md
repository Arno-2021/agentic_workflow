# 任务取消功能说明

## 问题背景

原系统存在两个场景未考虑：

1. **用户关闭浏览器后**：任务是否继续运行？
2. **用户主动终止**：如何停止正在执行的任务？

## 解决方案

### 1. 浏览器关闭后的行为

✅ **已支持** - 任务会在后台继续执行

- 使用 `asyncio.create_task()` 在后台异步执行，独立于 HTTP 连接
- SSE 连接断开不影响任务执行
- 用户重新打开浏览器后可通过以下方式恢复：
  - `GET /api/tasks/{thread_id}` 查询任务状态
  - `GET /api/chat/stream?thread_id={thread_id}` 重新订阅 SSE 事件流

**设计理念**：LangGraph 任务通常耗时较长（数据采集、LLM 推理、多 Agent 协作），用户体验应该是"提交任务后可以离开，稍后回来查看结果"。

### 2. 主动终止任务

🆕 **新增功能** - 用户可随时取消任务

#### API 端点

```http
POST /api/tasks/{thread_id}/cancel
Authorization: Bearer {token}
```

**响应示例**：

```json
{
  "status": "cancelling",
  "message": "任务正在取消"
}
```

或

```json
{
  "status": "cancelled", 
  "message": "已取消暂停中的任务"
}
```

#### 支持的场景

| 任务状态 | 取消行为 | 结果 |
|---------|---------|------|
| **运行中** | `asyncio.Task.cancel()` | 任务收到 `CancelledError`，清理后标记为 `cancelled` |
| **暂停中（HitL）** | 直接更新状态 | 立即标记为 `cancelled`，不恢复执行 |
| **已完成** | 无操作 | 返回 `not_running` |

#### 实现机制

**1. 任务追踪**
```python
class ResearchService:
    def __init__(self):
        # 保存运行中任务的引用
        self._running_tasks: Dict[str, asyncio.Task] = {}
        # 取消标记集合
        self._cancelled_threads: set[str] = set()
```

**2. 取消流程**
```
用户点击取消
    ↓
添加到 _cancelled_threads 集合
    ↓
获取 _running_tasks 中的 Task 引用
    ↓
调用 task.cancel() → 抛出 CancelledError
    ↓
except CancelledError: 更新状态为 cancelled
    ↓
推送 SSE 事件 {"type": "cancelled"}
    ↓
清理任务引用和取消标记
```

**3. 执行过程中的检查点**

在关键位置检查取消标记，确保及时响应：

```python
async def _execute(self, thread_id, ...):
    # 执行前检查
    if thread_id in self._cancelled_threads:
        return
    
    result = await fund_agent_graph.ainvoke(...)
    
    # 执行后检查（防止执行期间被取消）
    if thread_id in self._cancelled_threads:
        return
```

**4. 自动清理**

任务完成后自动清理资源：

```python
task = asyncio.create_task(self._execute(...))
self._running_tasks[thread_id] = task

def cleanup(_):
    self._running_tasks.pop(thread_id, None)
    self._cancelled_threads.discard(thread_id)

task.add_done_callback(cleanup)
```

### 3. 前端集成建议

#### 添加取消按钮

```javascript
async function cancelTask(threadId) {
  const response = await fetch(`/api/tasks/${threadId}/cancel`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`
    }
  });
  
  const result = await response.json();
  
  if (result.status === 'cancelling') {
    showMessage('任务正在取消...');
  } else if (result.status === 'cancelled') {
    showMessage('任务已取消');
    closeSSEConnection();
  }
}
```

#### SSE 事件处理

```javascript
eventSource.addEventListener('message', (e) => {
  const data = JSON.parse(e.data);
  
  switch(data.type) {
    case 'cancelled':
      showMessage('任务已被取消');
      eventSource.close();
      break;
    case 'status':
    case 'progress':
      updateProgress(data.message);
      break;
    case 'final':
      showResult(data.message);
      eventSource.close();
      break;
    case 'error':
      showError(data.message);
      eventSource.close();
      break;
  }
});
```

#### UI 状态管理

```javascript
// 根据任务状态显示/隐藏取消按钮
function updateTaskUI(status) {
  const cancelButton = document.getElementById('cancel-btn');
  
  if (status === 'running' || status === 'awaiting_review') {
    cancelButton.style.display = 'block';
  } else {
    cancelButton.style.display = 'none';
  }
}
```

## 数据库变更

### chat_sessions 表新增状态值

原有状态：
- `active` - 活跃
- `completed` - 已完成  
- `awaiting_review` - 等待审核

新增状态：
- `cancelled` - 已取消

### audit_logs 新增日志类型

- `task_cancel_requested` - 取消请求
- `task_cancelled` - 任务已取消

## 测试

运行取消功能测试：

```bash
python tests/test_cancellation.py
```

测试覆盖：
1. ✓ 运行中的任务可以被取消
2. ✓ 暂停中的任务（HitL）可以被取消
3. ✓ 取消后 SSE 推送 cancelled 事件
4. ✓ 取消后数据库状态更新为 cancelled
5. ✓ 并发取消的数据结构安全性

## 注意事项

### 1. LangGraph 执行粒度

LangGraph 的 `ainvoke()` 是一个长时间运行的操作，取消会在以下时机生效：
- Agent 节点之间的切换
- 工具调用的间隙
- await 点

如果某个节点内部执行时间很长（如大型 LLM 推理），取消可能不是即时的。

### 2. 资源清理

取消任务时，已经执行的操作不会回滚：
- 已保存的消息记录保留
- 已写入的分析结果保留
- 已生成的中间数据保留

这是设计选择，便于后续排查和恢复。

### 3. HitL 暂停状态

处于 Human-in-the-Loop 暂停的任务可以直接取消，无需等待审核。取消后该 thread_id 的 Checkpoint 仍然保存在数据库中，但不会再被恢复执行。

### 4. 并发安全

- `_running_tasks` 字典和 `_cancelled_threads` 集合操作都在同一个事件循环中，无需额外加锁
- 多个用户同时取消同一任务是安全的（幂等操作）

## 未来优化方向

1. **更细粒度的取消**：在长时间运行的节点内部增加取消检查点
2. **取消超时处理**：如果任务 5 秒内未响应取消，强制标记为取消（状态更新但任务可能仍在后台运行）
3. **取消原因记录**：在 audit_logs 中记录取消原因（用户主动/超时/系统异常）
4. **批量取消**：允许一次取消多个任务
5. **取消权限控制**：研究员只能取消自己的任务，管理员可以取消任何任务

## API 文档更新

在 README.md 的 API 一览表中添加：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/tasks/{thread_id}/cancel` | **取消任务**（运行中/暂停中的任务） |
