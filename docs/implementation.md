# POC 实现说明

本文记录需求与当前代码的对应关系，重点沉淀代码中已经形成的设计决策、状态边界、降级行为和验收方式。文档描述的是当前实现，不等同于生产部署方案。

## 1. 代码地图

| 领域 | 关键文件 | 实现职责 |
| --- | --- | --- |
| 图装配 | `apps/api/src/voice_shopping_api/agents/graph.py` | StateGraph 节点、条件路由和编译 |
| 图状态 | `agents/state.py` | TypedDict 状态契约、运行时依赖、持久化键筛选 |
| 意图 | `agents/nodes/intent.py` | 模型/关键词识别、品类规范化、订单安全守卫 |
| 澄清 | `agents/nodes/clarification.py` | 确定性槽位抽取、模型抽取、槽位校验和追问 |
| 推荐 | `agents/nodes/recommendation.py`、`agents/service.py` | SQL 召回、向量排序、Reranker 和画像二排 |
| 回复 | `agents/nodes/response.py`、`agents/model.py` | 逐卡理由、话术组装、流式发布和合规检查 |
| 会话服务 | `agents/service.py`、`modules/sessions/service.py` | turn 执行、状态投影、消息保存、画像收敛 |
| 品类/属性 | `core/taxonomy.py`、`modules/platform/router.py` | 动态品类、槽位枚举、Redis 快照缓存和商品属性校验 |
| 商品向量 | `core/product_embedding.py`、`core/embeddings.py` | 中文商品卡片文本、向量生成、归一化和降级 |
| 订单 | `modules/orders/service.py`、`modules/orders/router.py` | 幂等创建、锁行确认、库存事务和订单查询 |
| 实时语音 | `realtime/router.py`、`realtime/hub.py`、`realtime/asr.py`、`realtime/tts.py` | 双 WebSocket、事件广播/重放、ASR/TTS 适配 |
| 前端共享 | `packages/web-ui/src`、三个 `apps/*-web/src` | API 类型、共享组件和用户/商家/平台交互 |

## 2. 三端和 API 实现

### 2.1 用户端

- `GET /api/v1/catalog/merchants`：只返回未删除且启用的商家。
- `GET /api/v1/catalog/products`：只返回未删除、`on_sale`、有库存且商家启用的商品，支持商家、二级品类、名称/品牌查询。
- `POST /api/v1/catalog/behaviors`：当前只接受 `click`，更新动态画像。
- `GET /api/v1/orders/mine`、订单创建/确认/取消：只处理当前演示用户的订单。
- 用户端通过文本和音频 WebSocket 进入同一 `sessionId`，商品卡和逐卡理由可以在页面上实时填充。

### 2.2 商家端

- 店铺和商品 CRUD 的查询、更新、删除都带 owner 条件。
- 店铺删除将店铺及其未删除商品统一写入 `deleted_at`，不物理删除订单事实。
- 商品创建/更新从数据库重新读取二级品类槽位，校验父级、未知槽位、必填值和枚举值。
- 商品的名称、品类、品牌、描述、价格、属性或卖点变化时按需重新生成 embedding；仅改 SKU、库存、状态或图片不重算。
- 商家订单通过 `orders.merchant_id -> merchants.owner_user_id` 过滤。

### 2.3 平台端

- 一级分类可以独立创建和删除；仍有二级分类时不能删除。
- 二级分类必须选择已存在的一级分类；二级分类下单独管理槽位。
- 槽位 API 和 Pydantic 请求模型都要求至少一个非空枚举值，重复枚举会在请求层去重。
- 平台可以查看/启停商家、查看全量商品/订单，并调用同步的 `/platform/products/embeddings/rebuild` 重建商品向量。

## 3. Agent 工作流实现

### 3.1 当前图

实际执行顺序为：

```text
START
  -> clarification_agent          (存在 pending_question)
  -> intent_agent                 (无 pending_question)
     -> clarification_agent       (PRODUCT_RECOMMENDATION)
        -> recommendation_agent   (clarification_status=READY)
        -> emotional_agent         (clarification_status=ASK)
     -> recommendation_agent      (PRODUCT_COMPARE / PRODUCT_QUERY)
     -> order_node                (PRODUCT_ORDER)
     -> emotional_agent            (CHAT / UNSUPPORTED_REQUEST)
   -> emotional_agent
   -> compliance_node           (检查、兜底并发布安全回复)
   -> END
```

