"""
News Fetcher
Fetches the 20 most recent news articles for a ticker from Polygon.io.
Filters to the last 30 days.
"""

import os
from datetime import datetime, timedelta, timezone

import requests

POLYGON_BASE = "https://api.polygon.io"


def fetch_news(ticker: str) -> dict:
    """
    Returns:
    {
        "ticker": str,
        "articles": [
            {
                "headline": str,
                "published_utc": str,
                "article_url": str,
                "description": str | None,
                "source": "polygon",
                "sentiment_hint": None
            }
        ],
        "count": int,
        "error": None | "error message"
    }
    An empty articles list is not an error.
    Never raises.
    """
    result: dict = {
        "ticker": ticker.upper(),
        "articles": [],
        "count": 0,
        "error": None,
    }
    try:
        api_key = os.getenv("POLYGON_API_KEY")
        if not api_key:
            result["error"] = "POLYGON_API_KEY not set"
            return result

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=30)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        params = {
            "ticker": ticker.upper(),
            "limit": 20,
            "order": "desc",
            "published_utc.gte": cutoff_str,
            "apiKey": api_key,
        }
        resp = requests.get(
            f"{POLYGON_BASE}/v2/reference/news",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for item in data.get("results", []):
            articles.append({
                "headline": item.get("title", ""),
                "published_utc": item.get("published_utc", ""),
                "article_url": item.get("article_url", ""),
                "description": item.get("description"),
                "source": "polygon",
                "sentiment_hint": None,
            })

        result["articles"] = articles
        result["count"] = len(articles)

    except Exception as exc:
        result["error"] = str(exc)

    return result
