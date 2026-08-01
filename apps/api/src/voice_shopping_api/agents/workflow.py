import logging
import re
from decimal import Decimal
from typing import Any

from langgraph.graph import END, START, StateGraph

from voice_shopping_api.agents.model import (
    clarify_with_model,
    recognize_with_model,
    rerank_products,
    respond_with_model,
)
from voice_shopping_api.agents.state import (
    ClarificationResult,
    EmotionalResponseResult,
    IntentResult,
    ProductReason,
    ProductRecommendationResult,
    ShoppingState,
)
from voice_shopping_api.core.taxonomy import REQUIRED_ATTRIBUTE_KEYS_BY_CATEGORY

logger = logging.getLogger(__name__)

REQUIRED_SLOTS: dict[str, list[str]] = {
    category: list(keys) for category, keys in REQUIRED_ATTRIBUTE_KEYS_BY_CATEGORY.items()
}

CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "HEADPHONES": ("耳机", "蓝牙耳机", "降噪耳机"),
    "COFFEE_MACHINE": ("咖啡机", "胶囊机"),
    "ELECTRIC_KETTLE": ("电水壶", "热水壶", "恒温水壶", "水壶"),
    "RUNNING_SHOES": ("跑鞋", "跑步鞋", "运动鞋", "鞋子", "鞋"),
    "WATCHES": ("手表", "腕表", "表"),
    "LIPSTICK": ("口红", "唇膏"),
}

QUESTIONS = {
    "productCategory": "你想购买哪一类商品？",
    "noiseCancellation": "你需要主动降噪吗？",
    "form": "你想要入耳式还是头戴式？",
    "connectivity": "你希望使用蓝牙还是有线连接？",
    "batteryHours": "你希望续航至少多少小时？",
    "type": "你想要胶囊式还是半自动咖啡机？",
    "steamWand": "你需要蒸汽棒打奶泡吗？",
    "pressureBar": "你希望萃取压力至少多少 Bar？",
    "waterTankMl": "你希望水箱容量至少多少毫升？",
    "capacityL": "你希望水壶容量至少多少升？",
    "temperatureControl": "你需要多档温控吗？",
    "keepWarm": "你需要保温功能吗？",
    "gender": "你需要男款、女款还是中性款？",
    "size": "你需要多大尺码？",
    "terrain": "主要用于公路还是越野路面？",
    "cushion": "你偏好高缓震还是适中缓震？",
    "footType": "你的足型是正常足、扁平足还是过度内旋？",
    "movement": "你偏好机械、石英还是光动能机芯？",
    "material": "你偏好钢、钛金属还是树脂材质？",
    "waterResistance": "你希望至少达到多少米防水？",
    "shade": "你偏好什么色调？",
    "finish": "你偏好哑光、缎光还是水光妆效？",
    "skinType": "你的肤质是干性、油性还是中性？",
}

