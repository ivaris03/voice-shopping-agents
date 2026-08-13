from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

IntentType = Literal[
    "REQUIREMENT_CLARIFICATION",
    "PRODUCT_RECOMMENDATION",
    "PRODUCT_COMPARE",
    "PRODUCT_ORDER",
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


class RecommendationHook(AgentModel):
    hook: str


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
    starts_new_product_request: bool


class ConversationState(TypedDict, total=False):
    """Cross-turn dialogue facts, produced by intent and clarification nodes."""

    intent: Intent
    product_category: str | None
    category_changed: bool
    required_slots: list[str]
    allowed_slots: list[str]
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


class ProfileState(TypedDict, total=False):
    """Profile and semantic memories injected or collected during the turn."""

    user_profile_updates: dict[str, Any]
    semantic_memories: list[dict[str, Any]]


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
    violation_sentence: str | None
    reasons_streamed: bool
    speech_streamed: bool
    speech_audio_streamed: bool


class ShoppingInputState(TypedDict, total=False):
    """Data accepted when starting or resuming one shopping turn.

    LangGraph filters graph input through this schema before it reaches the
    internal state.  Runtime-only dependencies belong to
    ``ShoppingRuntimeDependencies`` instead of this mapping.
    """

    session_id: str
    turn_id: str
    user_id: str
    utterance: str
    conversation_history: list[str]
    model_enabled: bool

    # Cross-turn facts and deterministic inputs needed to seed this turn.
    product_category: str | None
    slots: dict[str, Any]
    pending_question: dict[str, Any] | None
    user_profile_updates: dict[str, Any]
    user_profile_snapshot: dict[str, Any]
    previous_product_cards: list[dict[str, Any]]
    product_cards: list[dict[str, Any]]
    pending_order: dict[str, Any] | None
    required_slots: list[str]
    allowed_slots: list[str]

    # The service loads these read-only values for the current turn.  They are
    # internal graph inputs and are intentionally omitted from the output.
    required_slots_by_category: dict[str, list[str]]
    allowed_slots_by_category: dict[str, list[str]]
    taxonomy_slot_definitions: dict[str, dict[str, Any]]
    taxonomy_slot_definitions_by_category: dict[str, dict[str, dict[str, Any]]]
    taxonomy_slot_questions: dict[str, str]
    taxonomy_category_names: dict[str, str]
    taxonomy_categories: list[dict[str, Any]]
    catalog_products: list[dict[str, Any]]


class ShoppingOutputState(TypedDict, total=False):
    """Public result of a completed shopping turn.

    Draft speech, taxonomy, catalog candidates, session metadata and profile
    snapshots remain internal state.  In particular, ``final_reply`` is the
    post-compliance value that can be sent to the client.
    """

    intent: Intent
    starts_new_product_request: bool
    product_category: str | None
    category_changed: bool
    required_slots: list[str]
    allowed_slots: list[str]
    slots: dict[str, Any]
    clarification_status: Literal["ASK", "READY"]
    missing_slots: list[str]
    pending_question: dict[str, Any] | None
    user_profile_updates: dict[str, Any]
    product_cards: list[dict[str, Any]]
    emotion_style: str
    pending_order: dict[str, Any] | None
    reasons: list[dict[str, str]]
    final_reply: str
    compliance_blocked: bool
    violation_sentence: str | None
    reasons_streamed: bool
    speech_streamed: bool
    speech_audio_streamed: bool


class CatalogFilters(TypedDict, total=False):
    """Deterministic recall constraints pushed into the catalog SQL."""

    category: str | None
    slots: dict[str, Any]
    required_slots: list[str]


CatalogLoader = Callable[[str, bool, CatalogFilters], Awaitable[list[dict[str, Any]]]]
OrderHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
SpeechDeltaPublisher = Callable[[str], Awaitable[None]]
SpeechSentencePublisher = Callable[[str, int, int], Awaitable[None]]
ReasonPublisher = Callable[[ProductReason], Awaitable[None]]
ProfileLoader = Callable[[], Awaitable[dict[str, Any]]]
ProfileUpdater = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ShoppingRuntimeDependencies:
    """Non-persisted dependencies available to workflow nodes for one turn."""

    catalog_loader: CatalogLoader
    order_handler: OrderHandler | None = None
    reason_publisher: ReasonPublisher | None = None
    speech_delta_publisher: SpeechDeltaPublisher | None = None
    speech_sentence_publisher: SpeechSentencePublisher | None = None
    profile_loader: ProfileLoader | None = None
    profile_updater: ProfileUpdater | None = None


# Alias used when describing the graph's context boundary.  Keep the longer
# name as the public compatibility name used by existing nodes and tests.
ShoppingContext = ShoppingRuntimeDependencies


class ShoppingState(
    TurnState,
    ConversationState,
    TaxonomyState,
    ProfileState,
    RecommendationState,
    OrderState,
    ResponseState,
    total=False,
):
    """The StateGraph contract, grouped above by lifecycle instead of by node."""


# Only durable business facts are carried to the next request. Taxonomy,
# candidate products, the read-only profile snapshot and generated text are
# per-turn data; order details remain in the orders domain table.
PERSISTED_STATE_KEYS = frozenset(
    {
        "product_category",
        "slots",
        "user_profile_updates",
        "pending_question",
        "product_cards",
    }
)
BUSINESS_STATE_VERSION = 1


def carry_forward_state(previous: ShoppingState) -> ShoppingState:
    """Select and copy the stable part of a stored state snapshot.

    This intentionally also migrates legacy flat snapshots: unknown keys,
    including the removed ``intents`` and ``action_queue`` fields, are dropped.
    """

    return {key: value for key, value in previous.items() if key in PERSISTED_STATE_KEYS}


def state_for_persistence(state: ShoppingState) -> ShoppingState:
    """Create the business projection stored in ``session_states``."""

    persisted = carry_forward_state(state)
    if state.get("clarification_status") == "READY":
        # A completed clarification must not route a later unrelated turn back
        # through the question that was just answered.
        persisted["pending_question"] = None
    return persisted


OUTPUT_STATE_KEYS = tuple(ShoppingOutputState.__annotations__)


def state_for_output(state: ShoppingState) -> ShoppingOutputState:
    """Project complete graph state onto the LangGraph output contract."""

    return {key: state[key] for key in OUTPUT_STATE_KEYS if key in state}
