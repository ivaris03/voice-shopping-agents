from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from voice_shopping_api.agents.nodes.clarification import clarify_requirements
from voice_shopping_api.agents.nodes.intent import recognize_intent
from voice_shopping_api.agents.nodes.recommendation import recommend_products, retrieve_catalog
from voice_shopping_api.agents.nodes.response import (
    compliance_check,
    emotional_response,
    order_response,
)
from voice_shopping_api.agents.state import ShoppingState, ShoppingWorkflowContext


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
    return "retrieve" if state.get("clarification_status") == "READY" else "respond"


def build_workflow(*, checkpointer: BaseCheckpointSaver | None = None):
    """Assemble the graph; business rules remain inside the individual nodes."""
    graph = StateGraph(ShoppingState, context_schema=ShoppingWorkflowContext)
    graph.add_node("intent_agent", recognize_intent)
    graph.add_node("clarification_agent", clarify_requirements)
    graph.add_node("catalog_retrieval", retrieve_catalog)
    graph.add_node("recommendation_agent", recommend_products)
    graph.add_node("order_node", order_response)
    graph.add_node("emotional_agent", emotional_response)
    graph.add_node("compliance_check", compliance_check)
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
        {"retrieve": "catalog_retrieval", "respond": "emotional_agent"},
    )
    graph.add_edge("catalog_retrieval", "recommendation_agent")
    graph.add_edge("recommendation_agent", "emotional_agent")
    graph.add_edge("order_node", "compliance_check")
    graph.add_edge("emotional_agent", "compliance_check")
    graph.add_edge("compliance_check", END)
    return graph.compile(checkpointer=checkpointer)


shopping_workflow = build_workflow()
