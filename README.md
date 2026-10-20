# FundAgent AI · 智能基金研究 Agent 系统

基于 **LangGraph 状态图 + Orchestrator 编排器** 的基金研究多 Agent 系统，实现《AgenticWorkflow-EduAgent 项目场景拓展文档》1.13 节「智能基金研究 Agent（FundAgent AI）」的完整可运行版本。

- **3 个 Agent**：DataAgent（数据采集，8 节点）/ AnalysisAgent（基金分析，9 节点 + Human-in-the-Loop）/ ReportAgent（报告生成，7 节点）
- **Orchestrator 编排器**：8 类意图识别 + 单 Agent 直达 / 多 Agent 串联 Pipeline
- **28 个工具**：LLM 工具 / 规则引擎 / MCP 工具 / 外部 API 封装（对应文档工具清单表）
- **双层记忆**：LangGraph Checkpointer（短期，按 thread_id）+ SQLite 长期记忆（按 department_id + user_id 隔离）
- **Web 服务**：FastAPI + SSE 流式输出 + JWT 认证 + 聊天前端 + Human-in-the-Loop 审核面板

数据为 **mock**（6 只标的：宁德时代 / 比亚迪 / 贵州茅台 / 隆基绿能 / 阳光电源 / 亿纬锂能）。
**LLM 默认对接本机 Ollama**（`qwen3.5:9b`，走原生 `/api/chat` 端点并关闭 thinking），
6 个 LLM 工具（公告解析 / 采集摘要 / 研报解析 / 风险评估 / 投资逻辑 / 报告生成）全部由真实模型产出；
Ollama 不可达时自动降级到内置模板（打警告日志），流程不中断。

---

## 一、快速开始

```bash
# 1. 安装依赖（Python 3.10+，已在 3.13 验证）
pip install -r requirements.txt

# 2. 冒烟自检（验证环境：依赖 / Checkpointer / 图组装 / mock 数据）
python tests/probe_cp.py

# 3a. CLI 演示：文档第四章三个示例场景（数据查询 / 深度分析+HitL / 三Agent全流程）
python run.py --demo

# 3b. Web 服务（推荐）
python run.py
# 打开 http://127.0.0.1:8000
```

**演示账号**（密码均为 `fund2026`）：

| 账号 | 角色 | 能做什么 |
| --- | --- | --- |
| `analyst_001` | 行业研究员 | 发起研究任务、查看自己的报告 |
| `fm_001` | 基金经理 | **审核分析结论**（确认 / 退回修改）、审核报告 |
| `ic_001` | 投委会成员 | 部门全部报告终审、数据看板 |

**推荐体验路径**：用 `analyst_001` 登录 → 点快捷问题「新能源主题全流程」→ 右侧看节点流水 →
任务暂停后**切换到 `fm_001` 登录**（新开浏览器标签）→ 在「审核/报告」页点「确认通过」→ 回到研究员视角查看最终报告。

---

## 二、系统架构

```
用户输入
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│ Orchestrator 编排器（意图识别 8 类 + 路由决策）                  │
│   data_query ─────────────► DataAgent                        │
│   research_query ─────────► AnalysisAgent                    │
│   market/stock/macro/... ─► DataAgent → AnalysisAgent        │
│   含"生成报告/全流程" ─────► DataAgent → AnalysisAgent → ReportAgent │
└─────────────────────────────────────────────────────────────┘
   │ 三 Agent 共享同一份 ResearchState（子图即节点，上下文零丢失传递）
   ▼
DataAgent（8 节点）        AnalysisAgent（9 节点）        ReportAgent（7 节点）
 load_data_sources          load_research_pool            load_analysis_result
 fetch_market_data          parse_research_report          generate_report_body
 fetch_financial_data       calculate_financial_indicators compliance_check ◄─┐
 fetch_announcements        build_valuation_model          format_report      │ 不合规
 classify_industry          assess_risk_level              add_disclaimer     │ 重生成
 validate_consistency ◄─┐   check_risk_threshold ◄─┐       save_and_push      │
   │不通过→重新对齐      │   │过高→换标的         │       log_audit_trail ───┘
 store_vector_db         │   generate_investment_logic
 generate_data_summary   │   human_review ── interrupt ──► 基金经理审核（HitL）
                         │   save_analysis_result           确认→保存 / 退回→重新生成
                         └── 一致性/风险重试回路
   ▼
summarize 汇总 → SSE 流式推送前端 → 异步写长期记忆（Oracle-like SQLite）
```

