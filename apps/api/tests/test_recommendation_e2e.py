"""商品推荐 Agent 端到端效果测试。

范围：``recommendation_agent`` 内部的 JSONB 过滤 + PGVector Top 20 →
ProfileReranker 画像加权。为隔离"推荐"这一环节，用例直接给出已填槽位，绕过意图识别
与澄清；意图相关节点（响应、下单）不在本套件覆盖内。

依赖：由 ``VOICE_SHOPPING_TEST_DATABASE_URL`` 指定的独立 PostgreSQL/PGVector 测试库与
可选 DashScope Key。测试夹具会在模块开始前执行全部迁移并重新播种该库。

- 数据库不可达时整模块跳过；
- 模型不可用或调用失败时，Agent 按设计降级到确定性路径，过滤类断言不受影响，
  排序类断言按降级路径验证（见 test_rule_penalty_reorders 与
  test_fallback_path_without_model）。
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from langgraph.runtime import Runtime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from voice_shopping_api.agents.nodes.recommendation import recommend_products
from voice_shopping_api.agents.service import _catalog
from voice_shopping_api.agents.state import ShoppingRuntimeDependencies, ShoppingState
from voice_shopping_api.core.queries import PRODUCT_COLUMNS, rows
from voice_shopping_api.core.taxonomy import list_categories
from voice_shopping_api.modules.catalog.profile import profile_snapshot

USER_101 = UUID("00000000-0000-4000-8000-000000000101")  # 小林：耳机画像
USER_102 = UUID("00000000-0000-4000-8000-000000000102")  # 陈晨：咖啡机画像、买过 De'Longhi
USER_103 = UUID("00000000-0000-4000-8000-000000000103")  # 爱丽丝：跑鞋画像
USER_104 = UUID("00000000-0000-4000-8000-000000000104")  # 大卫：冷启动空画像
USER_105 = UUID("00000000-0000-4000-8000-000000000105")  # 埃里克：耳机/手表画像

pytestmark = pytest.mark.e2e

# 数值槽位：商品侧取值 >= 槽位要求即命中（与 service._attribute_condition 一致）。
NUMERIC_SLOTS = frozenset({"batteryHours", "pressureBar", "waterTankMl", "capacityL"})

RESULTS: list[dict[str, Any]] = []


def _digits(value: Any) -> float | None:
    match = re.search(r"\d+", str(value))
    return float(match.group()) if match else None


def _slot_matches(attrs: dict[str, Any], slot: str, value: Any) -> bool:
    """按 JSONB @> / 数值区间语义独立复核单个槽位（与召回 SQL 的实现互相印证）。"""
    if slot == "gender":
        return attrs.get("gender") in ("unisex", value)
    if slot == "size":
        size = attrs.get("size")
        if isinstance(size, list) and len(size) == 2:
            if float(size[0]) <= float(value) <= float(size[1]):
                return True
            return str(value) in [str(item) for item in size]
        if size is not None and size == value:
            return True
        size_range = attrs.get("sizeRange")
        return (
            isinstance(size_range, list)
            and len(size_range) == 2
            and float(size_range[0]) <= float(value) <= float(size_range[1])
        )
    if slot == "waterResistance":
        digits = _digits(attrs.get("waterResistance"))
        return digits is not None and digits >= float(value)
    if slot in NUMERIC_SLOTS:
        return attrs.get(slot) is not None and float(attrs[slot]) >= float(value)
    # 枚举：标量等值或数组包含（对应 JSONB 包含语义，布尔槽位按 bool 比较）。
    attr = attrs.get(slot)
    if attr is None:
        return False
    if isinstance(attr, list):
        return value in attr
    return attr == value


def complies(
    product: dict[str, Any],
    category: str,
    slots: dict[str, Any],
) -> tuple[bool, str]:
    """校验商品是否满足品类、预算与全部已填槽位（过滤合规的独立复核）。

    已填槽位（含可选槽位）全部参与召回硬过滤，因此按全部已填槽位复核。
    """
    if product["category_l2"] != category:
        return False, "category"
    budget = slots.get("budgetMax")
    if budget is not None and float(product["price"]) > float(budget):
        return False, "budgetMax"
    attrs = product.get("attributes") or {}
    for slot, value in slots.items():
        if slot == "budgetMax" or value is None:
            continue
        if not _slot_matches(attrs, slot, value):
            return False, slot
    return True, ""


@pytest_asyncio.fixture(scope="module")
async def session(e2e_engine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(e2e_engine) as db_session:
        yield db_session


@pytest_asyncio.fixture(scope="module")
async def catalog(session: AsyncSession) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            f"""
            SELECT {PRODUCT_COLUMNS}
            FROM products p JOIN merchants m ON m.id = p.merchant_id
            WHERE p.deleted_at IS NULL AND p.status = 'on_sale'
            """
        )
    )
    return {str(row["id"]): row for row in rows(result)}


@pytest_asyncio.fixture(scope="module")
async def required_slots(session: AsyncSession) -> dict[str, list[str]]:
    categories = await list_categories(session)
    return {
        str(category["category_l2"]): list(category["required_slots"]) for category in categories
    }


@pytest.mark.asyncio
async def test_profile_snapshot_uses_static_and_dynamic_profile_fields(
    session: AsyncSession,
) -> None:
    snapshot = await profile_snapshot(session, USER_101)

    assert snapshot["static"]["city"] == "上海"
    assert "budgetBand" not in snapshot["static"]
    assert snapshot["dynamic"]["categoryAffinity"]["HEADPHONES"] == pytest.approx(0.94)
    assert snapshot["dynamic"]["brandAffinity"]["Sony"] == pytest.approx(0.72)
    assert snapshot["dynamic"]["recentViewed"]
    recent_purchased = snapshot["dynamic"]["recentPurchased"]
    assert isinstance(recent_purchased, list)
    assert all(UUID(product_id) for product_id in recent_purchased)


async def run_scenario(
    session: AsyncSession,
    *,
    utterance: str,
    user_id: UUID,
    category: str,
    slots: dict[str, Any],
    required_slots: list[str],
    profile: dict[str, Any] | None = None,
    model_enabled: bool = True,
) -> dict[str, Any]:
    """直接驱动推荐 Agent（召回 + 画像重排），返回运行结果。"""
    snapshot = profile if profile is not None else await profile_snapshot(session, user_id)
    runtime = Runtime(
        context=ShoppingRuntimeDependencies(
            catalog_loader=lambda query, enabled, filters_used: _catalog(
                session, query, enabled, filters_used
            )
        )
    )
    state: ShoppingState = {
        "utterance": utterance,
        "model_enabled": model_enabled,
        "product_category": category,
        "slots": dict(slots),
        "required_slots_by_category": {category: list(required_slots)},
        "user_profile_snapshot": snapshot,
    }
    started = time.perf_counter()
    result = await recommend_products(state, runtime)
    candidates = result["catalog_products"]
    return {
        "candidates": candidates,
        "cards": result.get("product_cards", []),
        "emotion_style": result.get("emotion_style"),
        "vector_used": any(
            float(candidate.get("vector_score") or 0) > 0 for candidate in candidates
        ),
        "elapsed": time.perf_counter() - started,
    }


async def run_and_record(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
    *,
    name: str,
    utterance: str,
    user_id: UUID,
    category: str,
    slots: dict[str, Any],
    profile: dict[str, Any] | None = None,
    model_enabled: bool = True,
) -> dict[str, Any]:
    outcome = await run_scenario(
        session,
        utterance=utterance,
        user_id=user_id,
        category=category,
        slots=slots,
        required_slots=required_slots[category],
        profile=profile,
        model_enabled=model_enabled,
    )
    violations = []
    for product in outcome["candidates"]:
        ok, reason = complies(product, category, slots)
        if not ok:
            violations.append(f"{product['name']} 违反 {reason}")
    compliant = len(violations) == 0
    cards = outcome["cards"]
    card_detail = [
        {
            "name": card["name"],
            "price": card["price"],
            "vector": card["scoreBreakdown"]["vector"],
            "rules": {
                key: value for key, value in card["scoreBreakdown"].items() if key != "vector"
            },
        }
        for card in cards
    ]
    RESULTS.append(
        {
            "name": name,
            "user": str(user_id)[-4:],
            "model": model_enabled,
            "candidate_count": len(outcome["candidates"]),
            "card_count": len(cards),
            "compliant": compliant,
            "violations": violations,
            "vector_used": outcome["vector_used"],
            "elapsed": outcome["elapsed"],
            "cards": card_detail,
            "emotion_style": outcome["emotion_style"],
        }
    )
    return outcome


# ---------------------------------------------------------------------------
# 场景用例：过滤合规（全部候选）、向量 Top 20、画像加权、Top 3
# ---------------------------------------------------------------------------

# 用例名称到简短中文标签，报告用。
RULE_LABELS = {
    "brandHit": "品牌+0.2",
    "priceOverAvgOrderAmount": "超价-0.15",
    "repeatPurchase": "复购-0.3",
}


@pytest.mark.asyncio
async def test_commute_nc_headphones_within_budget(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """101 通勤降噪耳机，预算一千以内：已填槽位（含可选降噪）全部参与硬过滤，
    预算 + 降噪过滤保留所有符合条件的真实型号。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="通勤降噪耳机≤1000",
        utterance="推荐一副通勤降噪耳机，预算一千元以内",
        user_id=USER_101,
        category="HEADPHONES",
        slots={"budgetMax": 1000, "noiseCancellation": True},
    )
    candidates = {product["name"] for product in outcome["candidates"]}
    assert {
        "Sony WH-CH720N 无线降噪头戴耳机",
        "soundcore Liberty 4 NC 真无线降噪耳机",
    } <= candidates
    assert all(
        product["price"] <= 1000 and product["attributes"]["noiseCancellation"]
        for product in outcome["candidates"]
    )


