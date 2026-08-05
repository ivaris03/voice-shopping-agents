# API 接口文档

本文档描述当前代码已经实现的 HTTP API 和文本/音频 WebSocket 协议。接口的运行时 OpenAPI 文档由 FastAPI 自动生成：

- Swagger UI：`http://localhost:8000/docs`
- ReDoc：`http://localhost:8000/redoc`
- OpenAPI JSON：`http://localhost:8000/openapi.json`

本文档面向当前 POC，用于本地验证。当前版本使用既有账号登录后签发短期 JWT；生产环境仍需补充 refresh token、服务端注销、限流和敏感数据治理。

## 1. 基础约定

### 1.1 地址和命名

HTTP API 的基础前缀是 `/api/v1`；健康检查位于前缀之外的 `/health`。请求体和响应体使用 JSON，JSON 字段使用 camelCase，路径和查询参数以接口定义为准。

除非特别说明：

- UUID 使用标准字符串格式，例如 `00000000-0000-4000-8000-000000000101`。
- 时间使用 ISO 8601，带时区，例如 `2026-08-03T12:00:00Z`。
- 金额和其他 `Decimal` 字段以 JSON 字符串返回，例如 `"699.00"`，避免浮点精度丢失。
- 列表响应统一使用 `{ "items": [...] }`。
- 删除成功返回 `204 No Content`，响应体为空。

### 1.2 JWT 登录与角色

先调用 `POST /api/v1/auth/login`，使用既有 `users` 账号的手机号和密码获取 access token。除公开目录浏览和健康检查外，HTTP 请求都需要：

```http
Authorization: Bearer <access-token>
```

JWT 的 `sub` 是现有用户 UUID，角色来自登录时的 `users.role`：`customer` 只能访问自己的订单、画像和会话；`merchant` 只能访问其 `owner_user_id` 对应的店铺和商品；`platform` 才能访问平台接口。演示数据的初始密码均为 `12345678`。

#### `POST /api/v1/auth/login`

```json
{
  "phone": "13900000101",
  "password": "12345678"
}
```

成功响应：

```json
{
  "accessToken": "<jwt>",
  "tokenType": "bearer",
  "expiresIn": 7200,
  "user": {
    "id": "00000000-0000-4000-8000-000000000101",
    "email": "lin@example.com",
    "displayName": "小林",
    "role": "customer"
  }
}
```

`GET /api/v1/auth/me` 返回当前 JWT 的用户信息。

### 1.3 错误格式

请求体、路径参数或查询参数不符合 Pydantic 约束时返回 `422`，格式由 FastAPI 提供：

```json
{
  "detail": [
    {
      "type": "greater_than",
      "loc": ["body", "quantity"],
      "msg": "Input should be greater than 0",
      "input": 0
    }
  ]
}
```

业务异常使用单条 detail：

```json
{
  "detail": "商品不可售或库存不足"
}
```

常见业务状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 查询、更新或业务操作成功 |
| `201` | 资源创建成功 |
| `202` | 请求已接受，行为上报入口处理成功 |
| `204` | 删除成功，无响应体 |
| `401` | 未登录、JWT 无效或 JWT 已过期 |
| `403` | 已登录但角色不匹配 |
| `404` | 资源不存在，或当前身份无权看到该资源 |
| `409` | 幂等键、SKU、slug、分类或槽位冲突 |
| `422` | 请求参数、请求体或跨表业务校验失败 |

## 2. 主要数据结构

### 2.1 商品 `ProductOut`

```json
{
  "id": "20000000-0000-4000-8000-000000000001",
  "merchantId": "10000000-0000-4000-8000-000000000001",
  "merchantName": "声选 · 通勤音频",
  "sku": "AUD-001",
  "name": "Sony WH-CH720N 无线降噪头戴耳机",
  "categoryL1": "ELECTRONICS",
  "categoryL2": "HEADPHONES",
  "brand": "Sony",
  "description": "真实公开型号；结构化规格已按平台筛选枚举归一化。",
  "price": "799.00",
  "stock": 31,
  "attributes": {"form": "over-ear", "noiseCancellation": true},
  "sellingPoints": ["主动降噪"],
  "imageUrls": ["https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=1200&q=80"],
  "status": "on_sale",
  "createdAt": "2026-08-03T12:00:00Z",
  "updatedAt": "2026-08-03T12:00:00Z"
}
```

