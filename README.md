# 语音导购 Agents

基于 FastAPI、LangGraph、PostgreSQL/PGVector、Redis 和三个 Vue 应用实现的语音导购
电商 MVP。产品范围见 [需求文档](docs/requirements.md)，系统设计见
[架构文档](docs/architecture.md)，当前实现映射见
[实现说明](docs/implementation.md)。

## 已实现能力

- 用户端：启用店铺与在售商品浏览、文字/语音导购、流式商品卡与理由、待确认订单、
  订单确认/取消和本人订单列表。
- 商家端：按店主身份隔离的店铺/商品 CRUD、软删除、上下架、价格库存维护和本店订单。
- 平台端：全量商家/商品/订单总览，以及带禁用原因的商家启停。
- Agent：六类意图与多意图队列、品类动态槽位、每轮单问题澄清、PGVector 召回分数、
  `qwen3-rerank` 与静态/动态画像加权精排、逐商品理由、增量与完整话术合规检查。
- 语音：浏览器 PCM16 上行、`qwen-audio-3.0-asr-flash-streaming` 流式识别、
  `qwen-audio-3.0-tts-plus` 分片下行；未配置模型时自动降级到浏览器语音能力。
- 订单：十五分钟待确认、价格/商家/商品/库存二次校验、事务扣库存、幂等键和成交快照。
- 会话：`sessionId + turnId + seq` 事件排序、PostgreSQL 工作流状态恢复、Redis 一小时事件
  日志重放，以及可选 LangSmith Trace。

## 工程结构

```text
apps/
  api/             FastAPI 业务 API、LangGraph 工作流、双 WebSocket
  user-web/        用户端 Vue 应用（端口 5173）
  merchant-web/    商家端 Vue 应用（端口 5174）
  platform-web/    平台端 Vue 应用（端口 5175）
packages/web-ui/   三端共享组件、样式、API 类型与客户端
sql/               PostgreSQL + PGVector Schema 和演示数据
docs/              需求、架构和实现说明
```

## 本地启动

需要 Node.js、pnpm、Python 3.12+、uv 和 Docker。

```bash
pnpm install
uv sync --project apps/api
pnpm infra:up

# 分别在四个终端启动
pnpm dev:api
pnpm dev:user
pnpm dev:merchant
pnpm dev:platform
```

API 文档位于 `http://localhost:8000/docs`，三个前端分别位于
`http://localhost:5173`、`http://localhost:5174` 和 `http://localhost:5175`。
PostgreSQL 与 Redis 默认使用宿主机 `55432`、`56379` 端口。

复制 `.env.example` 为 `.env` 后可接入 DashScope 和 LangSmith。无 DashScope Key 时，
Agent 保持确定性可运行，语音端使用浏览器降级；配置 Key 后启用文档指定的文本、Embedding、
Reranker、ASR 和 TTS 模型。

## 演示身份

认证系统不在当前文档定义的首版接口中，MVP 使用请求头传递演示身份，服务端仍在每条商家 SQL
中校验数据归属：

- 用户默认 `X-User-ID: 00000000-0000-4000-8000-000000000101`
- 商家默认 `X-Merchant-Owner-ID: 00000000-0000-4000-8000-000000000002`
- 平台端当前仅用于本地演示，接入生产环境前必须增加认证与角色授权。

## WebSocket 协议

文本连接：`/ws/text/{session_id}?userId={user_id}`。客户端提交：

```json
{
  "type": "turn.submit",
  "turnId": "uuid",
  "utterance": "推荐一副通勤降噪耳机，预算一千元以内"
}
```

服务端依次推送 `flow.status`、`recommendation.cards`、`text.delta`、
`text.completed` 和可选 `order.updated`。断线后发送 `session.resume + turnId + afterSeq`
可从 Redis 重放遗漏事件。

音频连接：`/ws/audio/{session_id}?userId={user_id}`。上行顺序为 `audio.start`、PCM16
二进制分片、`audio.commit`；下行顺序为 `audio.start`、WAV 二进制分片、`audio.end`。

## 验证

```bash
pnpm typecheck
pnpm build
pnpm test:api
pnpm lint:api
docker compose config --quiet
```

第一版不包含支付、退款、物流、售后和真实电商平台对接。