@pytest.mark.asyncio
async def test_default_commute_headphone_slots_keep_a_matching_product(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """The default UI prompt must still match after its required slot answers."""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="默认通勤耳机完整槽位",
        utterance="通勤降噪耳机，预算一千以内。蓝牙，头戴式。",
        user_id=USER_101,
        category="HEADPHONES",
        slots={
            "budgetMax": 1000,
            "noiseCancellation": True,
            "form": "over-ear",
            "connectivity": "bluetooth",
        },
        model_enabled=False,
    )

    candidates = outcome["candidates"]
    assert "Sony WH-CH720N 无线降噪头戴耳机" in {product["name"] for product in candidates}
    assert all(
        product["price"] <= 1000
        and product["attributes"]["noiseCancellation"]
        and product["attributes"]["form"] == "over-ear"
        and product["attributes"]["connectivity"] == "bluetooth"
        for product in candidates
    )


@pytest.mark.asyncio
async def test_wired_headphones_match_only_wired_products(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """101 有线耳机：扩容后的目录应能命中有线枚举值，且不混入无线商品。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="有线耳机",
        utterance="想买有线耳机",
        user_id=USER_101,
        category="HEADPHONES",
        slots={"connectivity": "wired"},
    )
    assert outcome["candidates"]
    assert all(
        product["attributes"]["connectivity"] == "wired" for product in outcome["candidates"]
    )


@pytest.mark.asyncio
async def test_high_cushion_road_shoes_within_budget(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """103 公路高缓震跑鞋预算一千内：terrain/缓震/预算全部硬过滤，
    唯一候选 Nike Pegasus 41，Nike 品牌命中加分。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="公路高缓震跑鞋≤1000",
        utterance="推荐一双公路缓震跑鞋，预算一千元以内",
        user_id=USER_103,
        category="RUNNING_SHOES",
        slots={"budgetMax": 1000, "terrain": "road", "cushion": "high"},
    )
    candidates = {product["name"] for product in outcome["candidates"]}
    assert candidates == {"Nike Pegasus 41 跑鞋"}
    assert [card["name"] for card in outcome["cards"]] == ["Nike Pegasus 41 跑鞋"]
    assert outcome["cards"][0]["scoreBreakdown"]["brandHit"] == 0.2  # Nike 品牌偏好


