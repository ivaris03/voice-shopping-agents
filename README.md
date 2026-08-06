# 声选 Agent

基于 FastAPI、LangGraph、PostgreSQL/PGVector、Redis 和三个 Vue 应用实现的语音导购
电商 POC（概念验证）。项目聚焦验证从需求理解、商品推荐到待确认下单的端到端链路；产品范围见 [需求文档](docs/requirements.md)，系统设计见
[架构文档](docs/architecture.md)，当前实现映射见
[实现说明](docs/implementation.md)，后续开发拆解见
[任务文档](docs/tasks.md)。

## 演示视频

[观看演示视频（MP4，165 MB）](resources/video/demo.mp4)

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [需求文档](docs/requirements.md) | POC 验证范围、Agent 契约和验证口径 |
| [架构文档](docs/architecture.md) | 系统模块、数据边界、工作流和技术选型 |
| [实现说明](docs/implementation.md) | 代码地图、关键实现、降级策略和测试覆盖 |
| [接口文档](docs/api.md) | HTTP API、请求/响应结构和 WebSocket 协议 |
| [任务文档](docs/tasks.md) | POC 验证基线、产品化任务、依赖和验收标准 |
| [部署文档](deploy/README.md) | Docker、Nginx、HTTPS 和 GitHub Actions 部署 |

## 已实现能力

- 用户端：启用店铺与在售商品浏览、文字/语音导购、流式商品卡与理由、待确认订单、
  订单确认/取消和本人订单列表。
- 商家端：按店主身份隔离的店铺/商品 CRUD、软删除、上下架、价格库存维护和本店订单。
- 平台端：全量商家/商品/订单总览，以及带禁用原因的商家启停。
- Agent：六类单意图、品类动态槽位、每轮最多两个问题澄清、PGVector 召回分数、
  `qwen3-rerank` 与静态/动态画像加权精排、逐商品理由、增量与短句合规检查。
- 语音：浏览器 PCM16 上行、`qwen-audio-3.0-asr-flash-streaming` 流式识别、
  `qwen-audio-3.0-tts-plus` 分片下行；文本 Agent 和 TTS 未配置模型时可确定性降级，
  服务端 ASR 仍需要可用的 ASR 模型。
- 订单：十五分钟待确认、价格/商家/商品/库存二次校验、事务扣库存、幂等键和成交快照。
- 目录缓存：Redis 缓存用户、商家和平台端的商家/店铺/商品列表；写入成功后递增目录版本，
  库存扣减也会失效相关列表，订单确认仍直接复核数据库。
- 会话：`sessionId + turnId + seq` 事件排序、LangGraph Checkpointer 工作流恢复、
  `session_states` 业务状态投影、Redis 一小时事件日志重放，以及可选 LangSmith Trace。
- 品类：一级/二级品类及槽位快照缓存到 Redis；平台 taxonomy 写入成功后立即失效，
  Redis 不可用时自动回退 PostgreSQL。

## 当前状态

当前版本是可本地运行的电商导购 POC，用于验证核心导购、语音和待确认订单链路，不是生产部署版本。若 POC 验证通过并决定产品化，至少需要接入外部身份提供方、补齐令牌刷新与撤销、多设备会话和细粒度授权，并完成异步化商品向量重建、敏感信息治理和多实例实时可靠性。完整任务拆解见 [任务文档](docs/tasks.md)。

当前明确不包含支付、退款、物流、售后和真实电商平台对接。

## 工程结构

```text
apps/
  api/             FastAPI 业务 API、LangGraph 工作流、双 WebSocket
  user-web/        用户端 Vue 应用（端口 5173）
  merchant-web/    商家端 Vue 应用（端口 5174）
  platform-web/    平台端 Vue 应用（端口 5175）
packages/web-ui/   三端共享组件、样式、API 类型与客户端
sql/               PostgreSQL + PGVector Schema 和演示数据
docs/              需求、架构、实现和任务文档
```

## 本地启动

需要 Node.js、pnpm、Python 3.12+、uv，以及本机已有的 PostgreSQL 16 + PGVector 和
Redis。项目直接连接宿主机的 `5432`、`6379` 端口，不会另外创建基础设施容器。

```bash
pnpm install
uv sync --project apps/api

# 分别在四个终端启动
pnpm dev:api
pnpm dev:user
pnpm dev:merchant
pnpm dev:platform
```

