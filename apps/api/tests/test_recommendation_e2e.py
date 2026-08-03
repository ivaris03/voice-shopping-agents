"""商品推荐 Agent 端到端效果测试。

范围：``catalog_retrieval``（召回 SQL）→ ``recommendation_agent``（LLM 精排 +
画像规则二次排序）。为隔离"推荐"这一环节，用例直接给出已填槽位，绕过意图识别
与澄清；意图相关节点（响应、下单）不在本套件覆盖内。

依赖：本地演示库（``.env`` 中的 VOICE_SHOPPING_DATABASE_URL）与可选 DashScope Key。

- 数据库不可达时整模块跳过；
- 模型不可用或调用失败时，Agent 按设计降级到确定性路径，过滤类断言不受影响，
  精排类断言按降级路径验证（见 test_rule_penalty_reorders 与
  test_fallback_path_without_model）。
"""

from __future__ import annotations

import re
import time
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from langgraph.runtime import Runtime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from voice_shopping_api.agents.nodes.recommendation import recommend_products, retrieve_catalog
from voice_shopping_api.agents.service import _catalog
from voice_shopping_api.agents.state import ShoppingState, ShoppingWorkflowContext
from voice_shopping_api.core.config import get_settings
from voice_shopping_api.core.queries import PRODUCT_COLUMNS, rows
from voice_shopping_api.core.taxonomy import list_categories
from voice_shopping_api.modules.catalog.profile import profile_snapshot

USER_101 = UUID("00000000-0000-4000-8000-000000000101")  # 小林：耳机画像
USER_102 = UUID("00000000-0000-4000-8000-000000000102")  # 陈晨：咖啡机画像、买过山岚
USER_103 = UUID("00000000-0000-4000-8000-000000000103")  # 爱丽丝：跑鞋画像
USER_104 = UUID("00000000-0000-4000-8000-000000000104")  # 大卫：冷启动空画像
USER_105 = UUID("00000000-0000-4000-8000-000000000105")  # 埃里克：耳机/手表画像

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
async def session() -> AsyncSession:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - 数据库不可达时整模块跳过
        pytest.skip(f"本地演示数据库不可达，跳过推荐 Agent 端到端测试：{exc}")
    async with AsyncSession(engine) as db_session:
        yield db_session
    await engine.dispose()


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
    assert snapshot["static"]["budgetBand"] == "mid"
    assert snapshot["dynamic"]["categoryAffinity"]["HEADPHONES"] == pytest.approx(0.94)
    assert snapshot["dynamic"]["brandAffinity"]["云雀"] == pytest.approx(0.72)
    assert snapshot["dynamic"]["recentViewed"]
    assert snapshot["dynamic"]["recentPurchased"] == []


