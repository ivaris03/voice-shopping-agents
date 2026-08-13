import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from voice_shopping_api.agents.state import carry_forward_state, state_for_persistence
from voice_shopping_api.modules.catalog.profile import (
    extract_static_profile_candidates,
    merge_static_profile_patches,
    normalize_static_profile_patch,
    update_dynamic_profile_from_turn,
)
from voice_shopping_api.schemas.domain import UserProfileStaticPatch


def test_profile_candidates_extract_high_confidence_facts_from_a_turn() -> None:
    candidates = extract_static_profile_candidates(
        "我今年32岁，住在上海，平时是干皮。",
        {"budgetMax": 1000, "skinType": "dry"},
    )

    assert candidates == {
        "age": 32,
        "city": "上海",
        "skin_type": "dry",
    }


def test_product_gender_preference_does_not_become_user_gender() -> None:
    assert extract_static_profile_candidates("我想买女款跑鞋。") == {}


def test_budget_max_is_not_a_static_profile_candidate() -> None:
    assert extract_static_profile_candidates("预算两千元以下", {"budgetMax": 2000}) == {}


def test_explicit_profile_facts_override_dialogue_and_never_clear_values() -> None:
    merged = merge_static_profile_patches(
        {"age": 28, "city": "上海", "locale": "zh_cn"},
        {"age": 31, "city": "", "locale": "en_us"},
    )

    assert merged == {"age": 31, "city": "上海", "locale": "en_us"}


@pytest.mark.parametrize("field", ["budget", "budgetBand", "budget_band"])
def test_static_profile_rejects_budget_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="budgetMax"):
        UserProfileStaticPatch.model_validate({field: 2000})


def test_profile_patch_rejects_values_that_would_violate_static_table_checks() -> None:
    assert normalize_static_profile_patch({"age": 121, "heightCm": 20, "weightKg": 301}) == {}


def test_profile_updates_are_the_only_profile_fact_carried_between_turns() -> None:
    state = {
        "user_profile_updates": {"age": 32},
        "user_profile_snapshot": {"static": {"age": 31}},
        "catalog_products": [{"id": "not-persisted"}],
    }

    persisted = state_for_persistence(state)
    carried = carry_forward_state(persisted)

    assert carried == {"user_profile_updates": {"age": 32}}


def test_completed_clarification_clears_its_pending_question_before_persistence() -> None:
    persisted = state_for_persistence(
        {
            "clarification_status": "READY",
            "pending_question": {"slot": "connectivity"},
            "slots": {"form": "over-ear", "connectivity": "bluetooth"},
        }
    )

    assert persisted["pending_question"] is None


@pytest.mark.asyncio
async def test_new_shopping_request_weakly_updates_dynamic_category_affinity() -> None:
    class Result:
        def __init__(self, value: object = None) -> None:
            self.value = value

        def scalar_one_or_none(self) -> object:
            return self.value

    class Session:
        def __init__(self) -> None:
            self.results = [Result(USER_ID), Result({"WATCHES": 0.2}), Result()]
            self.calls: list[dict[str, object] | None] = []

        async def execute(self, _statement: object, params: dict[str, object] | None = None):
            self.calls.append(params)
            return self.results.pop(0)

    USER_ID = UUID("00000000-0000-0000-0000-000000000101")
    session = Session()

    changed = await update_dynamic_profile_from_turn(session, USER_ID, "WATCHES")

    assert changed is True
    assert json.loads(str(session.calls[-1]["category_affinity"])) == {"WATCHES": 0.24}


@pytest.mark.asyncio
async def test_dynamic_profile_ignores_turn_without_a_category() -> None:
    assert await update_dynamic_profile_from_turn(None, UUID(int=1), None) is False
