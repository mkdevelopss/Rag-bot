from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    text: str
    chunk_index: int
    page_number: int | None


# Preferred split points, strongest first. Chunks end on the strongest
# boundary found in the last part of the window instead of mid-word.
_SEPARATORS = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")


def _find_break(text: str, start: int, end: int, min_size: int) -> int:
    lower = start + min_size
    for separator in _SEPARATORS:
        position = text.rfind(separator, lower, end)
        if position != -1:
            return position + len(separator)
    return end


def _align_start(text: str, position: int, limit: int) -> int:
    """Move an overlap start forward to the next word so chunks don't begin mid-word."""
    if position <= 0 or text[position - 1].isspace():
        return position
    for index in range(position, min(limit, len(text))):
        if text[index].isspace():
            return index + 1
    return position


def chunk_text(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 150,
    page_number: int | None = None,
) -> list[TextChunk]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [TextChunk(text=text, chunk_index=0, page_number=page_number)]

    pieces: list[str] = []
    start = 0
    min_size = max(1, int(chunk_size * 0.6))
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            end = _find_break(text, start, end, min_size)
        piece = text[start:end]
        if piece.strip():
            pieces.append(piece)
        if end >= len(text):
            break
        next_start = max(end - chunk_overlap, start + 1)
        start = _align_start(text, next_start, end)

    return [
        TextChunk(text=piece.strip(), chunk_index=index, page_number=page_number)
        for index, piece in enumerate(pieces)
    ]
