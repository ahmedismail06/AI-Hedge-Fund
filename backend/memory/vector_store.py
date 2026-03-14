"""
Vector Store — thin Supabase wrapper for investment memos.
ChromaDB / semantic search is stubbed (raises NotImplementedError).
"""

import os
from typing import Optional

from supabase import create_client, Client

_client: Optional[Client] = None


def _get_client() -> Client:
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
        _client = create_client(url, key)
    return _client


def store_memo(ticker: str, memo_dict: dict) -> str:
    """
    Insert a memo into the memos table.
    Returns the inserted row's UUID.
    memo_dict must contain keys: date, verdict, conviction_score.
    The full memo (minus _raw_docs) is stored in memo_json; raw fetcher output in raw_docs.
    """
    raw_docs = memo_dict.pop("_raw_docs", None)

    row = {
        "ticker": ticker.upper(),
        "date": memo_dict.get("date"),
        "verdict": memo_dict.get("verdict"),
        "conviction_score": float(memo_dict.get("conviction_score", 0)),
        "memo_json": memo_dict,
        "raw_docs": raw_docs,
        "status": "PENDING",
    }

    result = _get_client().table("memos").insert(row).execute()
    inserted = result.data[0] if result.data else {}
    return inserted.get("id", "")


def get_memo(ticker: str) -> Optional[dict]:
    """Returns the most recent memo for ticker, or None if none exists."""
    result = (
        _get_client()
        .table("memos")
        .select("*")
        .eq("ticker", ticker.upper())
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def get_all_memos(limit: int = 50) -> list[dict]:
    """Returns recent memos (summary fields only) across all tickers."""
    result = (
        _get_client()
        .table("memos")
        .select("id, ticker, date, verdict, conviction_score, status, created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def get_watchlist() -> list[dict]:
    """Returns all memos with status APPROVED or WATCHLIST."""
    result = (
        _get_client()
        .table("memos")
        .select("*")
        .in_("status", ["APPROVED", "WATCHLIST"])
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


def update_memo_status(memo_id: str, status: str) -> None:
    """Updates the status field for a given memo UUID."""
    valid = {"PENDING", "APPROVED", "REJECTED", "WATCHLIST"}
    if status not in valid:
        raise ValueError(f"Invalid status '{status}'. Must be one of {valid}")
    _get_client().table("memos").update({"status": status}).eq("id", memo_id).execute()


def search_similar(query: str, n: int = 5) -> list[dict]:
    raise NotImplementedError("ChromaDB not yet integrated")