**文档映射**：Oracle → SQLite（`app/dbutil.py` 单连接 + 自动降级）、Milvus → 内存向量库（`mcp_tools.py`）、
Wind/巨潮/央行 → mock 数据源（`app/mock_data.py`）、LLM → 模板生成或 OpenAI 兼容接口（`app/llm.py`）。

---

## 三、目录结构

```
FundAgent-AI/
├── run.py                      # 一键启动（--demo 跑 CLI 演示）
├── requirements.txt
├── README.md
├── app/
│   ├── config.py               # 配置（LLM mock/real、JWT、阈值）
│   ├── llm.py                  # LLM 客户端（mock 模板 / real OpenAI 兼容）
│   ├── dbutil.py               # SQLite 连接（WAL 自动降级 + 线程锁）
│   ├── db.py                   # 持久化层（表结构对齐文档 3.6，含双层隔离）
│   ├── mock_data.py            # mock 数据（6 标的行情/财务/公告/研报/宏观）
│   ├── security.py             # JWT 认证与角色权限
│   ├── service.py              # 任务驱动（执行/HitL恢复/事件推送）
│   ├── main.py                 # FastAPI 入口
│   ├── graph/
│   │   ├── state.py            # 共享 ResearchState
│   │   ├── events.py           # SSE 事件总线
│   │   ├── checkpointer.py     # SQLite Checkpointer（默认后端）
│   │   ├── redis_checkpointer.py  # Redis Checkpointer（Docker，可切换）
│   │   ├── summarize.py        # 长对话摘要压缩节点
│   │   ├── data_agent.py       # DataAgent 8 节点 + 一致性校验回路
│   │   ├── analysis_agent.py   # AnalysisAgent 9 节点 + interrupt 审核
│   │   ├── report_agent.py     # ReportAgent 7 节点 + 合规审查回路
│   │   └── orchestrator.py     # 意图分类 + 顶层图组装
│   ├── tools/                  # 28 个工具（LLM 6+ / 规则引擎 9 / MCP 4 / API 5+ / 辅助 4）
│   └── routers/                # auth / chat / reports / admin
│   ├── static/index.html       # 聊天前端（SSE 流式 + 审核面板 + 看板）
├── scripts/demo.py             # 文档三示例 CLI 演示
├── docs/
│   ├── intent-chains.md        # 九类意图完整链路文档（链路图/节点明细/回路/索引）
│   └── context-compression.md  # 长对话摘要压缩方案（阈值/增量/兜底/调参）
└── tests/
    ├── probe_cp.py             # 冒烟自检（4 项检查）
    ├── test_compression.py     # 摘要压缩测试（6 项断言）
    └── e2e_api.py              # API 端到端测试（11 项断言）
```

---

## 四、工具清单（28 个）

