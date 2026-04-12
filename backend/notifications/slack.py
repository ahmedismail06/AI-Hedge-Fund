"""
Core Slack webhook sender.

All agents call notify_event() from events.py, which calls post_slack() here.
Never raises — all failures are logged as warnings.
"""

import json
import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
_SLACK_MENTION_USER_ID = os.getenv("SLACK_MENTION_USER_ID")  # e.g. "U12345678" — mentions you on every message

# Color palette
COLOR_CRITICAL = "#E01E5A"   # red
COLOR_WARNING  = "#ECB22E"   # orange/yellow
COLOR_SUCCESS  = "#2EB67D"   # green
COLOR_INFO     = "#868e96"   # gray


def post_slack(
    title: str,
    fields: list[dict],
    color: str,
    text: str = "",
) -> None:
    """
    POST a formatted Slack message via incoming webhook.

    Args:
        title:  Bold header line in the attachment
        fields: List of {"title": str, "value": str, "short": bool} dicts
        color:  Left-border color (use COLOR_* constants)
        text:   Optional plain-text fallback shown in notifications
    """
    if not _SLACK_WEBHOOK_URL:
        logger.debug("SLACK_WEBHOOK_URL not set — Slack notification skipped: %s", title)
        return

    mention = f"<@{_SLACK_MENTION_USER_ID}> " if _SLACK_MENTION_USER_ID else ""
    payload = {
        "text": mention + (text or title),
        "attachments": [
            {
                "color": color,
                "title": title,
                "fields": fields,
                "footer": "AI Hedge Fund",
                "ts": int(time.time()),
            }
        ],
    }

    try:
        resp = requests.post(
            _SLACK_WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning(
                "Slack webhook returned %d for '%s': %s",
                resp.status_code,
                title,
                resp.text,
            )
        else:
            logger.debug("Slack notification sent: %s", title)
    except requests.RequestException as exc:
        logger.warning("Slack webhook failed for '%s': %s", title, exc)
