"""
Fed Scraper — scrapes the most recent FOMC statement from the Federal Reserve website.

The extracted text is passed to the Macro Agent for qualitative Fed tone analysis via Claude.
Caches the statement text in-memory for the duration of the current calendar day to avoid
redundant HTTP requests during intraday re-runs.
"""

from dotenv import load_dotenv

load_dotenv()

import logging
import os
import re
from datetime import date

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_BASE_URL = "https://www.federalreserve.gov"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
REQUEST_TIMEOUT = 10  # seconds
MAX_TEXT_CHARS = 6000  # truncate statement text to avoid token overflow

# ── In-memory cache ───────────────────────────────────────────────────────────

_cached_fed_text: str = ""
_cache_date: str = ""  # YYYY-MM-DD of when cache was populated


# ── Internal helpers ──────────────────────────────────────────────────────────


def _fetch_latest_statement_url() -> str | None:
    """
    Scrape the FOMC calendar page and return the URL of the most recent
    FOMC statement press release.

    Returns the fully-qualified URL string, or None if no matching link
    is found or if the request fails.
    """
    try:
        response = requests.get(
            FOMC_CALENDAR_URL,
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to fetch FOMC calendar page: %s", exc)
        return None

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        # Find all anchor tags whose visible text contains "Statement"
        statement_links = soup.find_all(
            "a", string=re.compile(r"Statement", re.IGNORECASE)
        )

        # Keep only links pointing to press-release pages
        filtered = [
            tag
            for tag in statement_links
            if tag.get("href") and "/newsevents/pressreleases/" in tag["href"]
        ]

        if not filtered:
            logger.warning("No FOMC statement links found on calendar page.")
            return None

        # The page lists meetings chronologically; the last matching link is
        # the most recent statement.
        href = filtered[-1]["href"]

        if href.startswith("/"):
            href = FOMC_BASE_URL + href

        return href

    except Exception as exc:
        logger.warning("Error parsing FOMC calendar HTML: %s", exc)
        return None


def _extract_statement_text(url: str) -> str:
    """
    Fetch an FOMC statement page and extract the human-readable paragraph text.

    Tries several CSS selectors in priority order to locate the main content
    container, then joins all <p> tags found within it. Returns at most
    MAX_TEXT_CHARS characters. Returns an empty string on any failure.
    """
    try:
        response = requests.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to fetch FOMC statement page %s: %s", url, exc)
        return ""

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        # Try selectors in priority order
        container = (
            soup.find("div", {"id": "content"})
            or soup.find("div", class_=re.compile(r"col-xs-12"))
            or soup.find("div", class_="article")
            or soup.find("body")
        )

        if container is None:
            logger.warning("No content container found on statement page %s", url)
            return ""

        paragraphs = container.find_all("p")
        if not paragraphs:
            logger.warning("No <p> tags found in content container on %s", url)
            return ""

        text = "\n\n".join(p.get_text(separator=" ", strip=True) for p in paragraphs)
        return text[:MAX_TEXT_CHARS]

    except Exception as exc:
        logger.warning("Error parsing FOMC statement HTML from %s: %s", url, exc)
        return ""


# ── Public API ────────────────────────────────────────────────────────────────


def get_fed_text() -> str:
    """
    Return the text of the most recent FOMC statement, fetching it from the
    Federal Reserve website if the in-memory cache is stale or empty.

    Caches the result for the remainder of the current calendar day.  On any
    fetch failure the function degrades gracefully: it returns whatever text is
    already in the cache (which may be an empty string).

    This function is the single entry point used by macro_agent.py.
    """
    global _cached_fed_text, _cache_date

    today = date.today().isoformat()

    if _cache_date == today and _cached_fed_text:
        logger.debug("Returning cached Fed statement (%d chars).", len(_cached_fed_text))
        return _cached_fed_text

    url = _fetch_latest_statement_url()

    if url:
        text = _extract_statement_text(url)
    else:
        text = ""

    if len(text) > 100:
        _cached_fed_text = text
        _cache_date = today
        logger.info(
            "Fed statement fetched: %d chars from %s",
            len(text),
            url,
        )
        return text

    logger.warning("Fed statement fetch failed — returning cached or empty")
    return _cached_fed_text