`status` 可取 `draft`、`on_sale`、`off_sale`。用户端商品列表只返回未删除、在售、有库存且商家启用的商品。

### 2.2 商家 `MerchantOut`

字段包括：`id`、`ownerUserId`、`ownerDisplayName`、`name`、`slug`、`description`、`logoUrl`、`contactPhone`、`isEnabled`、`disabledReason`、`productCount`、`createdAt`、`updatedAt`。其中 `ownerDisplayName` 主要由平台商家列表提供。

创建商家店铺时：

```json
{
  "name": "声选 · 通勤音频",
  "slug": "sound-digital",
  "description": "本地演示集合店，展示通勤音频的真实品牌型号。",
  "logoUrl": null,
  "contactPhone": "13800000002"
}
```

`slug` 只能使用小写字母、数字和连字符，并符合 `^[a-z0-9]+(?:-[a-z0-9]+)*$`。

### 2.3 订单 `OrderOut`

订单响应字段包括：`id`、`userId`、`merchantId`、`productId`、`status`、`quantity`、`unitPrice`、`totalAmount`、`merchantSnapshot`、`productSnapshot`、`failureReason`、`expiresAt`、`confirmedAt`、`createdAt`、`updatedAt`。

`status` 可取：

- `pending`：待确认，默认有效期 15 分钟。
- `success`：确认成功并已扣库存。
- `fail`：取消、超时、价格变化、库存不足或商品不可用。

### 2.4 分类和槽位

分类响应 `CategoryOut`：

```json
{
  "id": "60000000-0000-4000-8000-000000000001",
  "categoryL1Id": "61000000-0000-4000-8000-000000000001",
  "categoryL1": "ELECTRONICS",
  "categoryL2": "HEADPHONES",
  "requiredSlots": ["form", "connectivity"],
  "optionalSlots": ["noiseCancellation", "batteryHours"],
  "slots": [
    {
      "id": "62000000-0000-4000-8000-000000000001",
      "key": "form",
      "isRequired": true,
      "enumValues": ["in-ear", "over-ear"]
    }
  ],
  "createdAt": "2026-08-03T12:00:00Z",
  "updatedAt": "2026-08-03T12:00:00Z"
}
```

槽位 `enumValues` 至少有一个非空值；请求层会去重重复枚举值。`key` 必须符合 `^[a-z][A-Za-z0-9]*$`。

## 3. 系统接口

### `GET /health`

返回服务存活状态，不需要身份：

```json
{
  "status": "ok",
  "service": "Voice Shopping API",
  "version": "0.1.0"
}
```

## 4. 用户端接口

### 4.1 商品和商家

