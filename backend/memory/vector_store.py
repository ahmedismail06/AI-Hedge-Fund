"""
Vector Store — Supabase wrapper for investment memos and document chunks (pgvector).

Memo operations: store_memo, get_memo, get_all_memos, get_watchlist, update_memo_status.
Chunk operations: upsert_chunks, search_similar (pgvector cosine similarity).
Embedding model: BAAI/bge-base-en-v1.5 (768 dims, local SentenceTransformers).
"""

import logging
import os
from typing import Optional, TYPE_CHECKING

from dotenv import load_dotenv
from supabase import create_client, Client

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

load_dotenv()

logger = logging.getLogger(__name__)

_client: Optional[Client] = None
_embed_model: Optional["SentenceTransformer"] = None

_EMBED_MODEL_NAME = "BAAI/bge-base-en-v1.5"


def _get_client() -> Client:
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
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


def _get_embed_model() -> "SentenceTransformer":
    """Lazy singleton: loads BAAI/bge-base-en-v1.5 on first call (~400MB download cached locally)."""
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model %s (first call — may download ~400MB)", _EMBED_MODEL_NAME)
        _embed_model = SentenceTransformer(_EMBED_MODEL_NAME)
    return _embed_model


def upsert_chunks(chunks: list[dict]) -> None:
    """
    Bulk upsert document chunks into document_chunks table.
    Each chunk dict must have: ticker, doc_type, section, chunk_index, content, embedding.
    Idempotent: upserts on (ticker, doc_type, section, chunk_index) unique constraint.
    """
    if not chunks:
        return
    _get_client().table("document_chunks").upsert(
        chunks,
        on_conflict="ticker,doc_type,section,chunk_index",
    ).execute()


def search_similar(
    query: str,
    ticker: str,
    doc_types: Optional[list[str]] = None,
    n: int = 4,
) -> list[dict]:
    """
    Cosine similarity search over document_chunks for a given ticker.

    Args:
        query: natural language query string
        ticker: stock ticker to filter by
        doc_types: optional list of '10-K', '10-Q', 'transcript' to filter by
        n: max number of results (capped at 8)

    Returns:
        list of dicts with keys: id, ticker, doc_type, section, chunk_index,
        content, token_count, filing_date, similarity
    """
    n = min(n, 8)
    model = _get_embed_model()
    embedding: list[float] = model.encode([query], show_progress_bar=False)[0].tolist()

    try:
        result = _get_client().rpc(
            "match_document_chunks",
            {
                "query_embedding": embedding,
                "filter_ticker": ticker.upper(),
                "filter_doc_types": doc_types if doc_types else None,
                "match_count": n,
            },
        ).execute()
        return result.data or []
    except Exception as exc:
        logger.warning("search_similar(%s, %s): pgvector RPC failed — %s", ticker, query[:50], exc)
        return []