API 文档位于 `http://localhost:8000/docs`，三个前端分别位于
`http://localhost:5173`、`http://localhost:5174` 和 `http://localhost:5175`。
PostgreSQL 与 Redis 默认使用宿主机 `5432`、`6379` 端口。首次使用空数据库时，先创建
`voice-shopping-agents` 数据库，再执行版本化迁移；本地演示附带播种数据：

```bash
pnpm db:migrate --seed-demo
```

已有数据库同样执行 `pnpm db:migrate`。迁移记录及校验和保存在
`voice_shopping_schema_migrations`，已经应用的脚本不可改写；当前 schema 快照仍保留在
`sql/schema.sql` 供审阅和历史参考。

E2E 使用必须与应用库不同的 `VOICE_SHOPPING_TEST_DATABASE_URL`。复制 `.env.example` 后运行：

```bash
pnpm db:prepare-e2e
pnpm test:e2e
```

测试运行时会反复重建这个库的 `public` schema，因此不能指向任何含业务数据的数据库。

复制 `.env.example` 为 `.env` 后可接入 DashScope 和 LangSmith。无 DashScope Key 时，
文本 Agent 保持确定性可运行，TTS 使用浏览器语音降级；服务端 ASR、Embedding、Reranker
和模型生成的文本/TTS 需要配置对应能力后才会启用。

## POC 登录

POC 复用现有 `users` 表的手机号和密码哈希验证登录，并签发短期 JWT。用户、商家和平台端先通过
`POST /api/v1/auth/login` 登录，再以 `Authorization: Bearer <access-token>` 调用受保护接口。
现有演示数据的密码均为 `12345678`。三端登录页会分别预填用户端 `13700000001`、商家端
`13800000001` 和平台端 `13900000001`。演示账号如下：

| 端 | 账号 | 手机号 | 密码 |
| --- | --- | --- | --- |
| 用户端 | 小林 | `13700000001` | `12345678` |
| 用户端 | 陈晨 | `13700000002` | `12345678` |
| 用户端 | 爱丽丝 | `13700000003` | `12345678` |
| 用户端 | 大卫 | `13700000004` | `12345678` |
| 用户端 | 埃里克 | `13700000005` | `12345678` |
| 商家端 | 声选音频商家 | `13800000001` | `12345678` |
| 商家端 | 声选家电商家 | `13800000002` | `12345678` |
| 商家端 | 声选运动商家 | `13800000003` | `12345678` |
| 商家端 | 声选腕表商家 | `13800000004` | `12345678` |
| 商家端 | 声选美妆商家 | `13800000005` | `12345678` |
| 平台端 | 平台管理员 | `13900000001` | `12345678` |

该实现不新增用户或权限表，适合本地 POC，不包含 refresh token、服务端注销或多设备会话管理。

## WebSocket 协议

文本连接：`/ws/text/{session_id}?token={access_token}`。客户端提交：

```json
{
  "type": "turn.submit",
  "turnId": "uuid",
  "utterance": "推荐一副通勤降噪耳机，预算一千元以内"
}
```

服务端依次推送 `flow.status`、`recommendation.cards`、`text.delta`、
`text.completed` 和可选 `order.updated`。工作流节点开始执行时，`flow.status` 的
`payload` 会带上实际 `node` 和用户提示 `label`（例如
`clarification_agent` / `需求澄清 Agent 运行中`），前端可据此同步当前 Agent。
断线后发送 `session.resume + turnId + afterSeq` 可从 Redis 重放遗漏事件。

音频连接：`/ws/audio/{session_id}?token={access_token}`。上行顺序为 `audio.start`、PCM16
二进制分片、`audio.commit`；ASR 会持续发送携带完整当前转录的 `asr.partial`，并在句子
结束时发送 `asr.sentence`，录音提交时再发送 `asr.completed`。TTS 下行按情感应答生成的短句即时重复发送
`audio.start`、该句的 WAV 二进制分片、`audio.end`，全部短句完成后发送 `audio.done`；
控制消息中的 `sentenceIndex` 和 `sentenceCount` 用于标识顺序。

## 验证

```bash
pnpm typecheck
pnpm build
pnpm test:api
pnpm lint:api
```

API 集成测试依赖本机 PostgreSQL/PGVector 和 Redis；未配置 DashScope Key 时，确定性 Agent 测试仍可运行，依赖真实模型的 ASR、Embedding、Reranker 和 TTS 测试需要相应配置。Windows/Python 版本差异导致的 asyncpg 事件循环问题属于测试环境治理事项，见 [任务文档](docs/tasks.md) 的 `TASK-007`。