| 方法 | 路径 | 成功响应 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/catalog/merchants` | `200` + `ItemsResponse[MerchantOut]` | 只返回未删除且启用的商家 |
| `GET` | `/api/v1/catalog/products` | `200` + `ItemsResponse[ProductOut]` | 浏览可见商品 |
| `POST` | `/api/v1/catalog/behaviors` | `202` + `{ "status": "accepted" }` | 当前只接受 `click` |

#### `GET /api/v1/catalog/products`

查询参数：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `merchantId` | UUID，可选 | 按商家过滤 |
| `category` | string，可选 | 精确匹配二级分类，例如 `HEADPHONES` |
| `query` | string，可选 | 商品名称或品牌模糊匹配 |

示例：

```http
GET /api/v1/catalog/products?category=HEADPHONES&query=Sony
```

服务端最多返回 100 个商品，排序为创建时间和商品名称。响应格式：

```json
{
  "items": [
    {
      "id": "20000000-0000-4000-8000-000000000001",
      "merchantId": "10000000-0000-4000-8000-000000000001",
      "merchantName": "声选 · 通勤音频",
      "sku": "AUD-001",
      "name": "Sony WH-CH720N 无线降噪头戴耳机",
      "categoryL1": "ELECTRONICS",
      "categoryL2": "HEADPHONES",
      "brand": "Sony",
      "description": "真实公开型号；结构化规格已按平台筛选枚举归一化。",
      "price": "799.00",
      "stock": 31,
      "attributes": {},
      "sellingPoints": [],
      "imageUrls": [],
      "status": "on_sale",
      "createdAt": "2026-08-03T12:00:00Z",
      "updatedAt": "2026-08-03T12:00:00Z"
    }
  ]
}
```

#### `POST /api/v1/catalog/behaviors`

请求头：`Authorization: Bearer <access-token>`。请求体：

```json
{
  "productId": "20000000-0000-4000-8000-000000000001",
  "eventType": "click"
}
```

点击会更新当前用户的动态画像，并返回：

```json
{"status": "accepted"}
```

### 4.2 订单

| 方法 | 路径 | 成功响应 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/orders/mine` | `200` + `ItemsResponse[OrderOut]` | 当前用户订单，按创建时间倒序 |
| `POST` | `/api/v1/orders` | `201` + `OrderOut` | 创建待确认订单 |
| `POST` | `/api/v1/orders/{order_id}/confirm` | `200` + `OrderOut` | 确认并校验价格、库存和商品状态 |
| `POST` | `/api/v1/orders/{order_id}/cancel` | `200` + `OrderOut` | 取消当前用户的待确认订单 |

#### `POST /api/v1/orders`

请求头：`Authorization: Bearer <access-token>`。请求体：

```json
{
  "productId": "20000000-0000-4000-8000-000000000001",
  "quantity": 1,
  "idempotencyKey": "web-catalog-001"
}
```

`quantity` 默认为 1，范围是 1 到 99；`idempotencyKey` 必填，长度 1 到 120。
目录直购不绑定会话；`sessionId` 和 `sourceTurnId` 仅由服务端导购工作流在创建语音订单时写入。

业务行为：

- 商品不存在、下架、商家停用或库存不足时返回 `404`。
- 同一用户重复提交相同幂等键时返回原订单，不重复创建。
- 其他用户占用该幂等键时返回 `409`。
- 订单创建成功时状态为 `pending`，订单保存商品和商家快照。

#### `POST /api/v1/orders/{order_id}/confirm`

不需要请求体。确认时会锁定订单、商品和商家，检查订单是否过期、商品是否仍在售、价格是否变化以及库存是否足够。

失败时仍返回 `200` 和订单对象，订单状态为 `fail`，`failureReason` 可能为：

- `confirmation_timeout`
- `product_unavailable`
- `price_changed`
- `insufficient_stock`

确认成功时状态为 `success`，写入 `confirmedAt`，扣减库存并更新用户画像。已处于终态的订单会返回当前状态，不重复扣库存。

#### `POST /api/v1/orders/{order_id}/cancel`

不需要请求体。当前用户的 `pending` 订单取消后变为 `fail`，`failureReason` 为 `user_cancelled`。订单不存在或不属于当前用户时返回 `404`；重复取消会返回当前订单状态。

## 5. 商家端接口

商家店铺、商品和订单接口要求 JWT 中的角色为 `merchant`；查询和写入仍按该 JWT 用户对应的店主身份过滤。

### 5.1 分类和店铺

