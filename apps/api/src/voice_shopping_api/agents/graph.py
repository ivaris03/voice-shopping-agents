from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from voice_shopping_api.agents.nodes.clarification import clarify_requirements
from voice_shopping_api.agents.nodes.intent import recognize_intent
from voice_shopping_api.agents.nodes.recommendation import recommend_products
from voice_shopping_api.agents.nodes.response import (
    compliance_check,
    emotional_response,
    order_response,
    publish_response,
    violation_response,
)
from voice_shopping_api.agents.state import (
    ShoppingContext,
    ShoppingInputState,
    ShoppingOutputState,
    ShoppingState,
)


def _route_intent(state: ShoppingState) -> str:
    intent = (state.get("intent") or {}).get("type")
    if intent == "PRODUCT_RECOMMENDATION":
        return "clarify"
    if intent in ("PRODUCT_COMPARE", "PRODUCT_QUERY"):
        return "recommend"
    if intent == "PRODUCT_ORDER":
        return "order"
    if state.get("pending_question") and intent == "UNSUPPORTED_REQUEST":
        return "clarify"
    return "respond"


def _route_clarification(state: ShoppingState) -> str:
    return "recommend" if state.get("clarification_status") == "READY" else "respond"


def _route_compliance(state: ShoppingState) -> str:
    return "violation" if state.get("compliance_blocked") else "publish"


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
    graph.add_node("compliance_check", compliance_check)
    graph.add_node("violation_response", violation_response)
    graph.add_node("publish_response", publish_response)
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
    graph.add_conditional_edges(
        "compliance_check",
        _route_compliance,
        {"violation": "violation_response", "publish": "publish_response"},
    )
    graph.add_edge("violation_response", "publish_response")
    graph.add_edge("publish_response", END)
    return graph.compile(checkpointer=checkpointer)


shopping_workflow = build_workflow()