def find_product(catalog: dict[str, dict[str, Any]], keyword: str) -> dict[str, Any]:
    matches = [row for row in catalog.values() if keyword in str(row["name"])]
    assert len(matches) == 1, f"期望唯一匹配「{keyword}」，实际 {len(matches)} 个"
    return matches[0]


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
    """直接驱动 召回 → 精排 两个节点（推荐 Agent 本体），返回运行结果。"""
    snapshot = profile if profile is not None else await profile_snapshot(session, user_id)
    runtime = Runtime(
        context=ShoppingWorkflowContext(
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
    retrieved = await retrieve_catalog(state, runtime)
    candidates = retrieved["catalog_products"]
    result = await recommend_products({**state, **retrieved})
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
            "reranker": card["scoreBreakdown"]["reranker"],
            "rules": {
                key: value for key, value in card["scoreBreakdown"].items() if key != "reranker"
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
# 场景用例：过滤合规（全部候选）、召回集合、精排 top-3、规则二次排序
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
    预算 + 降噪过滤后只剩云雀 Air 与潮汐 Pro。"""
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
    assert candidates == {
        "云雀 Air 降噪耳机",
        "潮汐 Pro 真无线耳机",
    }
    for card in outcome["cards"]:
        assert card["scoreBreakdown"]["brandHit"] == 0.2  # 用户画像品牌命中


@pytest.mark.asyncio
async def test_wired_headphones_return_nothing(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """101 有线耳机：演示库无线缆商品，召回为空且不报错。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="有线耳机（空结果）",
        utterance="想买有线耳机",
        user_id=USER_101,
        category="HEADPHONES",
        slots={"connectivity": "wired"},
    )
    assert outcome["candidates"] == []
    assert outcome["cards"] == []
    assert outcome["emotion_style"] == "helpful-apologetic"


@pytest.mark.asyncio
async def test_high_cushion_road_shoes_within_budget(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """103 公路高缓震跑鞋预算一千内：terrain/缓震/预算全部硬过滤，
    唯一候选 Nike Pegasus 40，Nike 品牌命中加分。"""
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
    assert candidates == {"Nike Pegasus 40 缓震跑鞋"}
    assert [card["name"] for card in outcome["cards"]] == ["Nike Pegasus 40 缓震跑鞋"]
    assert outcome["cards"][0]["scoreBreakdown"]["brandHit"] == 0.2  # Nike 品牌偏好


@pytest.mark.asyncio
async def test_flat_foot_stability_shoe(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """103 扁平足跑鞋：footType（可选槽位）参与硬过滤，唯一候选
    Asics Gel-Kayano 30，Asics 品牌命中加分。"""
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
    assert candidates == {"Asics Gel-Kayano 30 稳定跑鞋"}
    assert outcome["cards"][0]["name"] == "Asics Gel-Kayano 30 稳定跑鞋"
    assert outcome["cards"][0]["scoreBreakdown"]["brandHit"] == 0.2  # Asics 品牌偏好


@pytest.mark.asyncio
async def test_cold_start_female_shoes_rank_by_reranker_only(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """104 冷启动女款跑鞋：空画像无规则分，女款鞋应在 top-3 且无规则加分。"""
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
    nb = find_product(catalog, "New Balance FuelCell")["name"]
    assert nb in {card["name"] for card in outcome["cards"]}
    for card in outcome["cards"]:
        assert set(card["scoreBreakdown"]) == {"reranker"}  # 冷启动：无规则分


@pytest.mark.asyncio
async def test_water_resistant_watches_rank_divers(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """105 防水手表：waterResistance（可选槽位）参与硬过滤，30 米防水两款被
    排除剩 8 款；200 米防水的潜水腕表/防震款应进精排 top-3。"""
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
    assert len(outcome["candidates"]) == 8  # 两款 30 米防水被硬过滤
    top3 = [card["name"] for card in outcome["cards"]]
    # 潜水腕表/防震款是最贴题的防水款，应出现在 top-3
    assert "Seiko Prospex 潜水腕表" in top3 or "Casio G-Shock GA-2100" in top3


@pytest.mark.asyncio
async def test_capsule_coffee_machine(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """102 胶囊咖啡机：type 枚举过滤，唯一候选晨雾 Mini，品牌命中加分。"""
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
    assert [card["name"] for card in outcome["cards"]] == ["晨雾 Mini 胶囊咖啡机"]
    assert outcome["cards"][0]["scoreBreakdown"]["brandHit"] == 0.2  # 晨雾品牌偏好


@pytest.mark.asyncio
async def test_semi_auto_with_steam_wand_penalizes_repeat_purchase(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """102 半自动带蒸汽棒：type 与布尔槽位 steamWand 均参与硬过滤出山岚，
    山岚命中最近购买惩罚 -0.3。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="半自动+蒸汽棒（复购）",
        utterance="想要一台带蒸汽棒的半自动咖啡机",
        user_id=USER_102,
        category="COFFEE_MACHINE",
        slots={"type": "semi-automatic", "steamWand": True},
    )
    assert [card["name"] for card in outcome["cards"]] == ["山岚半自动咖啡机"]
    breakdown = outcome["cards"][0]["scoreBreakdown"]
    assert breakdown["repeatPurchase"] == -0.3  # 90 天内买过山岚
    assert "priceOverAvgOrderAmount" not in breakdown  # 1699 < 1699 * 1.5


@pytest.mark.asyncio
async def test_rule_penalty_reorders_top_choice_deterministically(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """规则效果：兜底路径下，复购惩罚把 Pegasus 39 从第 1 压到第 3。

    全品类 10 款跑鞋词法分同为 0.52，阶段一按库存取前三：
    Pegasus 39(60) > Pegasus 40(50) > Clifton 9(40)。叠加平均客单价 400
    （>600 扣 0.15）与 Pegasus 39 复购惩罚（-0.3）后，阶段二排序为：
    Pegasus 40(0.37) > Clifton(0.37) > Pegasus 39(0.22)，可见翻转。
    注意：规则只重排阶段一前三，不能把第 4 名提进前三。
    """
    pegasus_39_id = find_product(catalog, "Pegasus 39")["id"]
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
    assert [card["name"] for card in without_profile["cards"]] == [
        "Nike Pegasus 39 入门跑鞋",
        "Nike Pegasus 40 缓震跑鞋",
        "HOKA Clifton 9 轻量缓震跑鞋",
    ]
    assert all(
        card["matchScore"] == pytest.approx(0.52, abs=0.001)
        and set(card["scoreBreakdown"]) == {"reranker"}
        for card in without_profile["cards"]
    )

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
                "avgOrderAmount": 400.0,
                "recentPurchased": [str(pegasus_39_id)],
            },  # 与 profile_snapshot 一致：字符串列表
        },
        model_enabled=False,
    )
    names = [card["name"] for card in outcome["cards"]]
    assert names[2] == "Nike Pegasus 39 入门跑鞋"  # 复购 0.22 跌到第三
    assert names[0] == "Nike Pegasus 40 缓震跑鞋"  # 0.37 升到第一
    assert names[1] == "HOKA Clifton 9 轻量缓震跑鞋"  # 0.37
    pegasus_40 = next(
        card for card in outcome["cards"] if card["name"].startswith("Nike Pegasus 40")
    )
    pegasus_39 = next(
        card for card in outcome["cards"] if card["name"].startswith("Nike Pegasus 39")
    )
    clifton = next(card for card in outcome["cards"] if "Clifton" in card["name"])
    assert pegasus_39["matchScore"] == pytest.approx(0.22, abs=0.001)  # 0.52 - 0.30
    assert pegasus_39["scoreBreakdown"] == {
        "reranker": pytest.approx(0.52, abs=0.001),
        "repeatPurchase": -0.3,
    }
    assert pegasus_40["matchScore"] == pytest.approx(0.37, abs=0.001)  # 0.52 - 0.15
    assert pegasus_40["scoreBreakdown"] == {
        "reranker": pytest.approx(0.52, abs=0.001),
        "priceOverAvgOrderAmount": -0.15,
    }
    assert clifton["matchScore"] == pytest.approx(0.37, abs=0.001)  # 0.52 - 0.15
    assert clifton["scoreBreakdown"] == {
        "reranker": pytest.approx(0.52, abs=0.001),
        "priceOverAvgOrderAmount": -0.15,
    }


@pytest.mark.asyncio
async def test_disabled_merchant_products_are_invisible(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """禁用店铺商品不可见：恒温水壶唯一在售款来自已禁用店铺，召回必须为空。"""
    outcome = await run_and_record(
        session,
        catalog,
        required_slots,
        name="禁用店铺水壶（不可见）",
        utterance="推荐一个恒温水壶",
        user_id=USER_101,
        category="ELECTRIC_KETTLE",
        slots={"capacityL": 1.5},
    )
    # 商品本身在售（存在于 catalog 快照），排除只能来自店铺禁用条件。
    assert "清泉恒温水壶" in {row["name"] for row in catalog.values()}
    assert outcome["candidates"] == []
    assert outcome["cards"] == []


@pytest.mark.asyncio
async def test_fallback_path_without_model_is_deterministic(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """无模型兜底：硬过滤仍生效（缓震为已填可选槽位），词法分 0.52 +
    品牌命中 0.2，确定性返回唯一候选 Pegasus 40。"""
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
    assert [card["name"] for card in outcome["cards"]] == ["Nike Pegasus 40 缓震跑鞋"]
    assert outcome["cards"][0]["scoreBreakdown"]["reranker"] == pytest.approx(
        0.52, abs=0.001
    )
    assert outcome["cards"][0]["matchScore"] == pytest.approx(0.72, abs=0.001)  # 0.52 + 品牌 0.2


@pytest.mark.asyncio
async def test_matte_lipstick_filter(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """104 冷启动雾面口红：finish 枚举过滤出 4 支哑光款，缎光 Dior 被排除。"""
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
    assert len(outcome["candidates"]) == 4
    assert "Dior 烈艳蓝金口红 999" not in {p["name"] for p in outcome["candidates"]}


@pytest.mark.asyncio
async def test_automatic_watch_filter_prefers_favorite_brand(
    session: AsyncSession,
    catalog: dict[str, dict[str, Any]],
    required_slots: dict[str, list[str]],
) -> None:
    """105 机械手表：movement 枚举过滤出 5 支，Seiko 品牌偏好应至少进一支 top-3。"""
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
    assert len(outcome["candidates"]) == 5
    top3 = [card["name"] for card in outcome["cards"]]
    assert any("Seiko" in name for name in top3)  # 画像偏好 Seiko（0.42）


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
            f"{card['name']}(精排{card['reranker']:.2f}"
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
