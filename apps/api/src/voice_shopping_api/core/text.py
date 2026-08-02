"""Text segmentation helpers shared by model, ASR, and TTS streaming."""

_SENTENCE_ENDINGS = frozenset("，,。！？!?；;\n")
_SENTENCE_CLOSERS = frozenset("。！？!?；;)]}》」』”’\"'）】")


def _boundary_end(text: str, index: int) -> int | None:
    character = text[index]
    if character not in _SENTENCE_ENDINGS and not (
        character == "." and (index + 1 == len(text) or text[index + 1].isspace())
    ):
        return None
    end = index + 1
    while end < len(text) and text[end] in _SENTENCE_CLOSERS:
        end += 1
    return end


def take_completed_sentences(text_value: str) -> tuple[list[str], str]:
    """Return completed short sentences and the unfinished remainder."""
    sentences: list[str] = []
    start = 0
    for index in range(len(text_value)):
        end = _boundary_end(text_value, index)
        if end is None:
            continue
        sentence = text_value[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = end
    return sentences, text_value[start:]


def split_sentences(text_value: str) -> list[str]:
    """Split text into speakable short sentences while preserving punctuation."""
    sentences, remainder = take_completed_sentences(text_value.strip())
    tail = remainder.strip()
    if tail:
        sentences.append(tail)
    return sentences
