"""
Document Indexer — chunks and embeds SEC filings + transcripts into pgvector.

Embedding model: BAAI/bge-base-en-v1.5 (768 dims, local, ~400MB download on first run).
Storage: Supabase document_chunks table (pgvector).
Idempotent: upserts on (ticker, doc_type, section, chunk_index) unique constraint.
"""

import logging
import re
from datetime import date
from typing import Optional

from dotenv import load_dotenv

from backend.memory.vector_store import upsert_chunks, _get_embed_model

load_dotenv()

logger = logging.getLogger(__name__)

# Chunking constants
_CHAR_TARGET = 2400    # target chunk size in characters
_CHAR_OVERLAP = 400    # overlap between chunks
_CHAR_MIN = 400        # minimum chunk size to avoid tiny orphan chunks
_TURNS_PER_CHUNK = 9   # transcript turns grouped per chunk


def index_documents(
    ticker: str,
    sec_data: dict,
    transcript_data: dict,
    filing_date: Optional[date] = None,
) -> int:
    """
    Index SEC narrative text and earnings transcripts into pgvector.

    Returns total number of chunks upserted.
    Raises on embedding or DB failure (caller should catch and fall back).
    """
    ticker = ticker.upper().strip()
    chunks: list[dict] = []

    # ── SEC filings ───────────────────────────────────────────────────────────
    for form_type in ("10-K", "10-Q"):
        text = sec_data.get(form_type)
        if not text or not isinstance(text, str):
            continue
        if text.startswith("["):
            continue  # error placeholder from sec_fetcher
        chunks.extend(_chunk_sec_text(text, form_type, ticker, filing_date))

    # ── Earnings transcripts ──────────────────────────────────────────────────
    transcripts = transcript_data.get("transcripts", {})
    for quarter_key, t in transcripts.items():
        turns = t.get("turns", [])
        if turns:
            chunks.extend(_chunk_transcript_turns(turns, quarter_key, ticker))
        else:
            # Flat text fallback
            text = t.get("text", "")
            if text and not text.startswith("["):
                chunks.extend(_chunk_text(text, quarter_key, "transcript", ticker, filing_date))

    if not chunks:
        logger.warning("index_documents(%s): no chunks produced — nothing to upsert", ticker)
        return 0

    # ── Embed in batch ────────────────────────────────────────────────────────
    texts = [c["content"] for c in chunks]
    embeddings = _embed_batch(texts)
    for chunk, emb in zip(chunks, embeddings):
        chunk["embedding"] = emb

    upsert_chunks(chunks)
    logger.info("index_documents(%s): upserted %d chunks", ticker, len(chunks))
    return len(chunks)


# ── SEC chunking ──────────────────────────────────────────────────────────────

def _chunk_sec_text(
    formatted_text: str,
    doc_type: str,
    ticker: str,
    filing_date: Optional[date],
) -> list[dict]:
    """
    Re-split on sec_fetcher's '=== Item N ===' section headers, then chunk
    within each section to prevent cross-section contamination.
    """
    # Pattern matches sec_fetcher's existing output format
    pattern = r'\n=== (Item \w+(?:\s+\w+)*) ===\n'
    parts = re.split(pattern, formatted_text)

    # parts alternates: [preamble, section_name, section_text, section_name, ...]
    chunks: list[dict] = []

    # Handle preamble (before first section header)
    if parts and parts[0].strip():
        chunks.extend(_chunk_text(parts[0], "preamble", doc_type, ticker, filing_date))

    # Handle section pairs
    i = 1
    while i + 1 < len(parts):
        section_name = parts[i].strip()
        section_text = parts[i + 1]
        chunks.extend(_chunk_text(section_text, section_name, doc_type, ticker, filing_date))
        i += 2

    return chunks


def _chunk_text(
    text: str,
    section: str,
    doc_type: str,
    ticker: str,
    filing_date: Optional[date],
) -> list[dict]:
    """
    Split text into overlapping chunks at sentence boundaries.
    Target: _CHAR_TARGET chars with _CHAR_OVERLAP overlap.
    """
    text = text.strip()
    if len(text) < _CHAR_MIN:
        if text:
            return [_make_chunk(ticker, doc_type, section, 0, text, filing_date)]
        return []

    # Split on sentence boundaries for cleaner chunks
    sentences = re.split(r'(?<=[.!?])\s+', text)

    chunks: list[dict] = []
    current: list[str] = []
    current_len = 0
    chunk_index = 0

    for sentence in sentences:
        sentence_len = len(sentence)

        if current_len + sentence_len > _CHAR_TARGET and current:
            # Emit current chunk
            chunk_text = " ".join(current)
            if len(chunk_text) >= _CHAR_MIN:
                chunks.append(_make_chunk(ticker, doc_type, section, chunk_index, chunk_text, filing_date))
                chunk_index += 1

            # Overlap: keep last few sentences that fit within _CHAR_OVERLAP
            overlap: list[str] = []
            overlap_len = 0
            for s in reversed(current):
                if overlap_len + len(s) > _CHAR_OVERLAP:
                    break
                overlap.insert(0, s)
                overlap_len += len(s)
            current = overlap
            current_len = overlap_len

        current.append(sentence)
        current_len += sentence_len

    # Emit final chunk
    if current:
        chunk_text = " ".join(current)
        if len(chunk_text) >= _CHAR_MIN:
            chunks.append(_make_chunk(ticker, doc_type, section, chunk_index, chunk_text, filing_date))

    return chunks


# ── Transcript chunking ───────────────────────────────────────────────────────

def _chunk_transcript_turns(
    turns: list[dict],
    quarter_key: str,
    ticker: str,
) -> list[dict]:
    """
    Group transcript turns into chunks of _TURNS_PER_CHUNK turns each.
    Preserves Q&A conversational context within a chunk.
    """
    chunks: list[dict] = []
    chunk_index = 0

    for i in range(0, len(turns), _TURNS_PER_CHUNK):
        group = turns[i : i + _TURNS_PER_CHUNK]
        lines: list[str] = []
        for turn in group:
            speaker = turn.get("speaker", "Unknown")
            title = turn.get("title", "")
            content = turn.get("content", "").strip()
            try:
                sent = float(turn.get("sentiment", 0))
                sent_str = f" [sentiment: {sent:+.1f}]"
            except (ValueError, TypeError):
                sent_str = ""
            header = f"{speaker} ({title}){sent_str}:" if title else f"{speaker}{sent_str}:"
            lines.append(f"{header} {content}")

        chunk_text = "\n".join(lines).strip()
        if len(chunk_text) >= _CHAR_MIN:
            chunks.append(
                _make_chunk(ticker, "transcript", quarter_key, chunk_index, chunk_text, None)
            )
            chunk_index += 1

    return chunks


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_chunk(
    ticker: str,
    doc_type: str,
    section: str,
    chunk_index: int,
    content: str,
    filing_date: Optional[date],
) -> dict:
    """Build a chunk dict with all required fields for upsert_chunks."""
    return {
        "ticker": ticker,
        "doc_type": doc_type,
        "section": section,
        "chunk_index": chunk_index,
        "content": content,
        "token_count": len(content) // 4,  # rough approximation: 1 token ≈ 4 chars
        "filing_date": filing_date.isoformat() if filing_date else None,
    }


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed texts using local SentenceTransformer model. No API call."""
    model = _get_embed_model()
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=False)
    return embeddings.tolist()
