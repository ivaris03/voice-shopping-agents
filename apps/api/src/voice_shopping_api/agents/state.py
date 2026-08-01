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


class ShoppingState(TypedDict, total=False):
    session_id: str
    turn_id: str
    user_id: str
    utterance: str
    conversation_history: list[str]
    model_enabled: bool
    intents: list[Intent]
    action_queue: list[str]
    product_category: str
    category_changed: bool
    required_slots: list[str]
    required_slots_by_category: dict[str, list[str]]
    taxonomy_slot_definitions: dict[str, dict[str, Any]]
    taxonomy_slot_questions: dict[str, str]
    taxonomy_category_names: dict[str, str]
    taxonomy_categories: list[dict[str, Any]]
    slots: dict[str, Any]
    clarification_status: Literal["ASK", "READY"]
    missing_slots: list[str]
    pending_question: dict[str, Any] | None
    user_profile_snapshot: dict[str, Any]
    catalog_products: list[dict[str, Any]]
    product_cards: list[dict[str, Any]]
    previous_product_cards: list[dict[str, Any]]
    emotion_style: str
    reasons: list[dict[str, str]]
    pending_order: dict[str, Any] | None
    speech_text: str
    final_reply: str
    compliance_blocked: bool