| 类别 | 工具 | 说明 |
| --- | --- | --- |
| LLM 工具 | `llm_parse_announcement` `llm_generate_summary` `llm_parse_research_report` `llm_assess_risk` `llm_extract_investment_logic` `llm_generate_report` `llm_generate_disclaimer` | 公告/研报解析、摘要、风险、投资逻辑、报告生成（mock 模板 / real LLM 双模式） |
| 规则引擎 | `format_data_standard` `align_financial_data` `industry_classify` `data_consistency_check` `financial_indicator_calc` `valuation_model_calc` `risk_indicator_calc` `compliance_filter` `format_report_template` | 标准化、口径对齐、申万分类、一致性校验、财务指标、多方法估值、风险指标、合规审查、模板格式化 |
| MCP 工具 | `get_fund_report` `query_announcements` `store_to_vector_db` `search_research_reports` | 财务报表、公告查询、向量库存储与检索（mock Milvus） |
| 外部 API 封装 | `get_market_quote` `fetch_market_data_api` `get_report_detail` `save_research_result` `push_to_fund_manager` `save_report_to_repository` `get_research_result` `save_audit_log` | 行情、报告库、分析结果保存、推送基金经理、审计日志 |

> 文档宣称 20 个工具，但工具表格实际列出以上名称；实现以表格为准。`GET /api/tools` 可查看完整清单。

---

## 五、意图分类与路由（8+1 类）

| 意图 | 典型问法 | 路由 |
| --- | --- | --- |
| `data_query` | 「查一下贵州茅台最新的财务数据」 | DataAgent |
| `research_query` | 「帮我找关于比亚迪的研报」 | AnalysisAgent |
| `stock_analysis` | 「帮我分析宁德时代的投资价值」 | DataAgent → AnalysisAgent |
| `market_analysis` | 「新能源主题走势怎么样」 | DataAgent → AnalysisAgent |
| `macro_analysis` | 「最新 CPI 对债市的影响」 | DataAgent → AnalysisAgent |
| `industry_compare` | 「对比宁德时代和比亚迪」 | DataAgent → AnalysisAgent |
| `portfolio_advice` | 「帮我优化这个持仓组合」 | DataAgent → AnalysisAgent |
| `financial_diagnosis` | 「诊断一下恒大财务状况」 | DataAgent → AnalysisAgent |
| `report_generate` / 明示出报告 | 「…生成基金研究报告」「全流程处理」 | DataAgent → AnalysisAgent → ReportAgent |

---

## 六、Human-in-the-Loop（审核回路）

AnalysisAgent 的 `human_review` 节点调用 LangGraph `interrupt()` 暂停整张图：

1. 基金经理在 Web 端看到审核卡片（评级/目标价/风险/投资逻辑）
2. **确认通过** → `Command(resume={"decision":"approve"})` → 断点恢复 → 保存分析结果 → 继续下游
3. **退回修改** → 带意见回到 `generate_investment_logic` 重新生成 → 再次进入审核（最多 2 次，防死循环）

Checkpointer 保证暂停期间 State 不丢失（SQLite 持久化，多轮/断点续传）。

---

## 七、API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/login` | 登录签发 JWT |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/chat` | 发起研究任务，返回 thread_id |
| GET | `/api/chat/stream?thread_id=&token=` | **SSE 流式**进度（progress/paused/final/error） |
| GET | `/api/tasks` | 历史任务列表 |
| GET | `/api/tasks/{thread_id}` | 任务状态（含暂停信息与节点流水） |
| POST | `/api/tasks/{thread_id}/review` | **HitL 审核**（approve/reject，基金经理/投委会权限） |
| POST | `/api/tasks/{thread_id}/cancel` | **取消任务**（运行中/暂停中的任务） |
| GET | `/api/reports` `/api/reports/{id}` | 报告列表/详情（信息隔离墙校验） |
| POST | `/api/reports/{id}/audit` | 投委会终审（留痕） |
| GET | `/api/dashboard` | 数据看板 |
| GET | `/api/tools` | 工具清单 |
| GET | `/api/tracking` | 单只基金跟踪（长期记忆） |
| GET | `/api/audit-logs` | 审计日志（留痕 5 年） |
| GET | `/api/history/{thread_id}` | 对话历史 |
| GET | `/api/macro` | 宏观数据（mock） |

---

## 八、LLM 配置（Ollama 默认）