@pytest.mark.asyncio
async def test_flat_foot_stability_shoe(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """103 扁平足跑鞋：footType（可选槽位）参与硬过滤，唯一候选
    ASICS GEL-Kayano 31，ASICS 品牌命中加分。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="扁平足稳定跑鞋",
        utterance="扁平足适合穿什么跑鞋",
        user_id=USER_103,
        category="RUNNING_SHOES",
        slots={"footType": "flat"},
    )
    candidates = {product["name"] for product in outcome["candidates"]}
    assert candidates == {"ASICS GEL-Kayano 31 跑鞋"}
    assert outcome["cards"][0]["name"] == "ASICS GEL-Kayano 31 跑鞋"
    assert outcome["cards"][0]["scoreBreakdown"]["brandHit"] == 0.2  # ASICS 品牌偏好


@pytest.mark.asyncio
async def test_cold_start_female_shoes_rank_by_vector_only(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """104 冷启动女款跑鞋：性别过滤保留女款/中性款，且不叠加画像规则。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="冷启动女款跑鞋",
        utterance="推荐一双女款跑鞋",
        user_id=USER_104,
        category="RUNNING_SHOES",
        slots={"gender": "female"},
    )
    assert outcome["candidates"]
    assert {product["attributes"]["gender"] for product in outcome["candidates"]} <= {
        "female",
        "unisex",
    }
    for card in outcome["cards"]:
        assert set(card["scoreBreakdown"]) == {"vector"}  # 冷启动：无画像分


