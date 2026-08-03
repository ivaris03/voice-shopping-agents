"""Backward-compatible imports for the split realtime speech adapters."""

from voice_shopping_api.core.text import split_sentences
from voice_shopping_api.realtime.asr import StreamingAsr
from voice_shopping_api.realtime.tts import synthesize_chunks

__all__ = ["StreamingAsr", "split_sentences", "synthesize_chunks"]