存在 `pending_question` 时，图会从 `START` 直接进入澄清节点，把本轮话语当作上一问题的回答处理，不会再次调用意图识别。没有待回答问题的对话才进入意图节点。`recommendation_agent` 和 `order_node` 都是图节点，订单事务由 context 中的 `order_handler` 执行。

### 3.2 状态契约

`StateGraph` 使用三个显式 schema，`ShoppingState` 只作为图内完整状态：

- `ShoppingInputState` 作为 `input_schema`，承载当前轮请求、会话业务事实、当前 taxonomy 和服务层加载的推荐输入。
- `ShoppingState` 作为内部 state schema，承载节点之间需要合并的全部扁平键。
- `ShoppingOutputState` 作为 `output_schema`，只暴露对话进度、商品卡、订单/合规结果和最终安全回复。

`ShoppingRuntimeDependencies` 作为 `context_schema` 注入 catalog loader、订单 handler 和各类发布器。候选商品、taxonomy、用户画像快照、模型开关和 draft speech 不属于图输出；当前品类的 `required_slots` / `allowed_slots` 会保留在输出中，支持无 checkpointer 的多轮调用。

`ShoppingState` 继续通过以下 TypedDict mixin 说明字段生命周期：

- `TurnState`：`session_id`、`turn_id`、`user_id`、当前 `utterance`、最近 6 条消息、`model_enabled`。
- `ConversationState`：`intent`、`product_category`、`category_changed`、`required_slots`、`allowed_slots`、`slots`、澄清状态和 `pending_question`。
- `TaxonomyState`：本轮从数据库加载的所有品类、槽位、枚举和问题文案；不持久化。
- `ProfileState`：会话内 `user_profile_updates`。
- `RecommendationState`：只读画像快照、候选商品、当前/上一轮商品卡和情绪风格。
- `OrderState`：本轮订单结果；待确认订单详情仍以 `orders` 为事实源。
- `ResponseState`：理由、完整回复、违规短句、合规状态以及是否已经流式发送的标记。

`ShoppingRuntimeDependencies` 注入数据库商品 loader、订单 handler、逐卡理由发布器、话术增量发布器和 TTS 短句发布器。节点因此可以在测试中脱离 FastAPI 和数据库连接运行。

### 3.3 模型开关和确定性降级

`process_turn()` 用 `bool(settings.dashscope_api_key)` 设置 `model_enabled`：

- 开启时使用 DashScope ChatQwen、Embedding 和 Reranker。意图识别、槽位抽取和单商品推荐理由通过 LangChain `with_structured_output()` 分别绑定 `IntentResult`、`SlotExtractionResult` 和 `ProductReason`；三类结果在模型边界直接完成 Pydantic 校验，Agent 模型禁止额外字段。情感回复仍保留 JSON mode，因为它需要兼容增量话术和短句发布。
- 未开启或模型调用异常时，意图节点回退关键词位置选择，澄清节点回退规则抽取，推荐节点回退确定性排序，理由节点回退固定模板。
- 模型失败只影响当前能力，不会让商品事实、订单事务或会话持久化绕过服务端校验。

## 4. 意图和需求澄清

### 4.1 意图节点

当没有 `pending_question` 时，`recognize_intent()` 会识别新一轮请求。存在待回答问题时，工作流直接复用已有品类和槽位上下文进入需求澄清节点，避免把“机械的吧”这类槽位回答误判为 `CHAT`。意图模型提示词由 `service._taxonomy_context()` 生成，包含当前数据库中的所有二级品类和槽位定义。

识别结果经过 `_finalize_intent()` 再进入路由：

- 统一大写/别名为 `CATEGORY_ALIASES` 中的标准二级品类。
- 记录 `category_changed` 和 `starts_new_product_request`。
- 选定一个具体下单请求时优先于品类词推荐。
- 新品类或明确新购买请求会清掉旧品类槽位。
- `PRODUCT_ORDER + CREATE` 必须有上一轮商品卡；没有卡、品类变了且没有明确目标，或用户明显是在开始新推荐时，会安全降级为推荐意图。
- 确认只在存在待确认订单且用户使用确认表达时产生；没有待确认订单时不会伪造确认动作。

### 4.2 澄清节点

澄清节点的数据来源优先级为：数据库当前品类槽位定义、应用内 canonical 定义、确定性抽取、模型抽取。最终写入前统一经过 `_validated_agent_slots()`：

- 丢弃不在允许集合中的键。
- 枚举值必须在定义的 `values` 中。
- 布尔值必须是真正的 JSON boolean。
- 数值必须满足最小值且不能是 boolean。
- 文本槽位必须是非空字符串。

