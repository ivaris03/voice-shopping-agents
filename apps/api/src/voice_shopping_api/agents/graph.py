from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from voice_shopping_api.agents.nodes.clarification import clarify_requirements
from voice_shopping_api.agents.nodes.intent import recognize_intent
from voice_shopping_api.agents.nodes.recommendation import recommend_products
from voice_shopping_api.agents.nodes.response import (
    compliance_node,
    emotional_response,
    order_response,
)
from voice_shopping_api.agents.state import (
    ShoppingContext,
    ShoppingInputState,
    ShoppingOutputState,
    ShoppingState,
)


def _route_start(state: ShoppingState) -> str:
    """Classify every turn before deciding whether it answers a pending question."""
    return "intent"


def _route_intent(state: ShoppingState) -> str:
    intent = (state.get("intent") or {}).get("type")
    if intent == "PRODUCT_ORDER":
        return "order"
    if intent in ("PRODUCT_RECOMMENDATION", "REQUIREMENT_CLARIFICATION"):
        return "clarify"
    if intent == "PRODUCT_COMPARE":
        return "recommend"
    return "respond"


def _route_clarification(state: ShoppingState) -> str:
    return "recommend" if state.get("clarification_status") == "READY" else "respond"


def build_workflow(*, checkpointer: BaseCheckpointSaver | None = None):
    """Assemble the graph; business rules remain inside the individual nodes."""
    graph = StateGraph(
        ShoppingState,
        input_schema=ShoppingInputState,
        output_schema=ShoppingOutputState,
        context_schema=ShoppingContext,
    )
    graph.add_node("intent_agent", recognize_intent)
    graph.add_node("clarification_agent", clarify_requirements)
    graph.add_node("recommendation_agent", recommend_products)
    graph.add_node("order_node", order_response)
    graph.add_node("emotional_agent", emotional_response)
    graph.add_node("compliance_node", compliance_node)
    graph.add_conditional_edges(
        START,
        _route_start,
        {"intent": "intent_agent", "clarify": "clarification_agent"},
    )
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
    graph.add_edge("order_node", "compliance_node")
    graph.add_edge("emotional_agent", "compliance_node")
    graph.add_edge("compliance_node", END)
    return graph.compile(checkpointer=checkpointer)


shopping_workflow = build_workflow()