| 方法 | 路径 | 成功响应 | 主要失败情况 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/merchant/categories` | `200` + 分类列表 | - |
| `GET` | `/api/v1/merchant/stores` | `200` + 商家列表 | - |
| `POST` | `/api/v1/merchant/stores` | `201` + `MerchantOut` | slug 冲突 `409` |
| `PATCH` | `/api/v1/merchant/stores/{store_id}` | `200` + `MerchantOut` | 不属于当前店主 `404`，slug 冲突 `409` |
| `DELETE` | `/api/v1/merchant/stores/{store_id}` | `204` | 不属于当前店主 `404` |

删除店铺是软删除，同时将店铺下未删除商品标记为删除；订单事实不会被删除。

`PATCH /stores/{store_id}` 是部分更新，只提交需要改变的字段：

```json
{
  "name": "声选 · 通勤音频",
  "description": "更新后的店铺介绍"
}
```

### 5.2 商品

| 方法 | 路径 | 成功响应 | 主要失败情况 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/merchant/products` | `200` + 商品列表 | - |
| `POST` | `/api/v1/merchant/products` | `201` + `ProductOut` | 店铺不存在 `404`，属性校验失败 `422`，SKU 冲突 `409` |
| `PATCH` | `/api/v1/merchant/products/{product_id}` | `200` + `ProductOut` | 商品不属于当前店主 `404`，属性校验失败 `422`，SKU 冲突 `409` |
| `DELETE` | `/api/v1/merchant/products/{product_id}` | `204` | 商品不属于当前店主 `404` |

创建商品请求示例：

```json
{
  "merchantId": "10000000-0000-4000-8000-000000000001",
  "sku": "AUD-001",
  "name": "Sony WH-CH720N 无线降噪头戴耳机",
  "categoryL1": "ELECTRONICS",
  "categoryL2": "HEADPHONES",
  "brand": "Sony",
  "description": "真实公开型号；结构化规格已按平台筛选枚举归一化。",
  "price": "799.00",
  "stock": 31,
  "attributes": {
    "form": "over-ear",
    "connectivity": "bluetooth",
    "noiseCancellation": true,
    "batteryHours": 30
  },
  "sellingPoints": ["主动降噪"],
  "imageUrls": ["https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=1200&q=80"],
  "status": "on_sale"
}
```

商品写入时会从数据库读取当前二级分类和槽位定义，校验：

- 一级分类和二级分类的父子关系。
- 是否存在未知属性键。
- 必填槽位是否都有非空值。
- 枚举属性是否在当前槽位的 `enumValues` 中。

商品名称、品类、品牌、描述、价格、属性或卖点变化时会按需重建 embedding；仅修改 SKU、库存、状态或图片不会重算。

### 5.3 商家订单

`GET /api/v1/merchant/orders` 返回当前店主名下店铺的订单，响应为 `ItemsResponse[OrderOut]`，按创建时间倒序。

## 6. 平台端接口

平台接口要求 JWT 中的角色为 `platform`；普通用户和商家账号会收到 `403`。

### 6.1 分类和槽位

| 方法 | 路径 | 成功响应 | 主要失败情况 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/platform/categories` | `200` + 分类列表 | - |
| `GET` | `/api/v1/platform/category-level-ones` | `200` + 一级分类列表 | - |
| `POST` | `/api/v1/platform/category-level-ones` | `201` + `CategoryL1Out` | 重复 code `409` |
| `DELETE` | `/api/v1/platform/category-level-ones/{category_l1_id}` | `204` | 仍有关联二级分类 `409` |
| `POST` | `/api/v1/platform/categories` | `201` + `CategoryOut` | 父级不存在 `422`，分类重复 `409` |
| `PATCH` | `/api/v1/platform/categories/{category_id}` | `200` + `CategoryOut` | 分类不存在 `404`，父级不存在 `422`，冲突 `409` |
| `DELETE` | `/api/v1/platform/categories/{category_id}` | `204` | 仍有关联商品 `409` |
| `POST` | `/api/v1/platform/categories/{category_id}/slots` | `201` + `CategorySlotOut` | 分类不存在 `404`，槽位重复 `409` |
| `PATCH` | `/api/v1/platform/category-slots/{slot_id}` | `200` + `CategorySlotOut` | 槽位不存在 `404` |
| `DELETE` | `/api/v1/platform/category-slots/{slot_id}` | `204` | 槽位不存在 `404` |

创建二级分类：

```json
{
  "categoryL1Id": "61000000-0000-4000-8000-000000000001",
  "categoryL2": "HEADPHONES"
}
```

创建槽位：

```json
{
  "key": "batteryHours",
  "isRequired": false,
  "enumValues": [8, 24, 30]
}
```

### 6.2 商家、商品和订单管理

| 方法 | 路径 | 成功响应 | 主要失败情况 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/platform/merchants` | `200` + 商家列表 | - |
| `PATCH` | `/api/v1/platform/merchants/{merchant_id}/status` | `200` + `MerchantOut` | 商家不存在 `404`，禁用原因缺失 `422` |
| `GET` | `/api/v1/platform/products` | `200` + 商品列表 | - |
| `GET` | `/api/v1/platform/orders` | `200` + 订单列表 | - |
| `POST` | `/api/v1/platform/products/embeddings/rebuild` | `200` + 统计结果 | - |