确定性抽取覆盖预算、布尔需求、耳机形态/连接方式/续航、咖啡机和水壶参数、跑鞋尺码/路面/足型、手表机芯/材质/防水、口红色调/妆效/肤质等常见表达。模型补充抽取只接收当前话语、已有槽位、最多两个 pending 槽位和标准定义，不能写入未定义值。

节点返回：

```json
{
  "required_slots": ["form", "connectivity"],
  "slots": {"form": "over-ear"},
  "clarification_status": "ASK",
  "missing_slots": ["connectivity"],
  "pending_question": {
    "slot": "connectivity",
    "slots": ["connectivity"],
    "question": "你希望使用蓝牙还是有线连接？"
  }
}
```

当缺失槽位为空时返回 `READY`，并将 `pending_question` 置为 `null`。`budgetMax` 只参与当前会话的商品过滤，不进入品类必填列表或静态画像。

## 5. 商品召回、排序和商品卡

### 5.1 硬过滤 SQL

`agents/service.py::_build_catalog_query()` 动态拼接一条参数化 SQL，条件包括：

```text
p.deleted_at IS NULL
p.status = 'on_sale'
p.stock > 0
m.deleted_at IS NULL
m.is_enabled
```

然后追加品类、预算和每个已填槽位。槽位键来自平台 taxonomy，可信键名内联到 SQL；槽位值全部作为参数传递。

匹配器的当前语义：

| 槽位 | SQL 语义 |
| --- | --- |
| 默认枚举/数组 | JSONB `@>`，标量等值、数组包含 |
| 布尔 | 将值序列化为 JSON `true/false` 后做包含比较 |
| `gender` | 商品 `unisex` 或等于用户值 |
| `size` | 优先商品 `size`；双元素数组按范围，其他数组按包含，标量按等值；无 `size` 时回退 `sizeRange` 范围 |
| `batteryHours`、`pressureBar`、`waterTankMl`、`capacityL` | 商品值 `>=` 用户要求 |
| `waterResistance` | 从商品值中提取数字后比较 `>=` |
| `budgetMax` | 当前会话内商品价格 `<=` 预算 |

向量可用时追加 `p.embedding IS NOT NULL`，按 `p.embedding <=> CAST(:embedding AS vector)` 排序并 `LIMIT 20`。查询向量生成失败或模型关闭时不加 embedding 条件，按 `created_at DESC` 排序，含 NULL embedding 商品。

### 5.2 两阶段精排

第一阶段使用 `qwen3-rerank` 对候选商品事实文本打分，分数裁剪到 `[0, 1]` 并取 Top 3；Reranker 失败时使用：

```text
lexicalScore = min(1.0, 0.52 + 命中关键词数量 * 0.1)
```

第二阶段在 Top 3 内按画像快照增加规则分：

```text
brandAffinity[product.brand] > 0       +0.20
product.price > 1.5 * avgOrderAmount   -0.15
product.id in recentPurchased          -0.30
```

最终 `matchScore = min(1.0, rerankerScore + ruleScore)`，因此对外展示的匹配度最高为 100%；`scoreBreakdown` 保存 `reranker` 和命中的规则项。规则分相同依赖 Python 稳定排序保持第一阶段顺序。没有画像时不增加或扣减规则分。

输出卡片字段包括 `productId`、`merchantId`、`merchantName`、`sku`、`name`、`categoryL1`、`categoryL2`、`brand`、`description`、`price`、`stock`、`imageUrl`、`imageUrls`、`status`、`createdAt`、`updatedAt`、`sellingPoints`、`attributes`、`matchScore` 和 `scoreBreakdown`。这些字段均来自后端商品事实，推荐节点不生成理由。

`PRODUCT_COMPARE` 和 `PRODUCT_QUERY` 有上一轮卡片时直接复用最多 3 张；查询意图优先选择用户话语中提到的商品名称，否则选择第一张。对比/查询的情绪风格为 `analytical-professional`，有商品推荐时为 `warm-professional`，无结果时为 `helpful-apologetic`。

## 6. 商品向量实现

`core/product_embedding.py` 先生成用户视角的中文卡片文本：

```text
商品；品类；品牌；卖点；描述；属性；价格带
```

英文代码通过 `CATEGORY_*_LABELS`、`ATTRIBUTE_KEY_LABELS` 和 `ATTRIBUTE_VALUE_LABELS` 映射为中文，数值属性追加单位，价格只写入价格带而不写入精确价格。这样用户的口语需求和商品文本共享更接近的语义空间。

