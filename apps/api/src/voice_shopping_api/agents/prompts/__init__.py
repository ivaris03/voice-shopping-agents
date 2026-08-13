"""System prompts and model instructions for shopping agents."""

from voice_shopping_api.agents.prompts.clarification import SLOT_EXTRACTION_SYSTEM_PROMPT
from voice_shopping_api.agents.prompts.emotional import (
    EMOTIONAL_RESPONSE_SYSTEM_PROMPT,
    PRODUCT_REASON_BATCH_SYSTEM_PROMPT,
    RECOMMENDATION_HOOK_SYSTEM_PROMPT,
)
from voice_shopping_api.agents.prompts.intent import build_intent_system_prompt

__all__ = [
    "SLOT_EXTRACTION_SYSTEM_PROMPT",
    "EMOTIONAL_RESPONSE_SYSTEM_PROMPT",
    "PRODUCT_REASON_BATCH_SYSTEM_PROMPT",
    "RECOMMENDATION_HOOK_SYSTEM_PROMPT",
    "build_intent_system_prompt",
]