SLOT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "noiseCancellation": {"type": "boolean", "meaning": "是否需要主动降噪"},
    "form": {
        "type": "enum",
        "meaning": "耳机佩戴形式",
        "values": {"in-ear": "入耳式", "over-ear": "头戴式"},
    },
    "connectivity": {
        "type": "enum",
        "meaning": "连接方式",
        "values": {"bluetooth": "蓝牙或无线", "wired": "有线"},
    },
    "batteryHours": {"type": "number", "meaning": "最低续航小时数", "minimum": 1},
    "type": {
        "type": "enum",
        "meaning": "咖啡机类型",
        "values": {"capsule": "胶囊式", "semi-automatic": "半自动"},
    },
    "steamWand": {"type": "boolean", "meaning": "是否需要蒸汽棒"},
    "pressureBar": {"type": "number", "meaning": "最低萃取压力 Bar", "minimum": 1},
    "waterTankMl": {"type": "number", "meaning": "最低水箱容量毫升", "minimum": 1},
    "capacityL": {"type": "number", "meaning": "最低容量升数", "minimum": 0.1},
    "temperatureControl": {"type": "boolean", "meaning": "是否需要多档温控"},
    "keepWarm": {"type": "boolean", "meaning": "是否需要保温"},
    "gender": {
        "type": "enum",
        "meaning": "适用性别",
        "values": {"male": "男款", "female": "女款", "unisex": "中性款"},
    },
    "size": {"type": "number", "meaning": "鞋码", "minimum": 1},
    "terrain": {
        "type": "enum",
        "meaning": "跑步路面",
        "values": {"road": "公路", "trail": "越野"},
    },
    "cushion": {
        "type": "enum",
        "meaning": "缓震偏好",
        "values": {"high": "高缓震", "medium": "适中缓震"},
    },
    "footType": {
        "type": "enum",
        "meaning": "足型",
        "values": {"neutral": "正常足", "flat": "扁平足", "overpronation": "过度内旋"},
    },
    "movement": {
        "type": "enum",
        "meaning": "手表机芯",
        "values": {"automatic": "机械", "quartz": "石英", "eco-drive": "光动能"},
    },
    "material": {
        "type": "enum",
        "meaning": "手表材质",
        "values": {"steel": "钢", "titanium": "钛金属", "resin": "树脂"},
    },
    "waterResistance": {"type": "number", "meaning": "最低防水米数", "minimum": 1},
    "shade": {
        "type": "enum",
        "meaning": "口红色调",
        "values": {
            "milk-tea": "奶茶色",
            "tomato-red": "番茄红",
            "coral": "珊瑚色",
            "rose": "豆沙或玫瑰色",
            "ruby-red": "正红色",
        },
    },
    "finish": {
        "type": "enum",
        "meaning": "口红妆效",
        "values": {"matte": "哑光", "satin": "缎光", "glossy": "水光"},
    },
    "skinType": {
        "type": "enum",
        "meaning": "肤质",
        "values": {"dry": "干性", "oily": "油性", "normal": "中性"},
    },
    "budgetMax": {"type": "number", "meaning": "最高预算元数", "minimum": 1},
}

COMPLIANCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (r"百分百", r"绝对(?:有效|安全)", r"包治", r"国家级", r"稳赚不赔")
)
COMPLIANCE_FALLBACK = "抱歉，这段推荐话术未通过合规检查。你可以查看商品事实后再做选择。"


def _category(utterance: str) -> str | None:
    for category, aliases in CATEGORY_ALIASES.items():
        if any(alias in utterance for alias in aliases):
            return category
    return None


