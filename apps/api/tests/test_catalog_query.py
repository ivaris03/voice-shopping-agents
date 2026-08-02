"""召回 SQL 动态拼接的纯函数测试（不依赖数据库）。"""

import json

from voice_shopping_api.agents.service import _build_catalog_query
from voice_shopping_api.agents.state import CatalogFilters

EMBEDDING = "[0.1, 0.2]"


def build(
    filters: CatalogFilters | None = None, *, embedding: str | None = EMBEDDING
) -> tuple[str, dict[str, object]]:
    return _build_catalog_query(filters or {}, embedding=embedding)


def test_vector_path_uses_hnsw_shape() -> None:
    sql, params = build()
    assert "ORDER BY p.embedding <=> CAST(:embedding AS vector)" in sql
    assert "p.embedding IS NOT NULL" in sql
    assert "LIMIT 20" in sql
    assert params["embedding"] == EMBEDDING


def test_fallback_path_orders_by_created_at() -> None:
    sql, params = build(embedding=None)
    assert "ORDER BY p.created_at DESC" in sql
    assert "p.embedding IS NOT NULL" not in sql
    assert "LIMIT 20" in sql
    assert params["embedding"] is None


def test_base_visibility_conditions() -> None:
    sql, _ = build()
    for condition in (
        "p.deleted_at IS NULL",
        "p.status = 'on_sale'",
        "p.stock > 0",
        "m.deleted_at IS NULL",
        "m.is_enabled",
    ):
        assert condition in sql


def test_category_and_budget_conditions() -> None:
    sql, params = build({"category": "HEADPHONES", "slots": {"budgetMax": 1000}})
    assert "p.category_l2 = :category" in sql
    assert params["category"] == "HEADPHONES"
    assert "p.price <= CAST(:budget AS numeric)" in sql
    assert params["budget"] == "1000"


def test_gender_unisex_condition() -> None:
    sql, params = build({"slots": {"gender": "male"}, "required_slots": ["gender"]})
    assert "p.attributes->>'gender' = 'unisex'" in sql
    assert params["slots_gender"] == "male"


def test_size_falls_back_to_size_range() -> None:
    sql, params = build({"slots": {"size": 42.0}, "required_slots": ["size"]})
    assert "p.attributes ? 'size'" in sql
    assert "p.attributes ? 'sizeRange'" in sql
    assert params["slots_size"] == "42.0"
    assert params["slots_size_num"] == 42.0
    assert params["slots_size_json"] == json.dumps("42.0")


def test_numeric_slot_ge_comparison() -> None:
    sql, params = build({"slots": {"batteryHours": "20"}, "required_slots": ["batteryHours"]})
    assert "(p.attributes->>'batteryHours')::float8 >= :slots_batteryHours" in sql
    assert params["slots_batteryHours"] == 20.0


def test_water_resistance_digits_extraction() -> None:
    sql, params = build({"slots": {"waterResistance": 100}, "required_slots": ["waterResistance"]})
    assert "regexp_replace(p.attributes->>'waterResistance'" in sql
    assert params["slots_waterResistance"] == 100.0


def test_enum_uses_jsonb_containment() -> None:
    sql, params = build({"slots": {"form": "over-ear"}, "required_slots": ["form"]})
    assert "p.attributes->'form' @> CAST(:slots_form_json AS jsonb)" in sql
    assert params["slots_form_json"] == json.dumps("over-ear")


def test_boolean_slot_serializes_as_json_boolean() -> None:
    # 布尔槽位必须序列化为 JSON true/false，而非 "True"/"False" 字符串，
    # 否则 JSONB @> 与商品属性的布尔值永远不匹配，召回恒为空。
    sql, params = build(
        {"slots": {"noiseCancellation": True}, "required_slots": ["noiseCancellation"]}
    )
    assert params["slots_noiseCancellation_json"] == "true"
    assert (
        "p.attributes->'noiseCancellation' @> CAST(:slots_noiseCancellation_json AS jsonb)" in sql
    )


def test_unfilled_slots_are_ignored() -> None:
    sql, params = build({"slots": {"form": None}})
    assert "'form'" not in sql
    assert set(params) == {"embedding"}


def test_filled_optional_slots_participate_in_hard_filter() -> None:
    # 已填槽位（含可选槽位）全部参与召回硬过滤。
    sql, params = build({"slots": {"noiseCancellation": True, "color": "red"}})
    assert (
        "p.attributes->'noiseCancellation' @> CAST(:slots_noiseCancellation_json AS jsonb)"
        in sql
    )
    assert "p.attributes->'color' @> CAST(:slots_color_json AS jsonb)" in sql
    assert params["slots_noiseCancellation_json"] == "true"
    assert params["slots_color_json"] == json.dumps("red")


def test_multiple_filled_slots_stack_conditions() -> None:
    sql, params = build(
        {
            "slots": {"gender": "female", "terrain": "road"},
            "required_slots": ["gender", "size", "terrain"],
        }
    )
    assert "slots_gender" in sql
    assert "slots_terrain_json" in sql
    assert "slots_size" not in sql  # size 未填
    assert params["slots_gender"] == "female"
    assert params["slots_terrain_json"] == json.dumps("road")