@pytest.mark.asyncio
async def test_water_resistant_watches_match_minimum_threshold(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """105 防水手表：数值型 waterResistance 参与硬过滤，不返回低于阈值的商品。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="防水手表≥50米",
        utterance="想买一块防水手表",
        user_id=USER_105,
        category="WATCHES",
        slots={"waterResistance": 50},
    )
    assert outcome["candidates"]
    assert all(
        _slot_matches(product["attributes"], "waterResistance", 50)
        for product in outcome["candidates"]
    )


@pytest.mark.asyncio
async def test_capsule_coffee_machine(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """102 胶囊咖啡机：type 枚举过滤，只保留真实的胶囊咖啡机型号。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="胶囊咖啡机",
        utterance="推荐一台胶囊咖啡机",
        user_id=USER_102,
        category="COFFEE_MACHINE",
        slots={"type": "capsule"},
    )
    candidates = outcome["candidates"]
    assert "Nespresso Essenza Mini C30 胶囊咖啡机" in {product["name"] for product in candidates}
    assert all(product["attributes"]["type"] == "capsule" for product in candidates)


@pytest.mark.asyncio
async def test_semi_auto_with_steam_wand_excludes_repeat_purchase_from_top_three(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """102 半自动带蒸汽棒：硬过滤保留已购商品，但复购惩罚将其移出 Top 3。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="半自动+蒸汽棒（复购）",
        utterance="想要一台带蒸汽棒的半自动咖啡机",
        user_id=USER_102,
        category="COFFEE_MACHINE",
        slots={"type": "semi-automatic", "steamWand": True},
        model_enabled=False,
    )
    dedica_name = "De'Longhi Dedica EC685 半自动咖啡机"
    dedica = next(product for product in outcome["candidates"] if product["name"] == dedica_name)
    snapshot = await profile_snapshot(session, USER_102)
    assert str(dedica["id"]) in snapshot["dynamic"]["recentPurchased"]
    assert dedica_name not in {card["name"] for card in outcome["cards"]}


@pytest.mark.asyncio
async def test_rule_penalty_reorders_top_choice_deterministically(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """复购惩罚只改变第二阶段排序，不改变 SQL 的硬过滤候选集合。"""
    without_profile = await run_scenario(
        session,
        utterance="公路缓震跑鞋",
        user_id=USER_103,
        category="RUNNING_SHOES",
        slots={},
        required_slots=required_slots["RUNNING_SHOES"],
        profile={"static": {}},
        model_enabled=False,
    )
    assert len(without_profile["cards"]) == 3
    assert all(set(card["scoreBreakdown"]) == {"vector"} for card in without_profile["cards"])
    baseline_card = without_profile["cards"][0]

    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="复购惩罚翻转排序",
        utterance="公路缓震跑鞋",
        user_id=USER_103,
        category="RUNNING_SHOES",
        slots={},
        profile={
            "static": {},
            "dynamic": {
                "recentPurchased": [baseline_card["productId"]],
            },  # 与 profile_snapshot 一致：字符串列表
        },
        model_enabled=False,
    )
    assert [product["id"] for product in outcome["candidates"]] == [
        product["id"] for product in without_profile["candidates"]
    ]
    baseline_ids = [card["productId"] for card in without_profile["cards"]]
    reranked_ids = [card["productId"] for card in outcome["cards"]]
    assert reranked_ids[:2] == baseline_ids[1:]
    assert baseline_card["productId"] not in reranked_ids


@pytest.mark.asyncio
async def test_enabled_kettle_products_are_recommendable(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """扩容后的所有演示店铺均启用，1.5 升及以上水壶应可被推荐。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="1.5 升水壶",
        utterance="推荐一个恒温水壶",
        user_id=USER_101,
        category="ELECTRIC_KETTLE",
        slots={"capacityL": 1.5},
    )
    assert "Xiaomi Mi Smart Kettle Pro 电水壶" in {
        product["name"] for product in outcome["candidates"]
    }
    assert all(
        _slot_matches(product["attributes"], "capacityL", 1.5) for product in outcome["candidates"]
    )


