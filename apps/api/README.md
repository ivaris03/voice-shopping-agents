# Voice Shopping API

FastAPI 模块化单体，包含用户/商家/平台 HTTP API、订单事务、画像更新、LangGraph 四节点
工作流、合规过滤、DashScope 模型适配、文本/音频 WebSocket、Redis 重放和 LangSmith 元数据。

从仓库根目录运行：

```bash
uv sync --project apps/api
uv run --project apps/api python -m voice_shopping_api.server --reload --port 8000
uv run --project apps/api pytest
uv run --project apps/api ruff check .
```

测试分层：

```bash
uv run --project apps/api pytest -m contract
uv run --project apps/api pytest -m service
uv run --project apps/api pytest -m e2e
```

E2E 测试推荐使用独立的 PostgreSQL/PGVector 测试库；可通过
`VOICE_SHOPPING_TEST_DATABASE_URL` 指定连接地址。E2E 请求使用外层事务，测试结束后回滚。

LangGraph 使用 PostgreSQL Checkpointer 持久化每个工作流节点；业务层的跨轮事实另外投影到
`session_states.business_state`。默认复用 `VOICE_SHOPPING_DATABASE_URL`；如需单独的 checkpoint 数据库，设置
`VOICE_SHOPPING_LANGGRAPH_CHECKPOINT_DATABASE_URL`。在 Windows 上，上述启动器会让 Uvicorn
工作进程通过 psycopg 兼容的 Selector loop factory 创建事件循环；请不要直接用 `uvicorn` 命令替代它。
