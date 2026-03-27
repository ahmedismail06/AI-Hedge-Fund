"""
Smoke tests for backend/memory/document_indexer.py

Coverage:
- _chunk_text: short text → single chunk; long text → multiple chunks
- _chunk_sec_text: Item headers → no cross-section contamination
- _chunk_transcript_turns: 20 turns → groups of 9, each >= 400 chars
- _make_chunk: correct dict keys and token_count approximation
- index_documents: mocked upsert_chunks + _embed_batch, returns chunk count
"""

from datetime import date
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Stub the heavy vector_store imports before loading document_indexer so
# tests run without Supabase credentials or downloading the embedding model.
# We only stub vector_store — the real backend.memory package is preserved.
# ---------------------------------------------------------------------------
import sys
import types

# Only stub the vector_store module, not the whole package
_fake_vs = types.ModuleType("backend.memory.vector_store")
_fake_vs.upsert_chunks = MagicMock()
_fake_vs._get_embed_model = MagicMock(return_value=MagicMock())
_fake_vs._get_client = MagicMock()  # needed by screening_agent import chain
sys.modules["backend.memory.vector_store"] = _fake_vs

from backend.memory.document_indexer import (  # noqa: E402
    _chunk_text,
    _chunk_sec_text,
    _chunk_transcript_turns,
    _make_chunk,
    index_documents,
    _CHAR_MIN,
    _CHAR_TARGET,
    _TURNS_PER_CHUNK,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHORT_TEXT = "Revenue grew 12% year-over-year."  # well under 400 chars

def _long_text(chars: int = 6000) -> str:
    """Generate deterministic text longer than _CHAR_TARGET (2400 chars)."""
    sentence = "The company reported strong results driven by product expansion. "
    return (sentence * (chars // len(sentence) + 1))[:chars]


def _make_turn(speaker: str, content: str) -> dict:
    return {"speaker": speaker, "title": "CEO", "content": content, "sentiment": 0.5}


def _fat_turn(idx: int) -> dict:
    """Turn with enough content that 9 turns easily exceed 400 chars."""
    content = f"Turn {idx}: " + ("This quarter performance was solid and guidance raised. " * 5)
    return _make_turn("Management", content)


# ---------------------------------------------------------------------------
# _chunk_text tests
# ---------------------------------------------------------------------------

def test_chunk_text_short_returns_single_chunk():
    """Text < _CHAR_MIN (400) → exactly one chunk (not empty)."""
    result = _chunk_text(_SHORT_TEXT, "overview", "10-K", "AAPL", None)
    assert len(result) == 1, f"Expected 1 chunk, got {len(result)}"
    assert result[0]["content"] == _SHORT_TEXT


def test_chunk_text_empty_returns_empty():
    """Empty string → no chunks."""
    result = _chunk_text("", "overview", "10-K", "AAPL", None)
    assert result == []


def test_chunk_text_long_returns_multiple_chunks():
    """Text >> _CHAR_TARGET → more than one chunk, each >= _CHAR_MIN chars."""
    text = _long_text(8000)
    result = _chunk_text(text, "mda", "10-K", "MSFT", date(2025, 1, 1))
    assert len(result) > 1, "Expected multiple chunks for long text"
    for chunk in result:
        assert len(chunk["content"]) >= _CHAR_MIN, (
            f"Chunk shorter than _CHAR_MIN: {len(chunk['content'])} chars"
        )


def test_chunk_text_long_increments_chunk_index():
    """chunk_index should be 0, 1, 2, ... for successive chunks."""
    text = _long_text(8000)
    result = _chunk_text(text, "mda", "10-K", "TSLA", None)
    indices = [c["chunk_index"] for c in result]
    assert indices == list(range(len(result))), f"Non-sequential indices: {indices}"


# ---------------------------------------------------------------------------
# _chunk_sec_text tests
# ---------------------------------------------------------------------------

def _make_sec_text() -> str:
    """Build a minimal SEC-formatted text with two Item sections."""
    item7_body = ("Management discussion content. " * 60)  # ~1800 chars each
    item7a_body = ("Quantitative risk disclosures. " * 60)
    return (
        f"\n=== Item 7 ===\n{item7_body}"
        f"\n=== Item 7A ===\n{item7a_body}"
    )


def test_chunk_sec_text_splits_on_headers():
    """Sections chunked separately — Item 7A content must not appear in Item 7 chunks."""
    text = _make_sec_text()
    chunks = _chunk_sec_text(text, "10-K", "NVDA", date(2025, 3, 1))

    item7_chunks = [c for c in chunks if c["section"] == "Item 7"]
    item7a_chunks = [c for c in chunks if c["section"] == "Item 7A"]

    assert item7_chunks, "Expected at least one chunk for Item 7"
    assert item7a_chunks, "Expected at least one chunk for Item 7A"

    # No cross-contamination: Item 7 chunks must not contain Item 7A text
    for chunk in item7_chunks:
        assert "Quantitative risk" not in chunk["content"], (
            "Item 7A content leaked into Item 7 chunk"
        )

    # Symmetric: Item 7A chunks must not contain Item 7 text
    for chunk in item7a_chunks:
        assert "Management discussion" not in chunk["content"], (
            "Item 7 content leaked into Item 7A chunk"
        )


def test_chunk_sec_text_section_label_preserved():
    """section field must exactly match the header name from the filing text."""
    text = _make_sec_text()
    chunks = _chunk_sec_text(text, "10-K", "AMD", None)
    sections = {c["section"] for c in chunks}
    assert "Item 7" in sections
    assert "Item 7A" in sections


# ---------------------------------------------------------------------------
# _chunk_transcript_turns tests
# ---------------------------------------------------------------------------

def test_chunk_transcript_turns_groups_of_nine():
    """20 turns → 3 groups (9, 9, 2). Only groups >= 400 chars become chunks.
    With _fat_turn content each group easily clears 400 chars."""
    turns = [_fat_turn(i) for i in range(20)]
    chunks = _chunk_transcript_turns(turns, "Q4-2024", "GOOGL")

    # 20 turns / 9 per chunk → ceil(20/9) = 3 groups
    assert len(chunks) == 3, f"Expected 3 chunks, got {len(chunks)}"


def test_chunk_transcript_turns_minimum_char_filter():
    """Groups with fewer than _CHAR_MIN chars are dropped."""
    # Single very short turn — content well under 400 chars total
    short_turns = [{"speaker": "CEO", "title": "", "content": "Hi.", "sentiment": 0}]
    chunks = _chunk_transcript_turns(short_turns, "Q1-2025", "META")
    assert chunks == [], "Expected no chunks when content is too short"


def test_chunk_transcript_turns_all_chunks_meet_min_size():
    """Every returned chunk must be >= _CHAR_MIN chars long."""
    turns = [_fat_turn(i) for i in range(20)]
    chunks = _chunk_transcript_turns(turns, "Q3-2024", "AMZN")
    for chunk in chunks:
        assert len(chunk["content"]) >= _CHAR_MIN, (
            f"Chunk {chunk['chunk_index']} is only {len(chunk['content'])} chars"
        )


def test_chunk_transcript_turns_sequential_chunk_index():
    """chunk_index must be sequential starting at 0."""
    turns = [_fat_turn(i) for i in range(20)]
    chunks = _chunk_transcript_turns(turns, "Q2-2024", "NFLX")
    indices = [c["chunk_index"] for c in chunks]
    assert indices == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# _make_chunk tests
# ---------------------------------------------------------------------------

def test_make_chunk_has_all_required_keys():
    """_make_chunk must return a dict with all 7 required keys."""
    required_keys = {"ticker", "doc_type", "section", "chunk_index", "content", "token_count", "filing_date"}
    chunk = _make_chunk("AAPL", "10-K", "Item 7", 0, "Some content here.", date(2025, 1, 15))
    assert required_keys == set(chunk.keys()), (
        f"Key mismatch. Got: {set(chunk.keys())}"
    )


def test_make_chunk_token_count_approximation():
    """token_count == len(content) // 4 (rough 1 token = 4 chars rule)."""
    content = "A" * 400
    chunk = _make_chunk("TSLA", "10-Q", "overview", 0, content, None)
    assert chunk["token_count"] == 100


def test_make_chunk_filing_date_iso_format():
    """filing_date is stored as ISO string when provided, None otherwise."""
    filing = date(2025, 6, 30)
    chunk_with_date = _make_chunk("MSFT", "10-K", "risk", 0, "text", filing)
    chunk_no_date = _make_chunk("MSFT", "10-K", "risk", 0, "text", None)
    assert chunk_with_date["filing_date"] == "2025-06-30"
    assert chunk_no_date["filing_date"] is None


def test_make_chunk_ticker_passed_through():
    """ticker value must appear verbatim in the returned dict."""
    chunk = _make_chunk("NVDA", "transcript", "Q4-2024", 2, "Content.", None)
    assert chunk["ticker"] == "NVDA"
    assert chunk["doc_type"] == "transcript"
    assert chunk["section"] == "Q4-2024"
    assert chunk["chunk_index"] == 2


# ---------------------------------------------------------------------------
# index_documents integration (mocked Supabase + embedding)
# ---------------------------------------------------------------------------

def _fake_embed_batch(texts):
    """Returns a list of dummy 768-dim zero vectors."""
    return [[0.0] * 768 for _ in texts]


def _build_sec_data() -> dict:
    """SEC data with enough text to produce at least one chunk."""
    body = "Operating results improved significantly driven by cloud services. " * 50
    return {"10-K": f"\n=== Item 7 ===\n{body}"}


def _build_transcript_data() -> dict:
    """Transcript data with enough content to produce at least one chunk."""
    turns = [_fat_turn(i) for i in range(9)]
    return {"transcripts": {"Q4-2024": {"turns": turns}}}


def test_index_documents_returns_chunk_count():
    """index_documents should return the number of chunks upserted."""
    with patch("backend.memory.document_indexer.upsert_chunks") as mock_upsert, \
         patch("backend.memory.document_indexer._embed_batch", side_effect=_fake_embed_batch):

        count = index_documents(
            "AAPL",
            _build_sec_data(),
            _build_transcript_data(),
            filing_date=date(2025, 1, 1),
        )

    assert count > 0, "Expected at least one chunk to be upserted"
    mock_upsert.assert_called_once()


def test_index_documents_calls_upsert_with_embeddings():
    """Every chunk passed to upsert_chunks must have an 'embedding' key."""
    with patch("backend.memory.document_indexer.upsert_chunks") as mock_upsert, \
         patch("backend.memory.document_indexer._embed_batch", side_effect=_fake_embed_batch):

        index_documents("MSFT", _build_sec_data(), _build_transcript_data())

    call_args = mock_upsert.call_args[0][0]  # first positional arg: list of chunks
    for chunk in call_args:
        assert "embedding" in chunk, f"Chunk missing 'embedding' key: {chunk.keys()}"


def test_index_documents_empty_data_returns_zero():
    """No SEC text, no transcripts → 0 chunks, upsert_chunks not called."""
    with patch("backend.memory.document_indexer.upsert_chunks") as mock_upsert, \
         patch("backend.memory.document_indexer._embed_batch", side_effect=_fake_embed_batch):

        count = index_documents("AAPL", {}, {"transcripts": {}})

    assert count == 0
    mock_upsert.assert_not_called()


def test_index_documents_skips_error_placeholder():
    """SEC text starting with '[' (error placeholder) must produce zero chunks."""
    sec_data = {"10-K": "[Error: EDGAR rate limit exceeded]"}
    with patch("backend.memory.document_indexer.upsert_chunks") as mock_upsert, \
         patch("backend.memory.document_indexer._embed_batch", side_effect=_fake_embed_batch):

        count = index_documents("FAIL", sec_data, {})

    assert count == 0
    mock_upsert.assert_not_called()


def test_index_documents_ticker_uppercased():
    """Ticker should be upper-cased regardless of input casing."""
    with patch("backend.memory.document_indexer.upsert_chunks") as mock_upsert, \
         patch("backend.memory.document_indexer._embed_batch", side_effect=_fake_embed_batch):

        index_documents("aapl", _build_sec_data(), {})

    if mock_upsert.called:
        chunks = mock_upsert.call_args[0][0]
        for chunk in chunks:
            assert chunk["ticker"] == "AAPL", f"Expected 'AAPL', got '{chunk['ticker']}'"
