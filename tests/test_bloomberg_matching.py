from datetime import datetime, timezone

from crawl_news.bloomberg_crawler import _rank_gnews_results

RSS_DATE = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
TITLE = "Container Ship Sinks in Strait of Hormuz After Collision"


def _entry(title, published, link, source="Yahoo Finance"):
    return {"title": f"{title} - {source}", "published": published, "link": link, "source": {"title": source}}


def test_rejects_old_or_unrelated_gnews_results():
    entries = [
        _entry(TITLE, "Tue, 14 Oct 2026 09:00:00 GMT", "https://news.google.com/ok"),
        _entry("Indian-flagged vessel sinks off Oman", "Thu, 14 May 2026 09:00:00 GMT", "https://news.google.com/old"),
        _entry(TITLE, "Tue, 29 Oct 2024 09:00:00 GMT", "https://news.google.com/same-title-old"),
        _entry(TITLE, "", "https://news.google.com/no-date"),
    ]
    assert _rank_gnews_results(entries, TITLE, RSS_DATE) == ["https://news.google.com/ok"]