@pytest.mark.asyncio
async def test_fallback_path_without_model_is_deterministic(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """无模型兜底：硬过滤仍生效，向量分为 0，品牌命中加 0.2。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="无模型兜底跑鞋",
        utterance="推荐一双公路缓震跑鞋，预算一千元以内",
        user_id=USER_103,
        category="RUNNING_SHOES",
        slots={"budgetMax": 1000, "terrain": "road", "cushion": "high"},
        model_enabled=False,
    )
    assert outcome["vector_used"] is False  # 无 embedding，走 created_at 排序
    assert [card["name"] for card in outcome["cards"]] == ["Nike Pegasus 41 跑鞋"]
    assert outcome["cards"][0]["scoreBreakdown"]["vector"] == 0.0
    assert outcome["cards"][0]["matchScore"] == pytest.approx(0.2, abs=0.001)


@pytest.mark.asyncio
async def test_matte_lipstick_filter(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """104 冷启动雾面口红：finish 枚举过滤不应混入缎光或镜面商品。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="雾面口红",
        utterance="推荐一支雾面口红",
        user_id=USER_104,
        category="LIPSTICK",
        slots={"finish": "matte"},
    )
    assert outcome["candidates"]
    assert all(product["attributes"]["finish"] == "matte" for product in outcome["candidates"])


@pytest.mark.asyncio
async def test_automatic_watch_filter_matches_only_automatic_products(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """105 机械手表：movement 枚举过滤只返回自动机械商品。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="机械手表",
        utterance="想买一块机械手表",
        user_id=USER_105,
        category="WATCHES",
        slots={"movement": "automatic"},
    )
    assert outcome["candidates"]
    assert all(
        product["attributes"]["movement"] == "automatic" for product in outcome["candidates"]
    )
    assert any(product["brand"] == "Seiko" for product in outcome["candidates"])


def test_recommendation_agent_report() -> None:
    """汇总报告：在全部场景执行后打印召回/精排/规则效果一览。"""
    if not RESULTS:
        pytest.skip("无场景执行")
    print("\n===== 商品推荐 Agent 端到端效果报告 =====")
    header = (
        f"{'用例':<20}{'用户':<6}{'模式':<6}{'候选':<5}{'卡数':<4}"
        f"{'合规':<5}{'向量':<5}{'耗时':<8}期望 Top-3"
    )
    print(header)
    print("-" * 120)
    for result in RESULTS:
        top3 = " / ".join(
            f"{card['name']}(向量{card['vector']:.2f}"
            + ("".join(f",{label}" for key, label in RULE_LABELS.items() if key in card["rules"]))
            + ")"
            for card in result["cards"]
        )
        print(
            f"{result['name']:<20}{result['user']:<6}"
            f"{'模型' if result['model'] else '兜底':<6}"
            f"{result['candidate_count']:<5}{result['card_count']:<4}"
            f"{'✓' if result['compliant'] else '✗':<5}"
            f"{'✓' if result['vector_used'] else '-':<5}"
            f"{result['elapsed'] * 1000:>6.0f}ms {top3}"
        )
        for violation in result["violations"]:
            print(f"    ✗ 过滤违规：{violation}")
    passed = sum(1 for r in RESULTS if r["compliant"])
    print("-" * 120)
    model_runs = sum(1 for r in RESULTS if r["model"])
    print(
        f"共 {len(RESULTS)} 个场景，过滤合规 {passed}/{len(RESULTS)}；"
        f"模型路径 {model_runs} 个，兜底路径 {len(RESULTS) - model_runs} 个。"
    )
