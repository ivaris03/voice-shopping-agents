import logging
import re
from typing import Any

from voice_shopping_api.agents.model import extract_slots_with_model
from voice_shopping_api.agents.nodes.constants import QUESTIONS, REQUIRED_SLOTS, SLOT_DEFINITIONS
from voice_shopping_api.agents.nodes.memory import model_memory_context
from voice_shopping_api.agents.state import ClarificationResult, ShoppingState

logger = logging.getLogger(__name__)


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
    boolean_slots = {"noiseCancellation", "steamWand", "temperatureControl", "keepWarm"}
    if pending_slot in boolean_slots and (answer := _boolean_answer(utterance)) is not None:
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
    if battery := re.search(r"(?:续航(?:至少|要)?|至少)\s*(\d{1,3})\s*小时", utterance):
        updated["batteryHours"] = int(battery.group(1))
    if "胶囊" in utterance:
        updated["type"] = "capsule"
    elif any(word in utterance for word in ("半自动", "半自助")):
        updated["type"] = "semi-automatic"
    if any(word in utterance for word in ("蒸汽棒", "奶泡")):
        updated["steamWand"] = not any(
            word in utterance for word in ("不要蒸汽棒", "不需要奶泡", "不打奶泡")
        )
    if pressure := re.search(r"(\d{1,2})\s*(?:Bar|bar|巴)", utterance):
        updated["pressureBar"] = int(pressure.group(1))
    if milliliters := re.search(r"(\d{3,4})\s*(?:毫升|ml|ML)", utterance):
        updated["waterTankMl"] = int(milliliters.group(1))
    if liters := re.search(r"(\d(?:\.\d+)?)\s*(?:升|L|l)", utterance):
        liters_value = float(liters.group(1))
        updated["waterTankMl" if pending_slot == "waterTankMl" else "capacityL"] = (
            int(liters_value * 1000) if pending_slot == "waterTankMl" else liters_value
        )
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
    if shoe_size := re.search(r"(3[5-9]|4[0-6])(?:\.5)?\s*(?:码|号)", utterance):
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
    if resistance := re.search(r"(\d{2,3})\s*米防水", utterance):
        updated["waterResistance"] = int(resistance.group(1))
    for canonical, aliases in {
        "milk-tea": ("奶茶",),
        "tomato-red": ("番茄红",),
        "coral": ("珊瑚", "橘色"),
        "rose": ("豆沙", "玫瑰"),
        "ruby-red": ("正红", "红色"),
    }.items():
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