`core/embeddings.py` 调用 Embedding API 后执行单位归一化。商品向量按 `embedding_model + 商品卡片文本 SHA-256` 写入 Redis，创建、更新和批量重建共用同一缓存；模型或商品卡片变化会自动使用新键。Redis 故障或坏值会回退到模型调用，商品向量失败时写 NULL 并记录 warning；查询向量失败时走 `created_at` 召回降级。平台重建接口当前同步遍历未删除商品，返回 `total/updated/cacheHits/generated/failed`。

## 7. 理由、话术和合规

当前工作流真正使用的是逐商品理由路径：

1. `emotional_response()` 为每张卡调用一次 `generate_product_reason()`。
2. 最多并发 3 个理由请求，模型返回的 `product_id` 必须和输入卡片一致，理由不能为空；每条理由
   最终统一为“第 N 款（商品名称）”开头，不能只使用“这款/该款”等无指向代词。
3. 单卡模型失败、返回商品 ID 不一致、理由不合规或无法补全商品身份时，只对该卡使用确定性 fallback。
4. 通过校验的理由通过 `reason_publisher` 按 `productId` 增量推送。
5. 情感应答先从全部商品卡提取能够唯一对应一件商品的价格、属性或卖点差异，再生成选择钩子；
   续航等有明确“越大越好”语义的数值属性只推荐唯一最大值，不能把较小值当成偏好选项；同一
   商品可以同时对应多个不同条件。共同条件不会分别推荐多件商品，资料不足时会明确说明。模型
   失败、钩子不合规或未按已验证差异引用商品时，使用同一比较计划的确定性 fallback。
6. `_build_speech()` 将逐商品理由和选择钩子组装为完整话术；当前不是让模型一次性生成完整业务话术。
7. `_build_speech()` 只负责生成完整话术；`compliance_node()` 在同一节点内通过 `split_sentences()` 拆成短句逐一检查。
8. 全部短句通过时，`compliance_node` 通过 `speech_delta_publisher` 每 12 个字符切片，并通过 `speech_sentence_publisher` 按标点发送 TTS。
9. 任一短句命中正则后，`compliance_node` 清空理由并替换 `speech_text`、`final_reply` 为 `COMPLIANCE_FALLBACK`，然后只发布固定违规提示，原始话术不会在检查和发布之间泄露。

`state_events()` 会根据 `reasons_streamed` 和 `speech_streamed` 避免在流式已发送后重复生成历史增量；最终始终发送 `text.completed`。

当前禁用正则包括：百分百、绝对有效/安全、包治、国家级、稳赚不赔。规则集中在 `agents/nodes/constants.py`，不是数据库配置。

## 8. 订单实现

### 8.1 创建

`create_pending_order()` 先按幂等键查找已有订单：同一用户直接返回原订单，其他用户复用该键返回冲突。新订单通过商品和商家联合查询验证商品可售、商家启用和库存，并保存：

- `unit_price`、数量和数据库生成的 `total_amount`。
- `merchant_snapshot`：商家 ID 和名称。
- `product_snapshot`：商品 ID、SKU、名称、品类和图片。
- 幂等键和 15 分钟 `expires_at`；语音导购创建的订单额外保存 `session_id`、`source_turn_id`，目录直购保持为空。

创建不扣库存，只产生 `pending` 订单。

### 8.2 确认/取消

`confirm_order()`：

1. `SELECT ... FOR UPDATE` 锁定订单并校验用户归属。
2. 已经是终态时直接返回，保持幂等。
3. `FOR UPDATE OF p, m` 锁定商品和商家。
4. 依次检查超时、删除/启停状态、价格变化和库存。
5. 失败时订单改为 `fail`，写入 `confirmation_timeout`、`product_unavailable`、`price_changed` 或 `insufficient_stock`。
6. 成功时原子扣减库存，更新订单为 `success`，然后在同一数据库事务内更新动态画像。

`cancel_order()` 只更新当前用户的 `pending` 订单；重复取消会读取并返回当前状态。数据库触发器保证成功或失败后的终态不能迁移到另一状态。

## 9. 画像和会话收敛

### 9.1 动态画像

`update_profiles()` 只处理 `click` 和 `order`：

- 点击增加品类和品牌偏好 `0.1`，更新 `recent_viewed`。
- 成功订单增加偏好 `0.32`，更新 `recent_purchased`。
- 画像行使用 `FOR UPDATE` 后 read-modify-write，避免同一用户并发事件覆盖增量。
- 最近列表去重并保留最近 20 项。
- 成功订单重新查询所有成功订单的平均金额。