默认对接本机 **Ollama**（`http://localhost:11434`，模型 `qwen3.5:9b`），无需任何配置即可运行。
可用环境变量覆盖（见 `.env.example`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FUNDAGENT_LLM_MODE` | `real` | `real`=调用 LLM；`mock`=离线模板 |
| `FUNDAGENT_LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI 兼容基址 |
| `FUNDAGENT_LLM_MODEL` | `qwen3.5:9b` | 模型名（`ollama list` 查看） |
| `FUNDAGENT_LLM_TIMEOUT` | `180` | 单次调用超时（秒），长报告可调大 |

**qwen3.5 思考模型的两个适配要点**（`app/llm.py` 已处理）：
1. Ollama 的 OpenAI 兼容端点会**忽略** `think=false`，导致 content 为空——
   因此 Ollama 一律走**原生 `/api/chat` 端点**（`think:false` + `format:json` 均正常）；
   其他服务（DashScope/vLLM 等）才走 `/v1/chat/completions` + `response_format`。
2. JSON 类工具用 `format:"json"` 语法约束，解析失败自动重试一次，再失败降级模板。

**降级策略**：Ollama 未启动时自动探测失败 → 工具层回退内置模板（打警告日志），
LangGraph 流程不中断；恢复后无需重启即自动切回真实 LLM。

**切换其他模型/服务**：改 `FUNDAGENT_LLM_BASE_URL/API_KEY/MODEL` 三个变量即可，
例如 DashScope：`BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`、`MODEL=qwen-plus`。

## 九、从 mock 切换到生产

| 组件 | mock 实现 | 生产替换 |
| --- | --- | --- |
| LLM | 本机 Ollama qwen3.5:9b（默认） | 改三个环境变量指向公司内网 vLLM / DashScope |
| Wind/巨潮/央行 | `app/mock_data.py` | `app/tools/api_tools.py` 里改 httpx 调用公司 API（字段映射逻辑已预留） |
| Milvus | 内存 n-gram 向量（`mcp_tools.py`） | 换真实 embedding（如 mxbai-embed-large）+ Milvus client |
| Oracle | SQLite（`app/db.py`，表结构一致） | 换 oracledb 连接池，DAO 签名不变 |
| Checkpointer | SQLite / **Redis 双实现**（`graph/checkpointer.py` / `graph/redis_checkpointer.py`，同一 `BaseCheckpointSaver` 接口） | 已就绪：`FUNDAGENT_CHECKPOINTER_BACKEND=redis` + `FUNDAGENT_REDIS_URL=redis://localhost:8379/0`（Docker Redis，需认证时设 `FUNDAGENT_REDIS_PASSWORD`）；Redis 版支持 TTL 自动过期（`FUNDAGENT_CHECKPOINTER_TTL_DAYS=90`）与多实例共享断点 |

## 十、验证

```bash
python tests/probe_cp.py        # 冒烟自检（4 项）
python run.py --demo            # 文档三示例场景（真实 LLM，约 2-3 分钟）
python run.py                   # 启动服务后，另开终端：
python tests/e2e_api.py         # API 端到端测试（11 项断言，需服务在 8000 端口）
```

**已验证**：一致性校验回路（公告 EPS 偏差 1.2% → 重新采集对齐）、风险阈值回路
（过高→换标的→耗尽回迁最初标的）、合规审查回路、HitL 中断恢复、三 Agent 串联、
SSE 流式、JWT 认证、信息隔离墙、投委会终审留痕；全部 6 个 LLM 工具经 Ollama qwen3.5:9b 实测。

## 十一、注意事项

- 演示数据中财务数字为构造值，仅用于跑通流程，不构成任何投资建议。
- 若本机同时运行多个进程访问 `data/fundagent.db`：正常环境走 WAL 模式无冲突；
  个别沙箱环境会降级为 EXCLUSIVE 锁（同时只允许一个进程），遇到自检卡住时先关掉其他占用进程。
- 端口默认 8000，可用 `python run.py --port 9000` 修改。