启停商家：

```json
{
  "isEnabled": false,
  "disabledReason": "店铺资料审核中"
}
```

当 `isEnabled=false` 时，`disabledReason` 必填；重新启用时可以省略原因，服务端会清空旧原因。

embedding 重建返回：

```json
{
  "total": 20,
  "updated": 19,
  "cacheHits": 16,
  "generated": 3,
  "failed": 1
}
```

商品向量缓存以当前 Embedding 模型和规范化商品卡片文本为键；命中时不会再调用 Embedding 模型。当前重建接口同步处理所有未删除商品，商品量增大后应迁移为后台任务。

## 7. 会话接口

### `POST /api/v1/sessions/{session_id}/close`

请求头：`Authorization: Bearer <access-token>`。请求体可以省略：

```json
{
  "reason": "page_closed",
  "profile": {
    "city": "上海"
  }
}
```

`reason` 可取 `order_completed`、`page_closed`、`user_ended`、`disconnect`，默认是 `user_ended`。

`profile` 支持以下静态画像字段：`gender`、`age`、`city`、`heightCm`、`weightKg`、`skinType`、`techSavvy`、`locale`。其中年龄、身高和体重有范围校验。预算不是静态画像字段；用户话语中的“2000 元以下”等表达会解析为当前会话的 `budgetMax` 槽位，仅用于本轮及后续同一需求的商品价格过滤。

成功响应：

```json
{
  "sessionId": "30000000-0000-4000-8000-000000000001",
  "status": "closed",
  "updatedFields": ["city"]
}
```

关闭动作会合并会话中已经收集的画像事实和本次显式 profile；显式值优先。重复关闭是幂等的。

## 8. WebSocket 协议

HTTP API 和实时语音使用同一个 `session_id`。WebSocket 使用登录后取得的 JWT：`?token={access_token}`。只有 `customer` JWT 能在握手完成前通过校验。

### 8.1 文本 WebSocket

连接：

```text
ws://localhost:8000/ws/text/{session_id}?token={access_token}
```

连接成功后第一条消息：

```json
{
  "type": "session.connected",
  "sessionId": "demo-session"
}
```

客户端消息：

| `type` | 字段 | 说明 |
| --- | --- | --- |
| `turn.submit` | `turnId`、`utterance` | 提交一轮文本；省略 `type` 时默认按此处理 |
| `session.resume` | `turnId`、`afterSeq` | 重放指定 turn 且 `seq > afterSeq` 的文本事件 |
| `session.close` | `profile` | 关闭会话，可带静态画像 patch |

提交示例：

```json
{
  "type": "turn.submit",
  "turnId": "turn-001",
  "utterance": "推荐一副通勤降噪耳机，预算一千元以内"
}
```

空 utterance 不会启动 Agent，服务端返回：

```json
{
  "type": "flow.error",
  "sessionId": "demo-session",
  "turnId": "turn-001",
  "seq": 0,
  "payload": {"message": "utterance 不能为空"}
}
```

正常业务事件使用以下 envelope：

```json
{
  "type": "text.delta",
  "sessionId": "demo-session",
  "turnId": "turn-001",
  "seq": 4,
  "payload": {
    "scope": "speech",
    "delta": "我先帮你筛选。"
  }
}
```

主要事件：