### 9.2 静态画像候选

`extract_static_profile_candidates()` 从话语提取年龄、身高、体重、城市、明确性别和技术熟练度，并从已确认槽位提取肤质。`budgetMax` 始终保留为当前会话的商品筛选槽位，不会并入静态画像。`merge_static_profile_patches()` 按“旧会话候选 -> 当前话语 -> 显式 profile”顺序合并，`normalize_static_profile_patch()` 丢弃超出数据库约束的值。

`finalize_session_profile()` 在以下时机运行：

- `POST /api/v1/sessions/{sessionId}/close`。
- 文本 WebSocket `session.close`。
- 用户页面的所有文本/音频连接都断开。
- 订单确认或取消使订单进入终态。

显式关闭和订单终态会关闭 `sessions`；普通断开只做最佳努力画像收敛，避免短暂重连立即关闭会话。

### 9.3 业务状态投影

`state_for_persistence()` 只保留以下键：

```text
product_category
slots
user_profile_updates
pending_question
product_cards
```

`intent`、taxonomy 上下文、候选商品、画像快照、模型开关、理由/回复文本和完整订单详情不会进入 `session_states.business_state`。待确认订单由 `orders` 查询，状态投影只保存 `pending_order_id` 引用。

LangGraph Checkpointer 开启时，`process_turn()` 以稳定 session UUID 作为 `thread_id`，优先恢复完整图状态；没有 checkpoint 时读取 `session_states` 最新投影。数据库演进由
`apps/api/scripts/migrate.py` 和 `sql/migrations/` 管理，`sql/schema.sql` 是当前初始 schema 快照。旧的
`workflow_state`、taxonomy 命名和画像表在迁移中转换；无法无损映射的旧画像字段保留在
`legacy_user_static_profiles` / `legacy_user_dynamic_profiles` 供审计。

## 10. WebSocket、事件顺序和重放

### 10.1 文本协议

文本端点：`/ws/text/{session_id}?token={access_token}`；服务端在接受连接前校验 customer JWT。

客户端发送：

```json
{
  "type": "turn.submit",
  "turnId": "uuid-or-client-id",
  "utterance": "推荐一副通勤降噪耳机"
}
```

也支持：

- `session.resume`：`turnId + afterSeq`，从 Hub 内存 journal 或 Redis 事件列表补发文本事件。
- `session.close`：可带显式 profile patch，返回 `session.closed`。

所有服务端 JSON 控制事件使用 `type`、`sessionId`、`turnId`、`seq`、`payload` 五字段；连接/会话级事件使用 `turnId="session"`、`seq=0`。服务端事件：

```text
session.connected
flow.status
recommendation.cards
text.delta(scope=reason, productId=...)
text.delta(scope=speech)
text.completed
order.updated
flow.error
flow.status(status=completed)
```

LangGraph 使用 `stream_mode=["updates", "tasks"]`：`tasks` 的节点开始事件用于发送真实的 Agent 运行状态；`updates` 的推荐节点结果用于尽早发送商品卡。`state_events()` 在无实时回调的 HTTP/测试场景生成同样的完整事件序列。

每个会话有独立 `asyncio.Lock`，事件发布使用锁保护 `seq` 递增。文本事件内存最多 300 条，Redis key 为 `voice-shopping:events:{session_id}`，最多 300 条、TTL 3600 秒。

### 10.2 音频协议

输入顺序：

```text
audio.start(turnId)
PCM16 binary frames, 16 kHz
audio.commit(turnId, clientMetrics)
```

服务端 ASR 按标点通过 `asr.sentence` 推送中间完整句，提交后发送 `asr.completed`。`audio.cancel` 会停止当前识别并清理缓冲。

输出顺序：每个话术短句发送 `audio.start`、一个或多个 WAV 二进制分片、`audio.end`；全部短句完成后发送 `audio.done`。DashScope TTS 使用 24 kHz WAV；TTS 没有产出时使用 16 kHz 静音 WAV 触发客户端浏览器语音 fallback。音频消息不写入 Redis，不能通过 `session.resume` 恢复二进制。

### 10.3 商家与商品目录缓存