def _effective_slot_definition(
    slot: str, taxonomy_definitions: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    taxonomy_definition = taxonomy_definitions.get(slot)
    canonical_definition = SLOT_DEFINITIONS.get(slot)
    if not taxonomy_definition:
        return canonical_definition
    if not canonical_definition:
        return taxonomy_definition
    return {
        **taxonomy_definition,
        **{
            key: value
            for key, value in canonical_definition.items()
            if key in {"type", "meaning", "minimum", "productAttribute", "matchMode"}
        },
    }


def _validated_agent_slots(
    candidate_slots: dict[str, Any],
    allowed_slots: list[str],
    definitions: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for slot, value in candidate_slots.items():
        if slot not in {*allowed_slots, "budgetMax"}:
            continue
        definition = _effective_slot_definition(slot, definitions or {})
        if not definition:
            continue
        value_type = definition["type"]
        if (value_type == "boolean" and type(value) is bool) or (
            value_type == "enum" and value in definition.get("values", [])
        ):
            validated[slot] = value
        elif value_type == "text" and isinstance(value, str) and value.strip():
            validated[slot] = value.strip()
        elif (
            value_type == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float(value) >= float(definition.get("minimum", 0))
        ):
            validated[slot] = value
    return validated


def _enum_answer(utterance: str, values: list[Any]) -> Any | None:
    lowered, matches = utterance.strip().lower(), []
    number = re.search(r"(?<!\d)(\d+(?:\.\d+)?)", utterance)
    for value in values:
        matched = (
            (type(value) is bool and _boolean_answer(utterance) is value)
            or (
                isinstance(value, (int, float))
                and number
                and float(number.group(1)) == float(value)
            )
            or (isinstance(value, str) and value.lower() in lowered)
        )
        if matched and value not in matches:
            matches.append(value)
    return matches[0] if len(matches) == 1 else None


def _question_for_slots(slots: list[str], taxonomy_questions: dict[str, str]) -> str:
    questions = [
        QUESTIONS.get(slot) or taxonomy_questions.get(slot) or f"请告诉我{slot}？" for slot in slots
    ]
    return questions[0] if len(questions) < 2 else f"{questions[0]}另外，{questions[1]}"


async def extract_slots_for_intent(state: ShoppingState) -> dict[str, Any]:
    """Extract and validate slot values owned by the intent Agent.

    This helper stays next to the slot parsing rules, but it is invoked only by
    ``intent_agent``.  The clarification Agent below only inspects the resulting
    slots and decides whether another question is required.
    """
    category = state.get("product_category")
    required_slots_by_category = state.get("required_slots_by_category")
    allowed_slots_by_category = state.get("allowed_slots_by_category")
    if required_slots_by_category and category in required_slots_by_category:
        required_slots = required_slots_by_category[category]
    elif "required_slots" in state:
        required_slots = state["required_slots"]
    else:
        required_slots = REQUIRED_SLOTS.get(category or "", [])
    if allowed_slots_by_category and category in allowed_slots_by_category:
        allowed_slots = allowed_slots_by_category[category]
    elif "allowed_slots" in state:
        allowed_slots = state["allowed_slots"]
    else:
        allowed_slots = required_slots
    taxonomy_definitions = state.get("taxonomy_slot_definitions_by_category", {}).get(
        category or "", state.get("taxonomy_slot_definitions", {})
    )
    # A pending question represents an in-flight clarification. In particular,
    # a checkpointer can still hold the previous turn's transient category flag;
    # that flag must not discard the answer currently being supplied.
    starts_new_request = not state.get("pending_question") and bool(
        state.get("category_changed") or state.get("starts_new_product_request")
    )
    existing_slots = (
        {}
        if starts_new_request
        else _validated_agent_slots(state.get("slots", {}), allowed_slots, taxonomy_definitions)
    )
    pending_slot = (
        None if starts_new_request else (state.get("pending_question") or {}).get("slot")
    )
    # Keep track of values the deterministic parser can establish from this
    # utterance. The model can fill ASR gaps, but must not replace an explicit
    # answer such as "机械的吧" with a conflicting enum value.
    deterministic_slots = _validated_agent_slots(
        _extract_slots(state.get("utterance", ""), {}, pending_slot),
        allowed_slots,
        taxonomy_definitions,
    )
    slots = _validated_agent_slots(
        _extract_slots(state.get("utterance", ""), existing_slots, pending_slot),
        allowed_slots,
        taxonomy_definitions,
    )
    pending_definition = _effective_slot_definition(pending_slot or "", taxonomy_definitions)
    if pending_slot and pending_definition and slots.get(pending_slot) in (None, ""):
        value_type = pending_definition["type"]
        if (
            value_type == "boolean"
            and (answer := _boolean_answer(state.get("utterance", ""))) is not None
        ):
            slots[pending_slot] = answer
            deterministic_slots[pending_slot] = answer
        elif value_type == "number" and (
            number := re.search(r"(?<!\d)(\d+(?:\.\d+)?)", state.get("utterance", ""))
        ):
            slots[pending_slot] = float(number.group(1))
            deterministic_slots[pending_slot] = slots[pending_slot]
        elif (
            value_type == "enum"
            and (
                answer := _enum_answer(
                    state.get("utterance", ""), list(pending_definition.get("values", []))
                )
            )
            is not None
        ):
            slots[pending_slot] = answer
            deterministic_slots[pending_slot] = answer
        elif value_type == "text" and state.get("utterance", "").strip():
            slots[pending_slot] = state["utterance"].strip()
            deterministic_slots[pending_slot] = slots[pending_slot]
    if state.get("model_enabled") and category:
        try:
            relevant_definitions = {
                slot: definition
                for slot in [*allowed_slots, "budgetMax"]
                if (definition := _effective_slot_definition(slot, taxonomy_definitions))
                is not None
            }
            agent_slots = await extract_slots_with_model(
                state.get("utterance", ""),
                category,
                required_slots,
                slots,
                state.get("pending_question") if not starts_new_request else None,
                relevant_definitions,
                model_memory_context(state),
            )
            model_slots = _validated_agent_slots(agent_slots, allowed_slots, taxonomy_definitions)
            slots.update(
                {
                    slot: value
                    for slot, value in model_slots.items()
                    if slot not in deterministic_slots
                }
            )
        except Exception as exc:
            logger.warning("Intent slot model failed; using deterministic fallback: %s", exc)
    return slots


async def clarify_requirements(state: ShoppingState) -> dict[str, Any]:
    """Ask for missing requirements without extracting or changing slot values."""
    category = state.get("product_category")
    required_slots_by_category = state.get("required_slots_by_category")
    allowed_slots_by_category = state.get("allowed_slots_by_category")
    if required_slots_by_category and category in required_slots_by_category:
        required_slots = required_slots_by_category[category]
    elif "required_slots" in state:
        required_slots = state["required_slots"]
    else:
        required_slots = REQUIRED_SLOTS.get(category or "", [])
    if allowed_slots_by_category and category in allowed_slots_by_category:
        allowed_slots = allowed_slots_by_category[category]
    elif "allowed_slots" in state:
        allowed_slots = state["allowed_slots"]
    else:
        allowed_slots = required_slots

    slots = state.get("slots", {})
    if not category:
        result = ClarificationResult(
            status="ASK",
            missing_slots=["productCategory"],
            question=QUESTIONS["productCategory"],
        )
        required_slots, question_slots = [], ["productCategory"]
    else:
        missing = [slot for slot in required_slots if slots.get(slot) in (None, "")]
        question_slots = missing[:2]
        result = ClarificationResult(
            status="ASK" if missing else "READY",
            missing_slots=missing,
            question=_question_for_slots(question_slots, state.get("taxonomy_slot_questions", {}))
            if missing
            else None,
        )
    return {
        "required_slots": required_slots,
        "allowed_slots": allowed_slots,
        "clarification_status": result.status,
        "missing_slots": result.missing_slots,
        "pending_question": {
            "slot": question_slots[0],
            "slots": question_slots,
            "question": result.question or "",
        }
        if result.status == "ASK"
        else None,
    }