| 事件 | payload | 说明 |
| --- | --- | --- |
| `flow.status` | `status`、可选 `intent`、`node`、`label` | Agent/工作流处理状态 |
| `recommendation.cards` | `productCards`、`emotionStyle` | 商品卡尽早下发 |
| `text.delta` | `scope`、`delta`，理由时附 `productId` | 增量话术或逐卡理由 |
| `text.completed` | `text`、`complianceBlocked` | 当前 turn 的最终文本 |
| `order.updated` | `order` | 订单状态变化 |
| `flow.error` | `message` | 当前 turn 出错 |

完成时会发送 `flow.status`，其 `payload.status` 为 `completed`。文本事件最多保留最近 300 条，Redis 重放 TTL 为 3600 秒；音频二进制不会写入重放日志。

收到 `session.close` 后，服务端关闭会话并返回：

```json
{
  "type": "session.closed",
  "sessionId": "demo-session",
  "payload": {
    "sessionId": "...",
    "status": "closed",
    "updatedFields": []
  }
}
```

### 8.2 音频 WebSocket

连接：

```text
ws://localhost:8000/ws/audio/{session_id}?token={access_token}
```

连接成功后服务端发送：

```json
{
  "type": "audio.ready",
  "sessionId": "demo-session"
}
```

客户端输入顺序：

```text
1. JSON: {"type":"audio.start","turnId":"voice-turn-001"}
2. 多个 PCM16 binary frame，采样率 16 kHz
3. JSON: {"type":"audio.commit","turnId":"voice-turn-001","clientMetrics":{...}}
```

可随时发送：

```json
{"type": "audio.cancel", "turnId": "voice-turn-001"}
```

ASR 相关事件：

- `audio.start` 成功后发送 `asr.started`，payload 包含 `model` 和 `sampleRate=16000`。
- 每次收到模型中间假设时发送 `asr.partial`，payload 为 `{ "transcript": "..." }`，其中 `transcript` 为截至当前的完整转录。
- 识别到完整句子时发送 `asr.sentence`，payload 为 `{ "transcript": "...", "fullTranscript": "..." }`；前者是该句，后者是截至当前的完整转录。
- 提交录音后发送 `asr.completed`，随后使用服务端 transcript 进入文本 Agent。
- 未配置 ASR、识别失败或转写后的工作流失败时发送 `audio.error`。payload 包含
  `stage`（`asr_start`、`asr`、`asr_finish` 或 `workflow`）；采集阶段还会带上
  `receivedBytes`、`clientMetrics`。客户端只有在 `stage=asr` 且明确收到采集指标时，
  才应将错误解释为麦克风问题。

TTS 下行顺序：

```text
audio.start (JSON)
WAV binary frame(s)
audio.end (JSON)
...
audio.done (JSON)
```

`audio.start` 的 payload 包含 `format`、`sampleRate`、`fallback`、`text`、`sentenceIndex`，如果已知句子总数还包含 `sentenceCount`。`audio.end` 标识当前短句完成，最后一个短句带 `final=true`；全部短句结束后发送 `audio.done`。

TTS 正常采样率为 24 kHz WAV；模型没有产出时使用 16 kHz 静音 WAV，并通过 `fallback=true` 通知客户端切换浏览器语音。音频数据不支持 Redis 断点重放。

## 9. 本地验证

启动 API：

```bash
uv run --project apps/api python -m voice_shopping_api.server --reload --port 8000
```

运行接口相关测试：

```bash
uv run --project apps/api pytest -m contract
uv run --project apps/api pytest -m service
uv run --project apps/api pytest -m e2e
```

E2E 必须设置独立且可丢弃的 PostgreSQL/PGVector 测试库：

```powershell
$env:VOICE_SHOPPING_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/voice-shopping-agents-test"
pnpm db:prepare-e2e
pnpm test:e2e
```

测试夹具会在每个 E2E 模块开始时重建该库的 `public` schema、执行迁移并播种演示数据；若未
设置该变量，E2E 会跳过，且绝不会使用应用数据库。

当前演示身份、接口范围和生产化限制见 [架构文档](architecture.md)、[实现说明](implementation.md) 和 [任务文档](tasks.md)。