def _normalize_category(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    canonical = normalized.upper()
    if canonical in CATEGORY_ALIASES:
        return canonical
    return _category(normalized)


def _has_order_target(state: ShoppingState, utterance: str) -> bool:
    if any(
        marker in utterance
        for marker in ("下单", "第一", "第二", "第三", "这款", "那款", "就要", "就买")
    ):
        return True
    cards = state.get("previous_product_cards") or state.get("product_cards") or []
    return any(card.get("name") and str(card["name"]) in utterance for card in cards)


def _order_action(utterance: str) -> str:
    if any(word in utterance for word in ("取消", "不要了", "不买了")):
        return "CANCEL"
    if any(word in utterance for word in ("确认", "确定", "下单吧", "就这样")):
        return "CONFIRM"
    return "CREATE"


async def recognize_intent(state: ShoppingState) -> dict[str, Any]:
    utterance = state.get("utterance", "").strip()
    previous_category = _normalize_category(state.get("product_category"))
    explicit_category = _category(utterance)
    category_switched_by_rule = bool(
        explicit_category and explicit_category != previous_category
    )
    if state.get("model_enabled") and (
        not state.get("pending_question") or category_switched_by_rule
    ):
        try:
            model_results = await recognize_with_model(
                utterance, state.get("conversation_history", [])
            )
            if model_results:
                normalized_results: list[IntentResult] = []
                for item in model_results:
                    normalized_category = _normalize_category(item.product_category)
                    normalized_results.append(
                        item.model_copy(update={"product_category": normalized_category})
                    )
                model_results = normalized_results
                model_category = next(
                    (item.product_category for item in model_results if item.product_category),
                    None,
                )
                category = explicit_category or model_category or previous_category
                category_changed = bool(category and category != previous_category)
                if (
                    category_changed
                    and model_results[0].type == "PRODUCT_ORDER"
                    and model_results[0].action == "CREATE"
                    and not _has_order_target(state, utterance)
                ):
                    model_results[0] = IntentResult(
                        type="PRODUCT_RECOMMENDATION",
                        confidence=model_results[0].confidence,
                        product_category=category,
                    )
                model_results = [
                    item.model_copy(update={"product_category": category})
                    if category
                    and item.type
                    in ("PRODUCT_RECOMMENDATION", "PRODUCT_COMPARE", "PRODUCT_QUERY")
                    else item
                    for item in model_results
                ]
                return {
                    "intents": [item.model_dump(exclude_none=True) for item in model_results],
                    "action_queue": [item.type for item in model_results],
                    "product_category": category,
                    "category_changed": category_changed,
                }
        except Exception as exc:
            logger.warning("Intent model failed; using deterministic fallback: %s", exc)
    category = explicit_category or previous_category
    category_changed = bool(category and category != previous_category)
    if state.get("pending_question") and not category_changed:
        results = [
            IntentResult(type="PRODUCT_RECOMMENDATION", confidence=0.99, product_category=category)
        ]
    else:
        detections: list[tuple[int, IntentResult]] = []

        def detect(keywords: tuple[str, ...], result: IntentResult) -> None:
            positions = [utterance.find(word) for word in keywords if word and word in utterance]
            if positions:
                detections.append((min(positions), result))

        recommendation_words = ("推荐", "想买", "帮我选", "需要一") + CATEGORY_ALIASES.get(
            category or "", ()
        )
        detect(
            recommendation_words,
            IntentResult(type="PRODUCT_RECOMMENDATION", confidence=0.95, product_category=category),
        )
        detect(
            ("对比", "比较", "区别"),
            IntentResult(type="PRODUCT_COMPARE", confidence=0.94, product_category=category),
        )
        detect(
            ("多少钱", "库存", "介绍", "怎么样", "查询"),
            IntentResult(type="PRODUCT_QUERY", confidence=0.9, product_category=category),
        )
        detect(
            ("下单", "买第一", "买第二", "买第三", "确认", "取消订单"),
            IntentResult(type="PRODUCT_ORDER", confidence=0.97, action=_order_action(utterance)),
        )
        detect(("你好", "谢谢", "嗨", "再见"), IntentResult(type="CHAT", confidence=0.9))
        if not detections:
            detections.append((0, IntentResult(type="UNSUPPORTED_REQUEST", confidence=0.86)))
        results = []
        seen: set[str] = set()
        for _, result in sorted(detections, key=lambda item: item[0]):
            if result.type not in seen:
                results.append(result)
                seen.add(result.type)
    data = [result.model_dump(exclude_none=True) for result in results]
    return {
        "intents": data,
        "action_queue": [result.type for result in results],
        "product_category": category,
        "category_changed": category_changed,
    }


def _chinese_amount(value: str) -> int | None:
    simple = {
        "五百": 500,
        "六百": 600,
        "八百": 800,
        "一千": 1000,
        "两千": 2000,
        "三千": 3000,
        "五千": 5000,
        "一万": 10000,
    }
    return next((amount for text, amount in simple.items() if text in value), None)


def _boolean_answer(utterance: str) -> bool | None:
    if any(word in utterance for word in ("不需要", "不要", "不用", "否", "没有")):
        return False
    if any(word in utterance for word in ("需要", "要", "是", "可以", "有")):
        return True
    return None


def _extract_slots(
    utterance: str, slots: dict[str, Any], pending_slot: str | None = None
) -> dict[str, Any]:
    updated = dict(slots)
    number = re.search(r"(?<!\d)(\d{2,6})(?:\s*元)?(?:以内|以下|左右|预算)?", utterance)
    amount = int(number.group(1)) if number else _chinese_amount(utterance)
    if amount and any(
        word in utterance for word in ("预算", "以内", "以下", "元", "百", "千", "万")
    ):
        updated["budgetMax"] = amount
    boolean_slots = {
        "noiseCancellation",
        "steamWand",
        "temperatureControl",
        "keepWarm",
    }
    if pending_slot in boolean_slots:
        answer = _boolean_answer(utterance)
        if answer is not None:
            updated[pending_slot] = answer

    if "降噪" in utterance:
        updated["noiseCancellation"] = not any(
            word in utterance for word in ("不要降噪", "不需要降噪", "无需降噪")
        )
    if any(word in utterance for word in ("入耳", "耳塞")):
        updated["form"] = "in-ear"
    elif "头戴" in utterance:
        updated["form"] = "over-ear"
    if any(word in utterance.lower() for word in ("蓝牙", "bluetooth", "无线")):
        updated["connectivity"] = "bluetooth"
    elif "有线" in utterance:
        updated["connectivity"] = "wired"
    battery = re.search(r"(?:续航(?:至少|要)?|至少)\s*(\d{1,3})\s*小时", utterance)
    if battery:
        updated["batteryHours"] = int(battery.group(1))

    if "胶囊" in utterance:
        updated["type"] = "capsule"
    elif any(word in utterance for word in ("半自动", "半自助")):
        updated["type"] = "semi-automatic"
    if any(word in utterance for word in ("蒸汽棒", "奶泡")):
        updated["steamWand"] = not any(
            word in utterance for word in ("不要蒸汽棒", "不需要奶泡", "不打奶泡")
        )
    pressure = re.search(r"(\d{1,2})\s*(?:Bar|bar|巴)", utterance)
    if pressure:
        updated["pressureBar"] = int(pressure.group(1))
    milliliters = re.search(r"(\d{3,4})\s*(?:毫升|ml|ML)", utterance)
    if milliliters:
        updated["waterTankMl"] = int(milliliters.group(1))
    liters = re.search(r"(\d(?:\.\d+)?)\s*(?:升|L|l)", utterance)
    if liters:
        liters_value = float(liters.group(1))
        if pending_slot == "waterTankMl":
            updated["waterTankMl"] = int(liters_value * 1000)
        else:
            updated["capacityL"] = liters_value
    if any(word in utterance for word in ("温控", "恒温", "多档温度")):
        updated["temperatureControl"] = True
    if "保温" in utterance:
        updated["keepWarm"] = not any(
            word in utterance for word in ("不要保温", "不需要保温", "无需保温")
        )

    if any(word in utterance for word in ("女款", "女士", "女性")):
        updated["gender"] = "female"
    elif any(word in utterance for word in ("男款", "男士", "男性")):
        updated["gender"] = "male"
    elif any(word in utterance for word in ("中性", "男女都可以", "不限性别")):
        updated["gender"] = "unisex"
    shoe_size = re.search(r"(3[5-9]|4[0-6])(?:\.5)?\s*(?:码|号)", utterance)
    if shoe_size:
        updated["size"] = float(shoe_size.group(0).split()[0].rstrip("码号"))
    if any(word in utterance for word in ("越野", "山路")):
        updated["terrain"] = "trail"
    elif any(word in utterance for word in ("公路", "路跑", "日常跑", "慢跑")):
        updated["terrain"] = "road"
    if any(word in utterance for word in ("高缓震", "强缓震", "缓震好")):
        updated["cushion"] = "high"
    elif any(word in utterance for word in ("适中缓震", "中等缓震")):
        updated["cushion"] = "medium"
    if "扁平足" in utterance:
        updated["footType"] = "flat"
    elif any(word in utterance for word in ("过度内旋", "内旋")):
        updated["footType"] = "overpronation"
    elif any(word in utterance for word in ("正常足", "中性足", "正常足型")):
        updated["footType"] = "neutral"

    if any(word in utterance for word in ("光动能", "光能")):
        updated["movement"] = "eco-drive"
    elif "机械" in utterance:
        updated["movement"] = "automatic"
    elif "石英" in utterance:
        updated["movement"] = "quartz"
    if any(word in utterance for word in ("钛金属", "钛合金", "钛")):
        updated["material"] = "titanium"
    elif any(word in utterance for word in ("不锈钢", "钢制", "钢")):
        updated["material"] = "steel"
    elif "树脂" in utterance:
        updated["material"] = "resin"
    resistance = re.search(r"(\d{2,3})\s*米防水", utterance)
    if resistance:
        updated["waterResistance"] = int(resistance.group(1))

    shades = {
        "milk-tea": ("奶茶",),
        "tomato-red": ("番茄红",),
        "coral": ("珊瑚", "橘色"),
        "rose": ("豆沙", "玫瑰"),
        "ruby-red": ("正红", "红色"),
    }
    for canonical, aliases in shades.items():
        if any(alias in utterance for alias in aliases):
            updated["shade"] = canonical
            break
    if any(word in utterance for word in ("哑光", "雾面", "丝绒")):
        updated["finish"] = "matte"
    elif "缎光" in utterance:
        updated["finish"] = "satin"
    elif any(word in utterance for word in ("水光", "亮泽")):
        updated["finish"] = "glossy"
    if any(word in utterance for word in ("干性", "干皮")):
        updated["skinType"] = "dry"
    elif any(word in utterance for word in ("油性", "油皮")):
        updated["skinType"] = "oily"
    elif any(word in utterance for word in ("中性", "正常肤质")):
        updated["skinType"] = "normal"
    return updated


def _validated_agent_slots(
    candidate_slots: dict[str, Any], required_slots: list[str]
) -> dict[str, Any]:
    allowed_slots = {*required_slots, "budgetMax"}
    validated: dict[str, Any] = {}
    for slot, value in candidate_slots.items():
        if slot not in allowed_slots:
            continue
        definition = SLOT_DEFINITIONS.get(slot)
        if not definition:
            continue
        value_type = definition["type"]
        if value_type == "boolean":
            if type(value) is bool:
                validated[slot] = value
            continue
        if value_type == "enum":
            if value in definition.get("values", {}):
                validated[slot] = value
            continue
        if (
            value_type == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            minimum = float(definition.get("minimum", 0))
            if float(value) >= minimum:
                validated[slot] = value
    return validated


async def clarify_requirements(state: ShoppingState) -> dict[str, Any]:
    category = state.get("product_category")
    existing_slots = {} if state.get("category_changed") else state.get("slots", {})
    pending_slot = None
    if not state.get("category_changed"):
        pending_slot = (state.get("pending_question") or {}).get("slot")
    slots = _extract_slots(state.get("utterance", ""), existing_slots, pending_slot)
    required_slots = REQUIRED_SLOTS.get(category or "", [])
    if state.get("model_enabled") and category:
        try:
            relevant_definitions = {
                slot: SLOT_DEFINITIONS[slot]
                for slot in [*required_slots, "budgetMax"]
                if slot in SLOT_DEFINITIONS
            }
            agent_slots = await clarify_with_model(
                state.get("utterance", ""),
                category,
                required_slots,
                slots,
                state.get("pending_question") if not state.get("category_changed") else None,
                relevant_definitions,
                state.get("conversation_history", []),
            )
            slots.update(_validated_agent_slots(agent_slots, required_slots))
        except Exception as exc:
            logger.warning("Clarification model failed; using deterministic fallback: %s", exc)
    if not category:
        result = ClarificationResult(
            status="ASK",
            slots=slots,
            missing_slots=["productCategory"],
            question=QUESTIONS["productCategory"],
        )
        required_slots: list[str] = []
        question_slot = "productCategory"
    else:
        missing = [slot for slot in required_slots if slots.get(slot) in (None, "")]
        result = ClarificationResult(
            status="ASK" if missing else "READY",
            slots=slots,
            missing_slots=missing,
            question=QUESTIONS.get(missing[0]) if missing else None,
        )
        question_slot = missing[0] if missing else ""
    return {
        "required_slots": required_slots,
        "slots": result.slots,
        "clarification_status": result.status,
        "missing_slots": result.missing_slots,
        "pending_question": (
            {"slot": question_slot, "question": result.question or ""}
            if result.status == "ASK"
            else None
        ),
    }


def _score_product(
    product: dict[str, Any], state: ShoppingState, reranker_score: float | None = None
) -> tuple[float, dict[str, float]]:
    profile = state.get("user_profile_snapshot", {})
    static = profile.get("static", {})
    dynamic = profile.get("dynamic", {})
    category = str(product.get("category_l2", ""))
    brand = str(product.get("brand") or "")
    product_id = str(product.get("id", ""))
    utterance = state.get("utterance", "")
    facts = " ".join(
        [
            str(product.get("name", "")),
            str(product.get("description", "")),
            " ".join(product.get("selling_points", [])),
            str(product.get("attributes", {})),
        ]
    )
    keywords = [word for word in re.split(r"[，。\s、]+", utterance) if len(word) >= 2]
    lexical_hits = sum(1 for word in keywords if word in facts)
    reranker = (
        min(1.0, max(0.0, reranker_score))
        if reranker_score is not None
        else min(1.0, 0.52 + lexical_hits * 0.1)
    )
    dynamic_category = float(dynamic.get("categoryScores", {}).get(category, 0))
    dynamic_product = float(dynamic.get("productScores", {}).get(product_id, 0))
    dynamic_score = min(1.0, dynamic_category * 0.6 + dynamic_product * 0.4)
    static_category = float(static.get("categoryScores", {}).get(category, 0))
    static_brand = float(static.get("brandScores", {}).get(brand, 0))
    static_score = min(1.0, static_category * 0.6 + static_brand * 0.4)
    score = 0.4 * reranker + 0.4 * dynamic_score + 0.2 * static_score
    return score, {
        "reranker": round(reranker, 4),
        "dynamicProfile": round(dynamic_score, 4),
        "staticProfile": round(static_score, 4),
    }


def _attribute_matches(key: str, product_value: Any, requested_value: Any) -> bool:
    if product_value is None:
        return False
    if key == "gender" and product_value == "unisex":
        return requested_value in {"male", "female", "unisex"}
    if key == "size" and isinstance(product_value, list) and len(product_value) == 2:
        return float(product_value[0]) <= float(requested_value) <= float(product_value[1])
    if key in {"batteryHours", "pressureBar", "waterTankMl", "capacityL"}:
        return float(product_value) >= float(requested_value)
    if key == "waterResistance":
        matched = re.search(r"\d+", str(product_value))
        return bool(matched and int(matched.group()) >= int(requested_value))
    if isinstance(product_value, list):
        return requested_value in product_value
    return product_value == requested_value


async def recommend_products(state: ShoppingState) -> dict[str, Any]:
    intent = (state.get("intents") or [{}])[0].get("type")
    previous_cards = state.get("previous_product_cards", [])
    if intent in ("PRODUCT_COMPARE", "PRODUCT_QUERY") and previous_cards:
        selected = previous_cards
        if intent == "PRODUCT_QUERY":
            mentioned = [
                card
                for card in previous_cards
                if str(card.get("name") or "") in state.get("utterance", "")
            ]
            selected = mentioned or previous_cards[:1]
        result = ProductRecommendationResult(
            product_cards=selected[:3],
            emotion_style="analytical-professional",
        )
        return result.model_dump()
    category = state.get("product_category")
    slots = state.get("slots", {})
    budget = slots.get("budgetMax")
    required_slots = REQUIRED_SLOTS.get(category or "", [])
    products = []
    for product in state.get("catalog_products", []):
        if category and product.get("category_l2") != category:
            continue
        if budget is not None and Decimal(str(product.get("price", 0))) > Decimal(str(budget)):
            continue
        attributes = product.get("attributes", {})
        if any(
            not _attribute_matches(slot, attributes.get(slot), slots.get(slot))
            for slot in required_slots
            if slots.get(slot) is not None
        ):
            continue
        products.append(product)
    products = sorted(
        products, key=lambda product: float(product.get("vector_score") or 0), reverse=True
    )[:20]
    reranker_scores: dict[str, float] = {}
    if state.get("model_enabled") and products:
        try:
            reranker_scores = await rerank_products(state.get("utterance", ""), products)
        except Exception as exc:
            logger.warning("Reranker failed; using lexical fallback: %s", exc)
    ranked = sorted(
        (
            (
                *_score_product(product, state, reranker_scores.get(str(product.get("id")))),
                product,
            )
            for product in products
        ),
        key=lambda item: (item[0], int(item[2].get("stock", 0))),
        reverse=True,
    )[:3]
    cards: list[dict[str, Any]] = []
    for score, score_parts, product in ranked:
        cards.append(
            {
                "productId": str(product["id"]),
                "merchantId": str(product["merchant_id"]),
                "merchantName": product.get("merchant_name"),
                "name": product["name"],
                "brand": product.get("brand"),
                "price": float(product["price"]),
                "stock": product["stock"],
                "imageUrl": (product.get("image_urls") or [None])[0],
                "sellingPoints": product.get("selling_points", []),
                "attributes": product.get("attributes", {}),
                "matchScore": round(score, 4),
                "scoreBreakdown": score_parts,
            }
        )
    result = ProductRecommendationResult(
        product_cards=cards,
        emotion_style="warm-professional" if cards else "helpful-apologetic",
    )
    return result.model_dump()


async def order_response(_: ShoppingState) -> dict[str, Any]:
    return {"speech_text": "正在处理你的订单请求。", "final_reply": "正在处理你的订单请求。"}


async def emotional_response(state: ShoppingState) -> dict[str, Any]:
    if state.get("clarification_status") == "ASK":
        speech = (state.get("pending_question") or {}).get("question", QUESTIONS["productCategory"])
        result = EmotionalResponseResult(reasons=[], speech_text=speech)
    elif state.get("product_cards"):
        if state.get("model_enabled"):
            try:
                model_result = await respond_with_model(
                    state.get("utterance", ""),
                    state["product_cards"],
                    state.get("emotion_style", "warm-professional"),
                )
                return {
                    "reasons": [reason.model_dump() for reason in model_result.reasons],
                    "speech_text": model_result.speech_text,
                    "final_reply": model_result.speech_text,
                }
            except Exception as exc:
                logger.warning("Response model failed; using deterministic fallback: %s", exc)
        reasons = []
        for index, card in enumerate(state["product_cards"], start=1):
            point = (card.get("sellingPoints") or ["整体匹配你的需求"])[0]
            reasons.append(
                ProductReason(
                    product_id=card["productId"],
                    reason=f"第{index}款{card['name']}：{point}，且价格符合当前筛选条件。",
                )
            )
        speech = "我筛选出了三款商品。" if len(reasons) == 3 else f"我找到了{len(reasons)}款商品。"
        speech += " ".join(reason.reason for reason in reasons)
        result = EmotionalResponseResult(reasons=reasons, speech_text=speech)
    else:
        intent = (state.get("intents") or [{}])[0].get("type")
        if intent == "CHAT":
            speech = "你好，我可以帮你推荐、查询、对比商品，也可以协助语音下单。"
        elif intent == "UNSUPPORTED_REQUEST":
            speech = "抱歉，我目前只能协助商品推荐、查询、对比和下单。你可以告诉我想买什么商品。"
        else:
            speech = "暂时没有找到符合条件的在售商品，可以放宽预算或换一个品类试试。"
        result = EmotionalResponseResult(reasons=[], speech_text=speech)
    return {
        "reasons": [reason.model_dump() for reason in result.reasons],
        "speech_text": result.speech_text,
        "final_reply": result.speech_text,
    }


def is_compliant(text_value: str) -> bool:
    return not any(pattern.search(text_value) for pattern in COMPLIANCE_PATTERNS)


async def compliance_check(state: ShoppingState) -> dict[str, Any]:
    speech = state.get("speech_text", "")
    if is_compliant(speech):
        return {"compliance_blocked": False, "final_reply": speech}
    return {
        "compliance_blocked": True,
        "reasons": [],
        "speech_text": COMPLIANCE_FALLBACK,
        "final_reply": COMPLIANCE_FALLBACK,
    }


def _route_intent(state: ShoppingState) -> str:
    intent = (state.get("intents") or [{}])[0].get("type")
    if intent == "PRODUCT_RECOMMENDATION":
        return "clarify"
    if intent in ("PRODUCT_COMPARE", "PRODUCT_QUERY"):
        return "recommend"
    if intent == "PRODUCT_ORDER":
        return "order"
    return "respond"


def _route_clarification(state: ShoppingState) -> str:
    return "recommend" if state.get("clarification_status") == "READY" else "respond"


def build_workflow():
    graph = StateGraph(ShoppingState)
    graph.add_node("intent_agent", recognize_intent)
    graph.add_node("clarification_agent", clarify_requirements)
    graph.add_node("recommendation_agent", recommend_products)
    graph.add_node("order_node", order_response)
    graph.add_node("emotional_agent", emotional_response)
    graph.add_node("compliance_check", compliance_check)
    graph.add_edge(START, "intent_agent")
    graph.add_conditional_edges(
        "intent_agent",
        _route_intent,
        {
            "clarify": "clarification_agent",
            "recommend": "recommendation_agent",
            "order": "order_node",
            "respond": "emotional_agent",
        },
    )
    graph.add_conditional_edges(
        "clarification_agent",
        _route_clarification,
        {"recommend": "recommendation_agent", "respond": "emotional_agent"},
    )
    graph.add_edge("recommendation_agent", "emotional_agent")
    graph.add_edge("order_node", "compliance_check")
    graph.add_edge("emotional_agent", "compliance_check")
    graph.add_edge("compliance_check", END)
    return graph.compile()


shopping_workflow = build_workflow()
