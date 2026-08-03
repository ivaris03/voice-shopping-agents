# Voice Shopping API

FastAPI 模块化单体，包含用户/商家/平台 HTTP API、订单事务、画像更新、LangGraph 四节点
工作流、合规过滤、DashScope 模型适配、文本/音频 WebSocket、Redis 重放和 LangSmith 元数据。

从仓库根目录运行：

```bash
uv sync --project apps/api
uv run --project apps/api uvicorn voice_shopping_api.main:app --reload --port 8000
uv run --project apps/api pytest
uv run --project apps/api ruff check .
```

LangGraph 使用 PostgreSQL Checkpointer 持久化每个工作流节点；业务层的跨轮事实另外投影到
`session_states.business_state`。默认复用 `VOICE_SHOPPING_DATABASE_URL`；如需单独的 checkpoint 数据库，设置
`VOICE_SHOPPING_LANGGRAPH_CHECKPOINT_DATABASE_URL`。在 Windows 上，异步 psycopg 需要
Selector event loop，因此开发环境请保留上述 `--reload` 参数。
