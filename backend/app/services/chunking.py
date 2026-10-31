from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    text: str
    chunk_index: int
    page_number: int | None


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

    starts = list(range(0, len(text), chunk_size - chunk_overlap))
    ranges = [(start, min(start + chunk_size, len(text))) for start in starts]
    if len(ranges) > 1 and ranges[-1][1] - ranges[-1][0] < min(120, chunk_size // 4):
        ranges.pop()
        start = max(ranges[-1][0] + 1, len(text) - chunk_size)
        ranges.append((start, len(text)))

    return [
        TextChunk(text=text[start:end], chunk_index=index, page_number=page_number)
        for index, (start, end) in enumerate(ranges)
        if text[start:end].strip()
    ]
