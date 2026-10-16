"""So khớp tiêu đề bài báo để gộp các bài cùng 1 sự kiện (nhiều báo đưa cùng 1 tin) — dùng chung cho
Mục 6 báo cáo (report_generator._dedupe_similar_articles) và email tin nóng (hot_news_email)."""
import re
from typing import Iterable


def title_tokens(title: str) -> set:
    return {w for w in re.findall(r"\w+", (title or "").lower()) if len(w) > 2}


def is_similar_title(tokens: set, others: Iterable[set]) -> bool:
    """Ngưỡng: Jaccard ≥ 0,5 hoặc phủ ≥ 0,75 bộ từ của tiêu đề ngắn hơn (tối thiểu 4 từ)."""
    for other in others:
        inter = len(tokens & other)
        if not inter:
            continue
        smaller = min(len(tokens), len(other))
        if inter / len(tokens | other) >= 0.5 or (smaller >= 4 and inter / smaller >= 0.75):
            return True
    return False
