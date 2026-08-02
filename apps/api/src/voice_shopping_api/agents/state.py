from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

IntentType = Literal[
    "PRODUCT_RECOMMENDATION",
    "PRODUCT_ORDER",
    "PRODUCT_COMPARE",
    "PRODUCT_QUERY",
    "CHAT",
    "UNSUPPORTED_REQUEST",
]
OrderAction = Literal["CREATE", "CONFIRM", "CANCEL"]


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentResult(AgentModel):
    type: IntentType
    confidence: float = Field(ge=0, le=1)
    action: OrderAction | None = None
    product_category: str | None = None


class ClarificationResult(AgentModel):
    status: Literal["ASK", "READY"]
    slots: dict[str, Any]
    missing_slots: list[str]
    question: str | None = None


class SlotExtractionResult(AgentModel):
    slots: dict[str, Any]


class ProductRecommendationResult(AgentModel):
    product_cards: list[dict[str, Any]]
    emotion_style: str


class ProductReason(AgentModel):
    product_id: str
    reason: str


class EmotionalResponseResult(AgentModel):
    reasons: list[ProductReason]
    speech_text: str


class Intent(TypedDict, total=False):
    type: IntentType
    confidence: float
    action: OrderAction
    product_category: str


# LangGraph keeps one shared, flat state mapping.  The mixins below deliberately
# keep that wire format while making each field's lifetime and owner explicit.
# Nesting these values would make every node replace an entire sub-dictionary on
# each update, which is both noisy and easy to get wrong with StateGraph's
# default reducer.
class TurnState(TypedDict, total=False):
    """Inputs that belong only to the turn currently being processed."""

    session_id: str
    turn_id: str
    user_id: str
    utterance: str
    conversation_history: list[str]
    model_enabled: bool


class ConversationState(TypedDict, total=False):
    """Cross-turn dialogue facts, produced by intent and clarification nodes."""

    intent: Intent
    product_category: str
    category_changed: bool
    required_slots: list[str]
    slots: dict[str, Any]
    clarification_status: Literal["ASK", "READY"]
    missing_slots: list[str]
    pending_question: dict[str, Any] | None


class TaxonomyState(TypedDict, total=False):
    """Read-only taxonomy data loaded for this turn; never persisted."""

    required_slots_by_category: dict[str, list[str]]
    allowed_slots_by_category: dict[str, list[str]]
    taxonomy_slot_definitions: dict[str, dict[str, Any]]
    taxonomy_slot_definitions_by_category: dict[str, dict[str, dict[str, Any]]]
    taxonomy_slot_questions: dict[str, str]
    taxonomy_category_names: dict[str, str]
    taxonomy_categories: list[dict[str, Any]]


class RecommendationState(TypedDict, total=False):
    """Retrieval and ranking data for the current recommendation path."""

    user_profile_snapshot: dict[str, Any]
    catalog_products: list[dict[str, Any]]
    product_cards: list[dict[str, Any]]
    previous_product_cards: list[dict[str, Any]]
    emotion_style: str


class OrderState(TypedDict, total=False):
    """The latest pending or completed order linked to this conversation."""

    pending_order: dict[str, Any] | None


class ResponseState(TypedDict, total=False):
    """Presentation output produced during this turn."""

    reasons: list[dict[str, str]]
    speech_text: str
    final_reply: str
    compliance_blocked: bool


CatalogLoader = Callable[[str, bool], Awaitable[list[dict[str, Any]]]]
OrderHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ShoppingWorkflowContext:
    """Non-persisted dependencies available to workflow nodes for one turn."""

    catalog_loader: CatalogLoader
    order_handler: OrderHandler | None = None


class ShoppingState(
    TurnState,
    ConversationState,
    TaxonomyState,
    RecommendationState,
    OrderState,
    ResponseState,
    total=False,
):
    """The StateGraph contract, grouped above by lifecycle instead of by node."""


# Only durable conversation facts are carried to the next request.  In
# particular, taxonomy, candidate products, profiles and generated text are all
# per-turn data and must not leak into the next graph invocation.
PERSISTED_STATE_KEYS = frozenset(
    {
        "product_category",
        "slots",
        "pending_question",
        "product_cards",
        "emotion_style",
        "pending_order",
    }
)


def carry_forward_state(previous: ShoppingState) -> ShoppingState:
    """Select and copy the stable part of a stored state snapshot.

    This intentionally also migrates legacy flat snapshots: unknown keys,
    including the removed ``intents`` and ``action_queue`` fields, are dropped.
    """

    return {
        key: value
        for key, value in previous.items()
        if key in PERSISTED_STATE_KEYS
    }


def state_for_persistence(state: ShoppingState) -> ShoppingState:
    """Create the small business snapshot stored in ``session_states``."""

    return carry_forward_state(state)
