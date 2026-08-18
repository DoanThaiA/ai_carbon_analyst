import re
from typing import List

CHUNK_MAX_CHARS = 1000
CHUNK_MIN_CHARS = 200  

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS, min_chars: int = CHUNK_MIN_CHARS) -> List[str]:
    """Trả về danh sách đoạn văn (str), mỗi đoạn tối đa ~max_chars ký tự."""
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]
    if not paragraphs:
        return []

    units: List[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            units.append(paragraph)
        else:
            units.extend(_split_long_paragraph(paragraph, max_chars))

    chunks = _greedy_merge(units, max_chars)
    return _merge_short_tail(chunks, min_chars)


def _split_long_paragraph(paragraph: str, max_chars: int) -> List[str]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
    return _greedy_merge(sentences, max_chars) if sentences else [paragraph]


def _greedy_merge(units: List[str], max_chars: int) -> List[str]:
    chunks: List[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _merge_short_tail(chunks: List[str], min_chars: int) -> List[str]:
    if len(chunks) >= 2 and len(chunks[-1]) < min_chars:
        chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}"
        chunks.pop()
    return chunks