`core/catalog_cache.py` 对用户可见目录、商家自有店铺/商品和平台全量商家/商品列表执行
Redis read-through 缓存。每个条目使用 `voice-shopping:cache:catalog:v{revision}:...` key，默认
TTL 为 60 秒；商家、店铺或商品创建、更新、删除、启停，或者订单成功扣库存后，业务路径会在
PostgreSQL 提交成功后递增 revision。Redis 不可用或缓存值无效时自动回退到数据库查询。

目录缓存只用于展示列表，绝不参与订单确认、库存扣减、价格复核、会话恢复或权限判断。可通过
`VOICE_SHOPPING_CATALOG_CACHE_ENABLED`、`VOICE_SHOPPING_CATALOG_CACHE_TTL_SECONDS` 和可选的
`VOICE_SHOPPING_CATALOG_CACHE_REDIS_URL` 配置开关、TTL 和独立 Redis 实例。

## 11. 数据库和索引关键点

- `merchants`、`products` 使用 `deleted_at` 软删除；启用状态和删除状态共同决定用户可见性。
- `category_l2.category_l1_id` 外键保证父级存在，`category_slots` 的 JSONB 约束保证枚举数组非空。
- `products.attributes` 使用 JSONB；GIN 索引服务属性包含查询，HNSW 部分索引服务非空向量余弦查询。
- `orders` 具有用户/商家/商品联合外键、幂等键唯一约束、15 分钟有效期约束和终态迁移触发器。
- `sessions` 通过 `(id, user_id)` 唯一组合和订单组合外键保证会话与用户一致。
- `session_states` 通过 `(session_id, turn_id)` 唯一约束保证同一轮投影可重复写入。
- `session_messages` 通过 `(session_id, turn_id, seq)` 唯一约束保证消息写入幂等。
- `user_profile_static` 和 `user_profile_dynamic` 分离存储；静态资料有年龄、身高和体重范围约束，动态画像有 JSONB 对象、分数和平均订单金额约束。

## 12. 可观测性

`core/observability.py` 提供 fail-open 的手动 trace helper：

- Chat、Embedding、Reranker、ASR、TTS 分别标记 provider、模型名和 operation。
- 保存 request ID、耗时、结果数量、Token/计费 usage、音频字节数、句子数等指标。
- `process_turn()` 的 LangGraph run 使用 session UUID 作为 `thread_id`，metadata 附带 turn、环境和模型配置。
- 追踪异常只写 debug 日志，不让 LangSmith 故障影响商品、订单或会话请求。

当前 trace 可能包含模型输入、Embedding 文本、ASR transcript 和 TTS 文本；生产接入前需要增加脱敏和访问保留策略。

## 13. 测试和验证

关键测试覆盖：

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test_agents.py` | 意图优先级、订单守卫、品类切换、澄清恢复、逐卡理由、合规和状态持久化 |
| `test_catalog_query.py` | 可见性、预算、布尔/数值/尺码/防水/枚举 SQL 条件和向量降级 |
| `test_recommendation_e2e.py` | 多品类硬过滤、PGVector 召回、Reranker/画像排序、冷启动和禁用商家 |
| `test_recommendation_rules.py` | 画像二排分数和词法 Reranker fallback |
| `test_taxonomy.py`、`test_taxonomy_cache.py` | 父级分类、槽位枚举、商品属性校验、缓存命中和失效 |
| `test_product_embedding.py` | 中文向量卡片、单位归一化、标签/单位/价格带渲染 |
| `test_profile_lifecycle.py` | 静态画像候选、显式覆盖、空值保护和持久化键筛选 |
| `test_speech.py` | ASR 句子切分、TTS 分片、fallback 和安全 usage 统计 |
| `test_model_adapters.py` | Chat/Embedding/Reranker/usage 适配 |
| `test_observability.py` | LangSmith fail-open 和 span 字段保留 |

仓库根目录验证命令：

```bash
pnpm typecheck
pnpm build
pnpm test:api
pnpm lint:api
```

API 子项目也可以直接运行：

```bash
uv run --project apps/api pytest
uv run --project apps/api ruff check .
```

## 14. 仍需生产化的边界

- 请求头演示身份必须替换为真实认证、角色授权和 WebSocket 鉴权。
- 品类槽位和合规词规则当前是业务配置/应用常量的混合形态，需要统一配置管理和审计。
- 向量重建当前同步执行，数据量增大后应改为后台任务、批量调用和重试。
- Redis 重放只覆盖 1 小时和 300 条文本事件，不能替代长期审计，也不支持音频断点续播。
- LangSmith 生产追踪需要脱敏、访问控制、成本告警和保留周期。
- 当前版本不实现支付、退款、物流、售后和真实电商平台对接。
