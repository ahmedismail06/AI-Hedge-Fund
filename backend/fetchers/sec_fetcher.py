"""
SEC Fetcher
Fetches 10-K and 10-Q filings from SEC EDGAR for a given ticker.
Extracts Items 1, 1A, 7, 8 (CAG approach) and caps each at ~12,000 chars.
"""

import re
import time
import requests
from bs4 import BeautifulSoup

# Module-level CIK cache — populated on first call, reused thereafter
_cik_cache: dict[str, str] = {}

HEADERS = {"User-Agent": "ResearchAgent ahmednaserismail6@gmail.com"}
EDGAR_BASE = "https://data.sec.gov"
EDGAR_ARCHIVES_BASE = "https://www.sec.gov"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

SECTION_CAP = 12_000
FALLBACK_CAP = 15_000

# Regex patterns for 10-K section headers (case-insensitive, flexible whitespace)
SECTION_PATTERNS = {
    "Item 1":  re.compile(r"item\s+1[\.\s]*(?:business|description of business)", re.I),
    "Item 1A": re.compile(r"item\s+1a[\.\s]*(?:risk\s+factors?)", re.I),
    "Item 7":  re.compile(r"item\s+7[\.\s]*(?:management[\u2019's\s]+discussion|md&a)", re.I),
    "Item 8":  re.compile(r"item\s+8[\.\s]*(?:financial\s+statements?)", re.I),
}

# Next-section boundary (anything that looks like "Item N" or "Item NA")
NEXT_SECTION_PATTERN = re.compile(r"(?:^|\n)\s*item\s+\d+[a-z]?[\.\s]", re.I)


def _load_cik_cache() -> None:
    resp = requests.get(TICKERS_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    for entry in data.values():
        ticker = entry["ticker"].upper()
        cik = str(entry["cik_str"]).zfill(10)
        _cik_cache[ticker] = cik


def _resolve_cik(ticker: str) -> str:
    if not _cik_cache:
        _load_cik_cache()
    cik = _cik_cache.get(ticker.upper())
    if not cik:
        raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR company list")
    return cik


def _get_filings_metadata(cik: str) -> dict:
    url = f"{EDGAR_BASE}/submissions/CIK{cik}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _find_latest_filing(filings_meta: dict, form_type: str) -> dict | None:
    """Return accession number and primary document for the most recent form_type filing."""
    recent = filings_meta.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    filing_dates = recent.get("filingDate", [])

    for i, form in enumerate(forms):
        if form == form_type:
            return {
                "accession": accessions[i].replace("-", ""),
                "primary_doc": primary_docs[i],
                "date": filing_dates[i],
                "cik": filings_meta.get("cik", ""),
            }
    return None


def _download_filing_text(cik: str, accession: str, primary_doc: str) -> str:
    # accession arrives with dashes stripped (e.g. "000032019325000079")
    # EDGAR index JSON requires dashes restored (e.g. "0000320193-25-000079")
    accession_dashed = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"

    cik_int = cik.lstrip("0")
    # Try primary doc directly first
    url = f"{EDGAR_ARCHIVES_BASE}/Archives/edgar/data/{cik_int}/{accession}/{primary_doc}"
    resp = requests.get(url, headers=HEADERS, timeout=30)

    # On 404, fetch the filing index and find the largest .htm
    if resp.status_code == 404:
        index_url = f"{EDGAR_ARCHIVES_BASE}/Archives/edgar/data/{cik_int}/{accession}/{accession_dashed}-index.json"
        index_resp = requests.get(index_url, headers=HEADERS, timeout=15)
        index_resp.raise_for_status()
        index_data = index_resp.json()

        files = index_data.get("directory", {}).get("item", [])
        htm_files = [
            f for f in files
            if f.get("name", "").lower().endswith((".htm", ".html"))
            and "index" not in f.get("name", "").lower()
        ]
        if not htm_files:
            raise ValueError(f"No .htm filing found in EDGAR index for accession {accession}")

        htm_files.sort(key=lambda f: int(f.get("size", 0)), reverse=True)
        best_file = htm_files[0]["name"]
        url = f"{EDGAR_ARCHIVES_BASE}/Archives/edgar/data/{cik_int}/{accession}/{best_file}"
        resp = requests.get(url, headers=HEADERS, timeout=30)

    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")
    for tag in soup(["script", "style", "table"]):
        tag.decompose()
    return soup.get_text(separator="\n")


def _extract_sections(text: str) -> dict[str, str]:
    """Extract Items 1, 1A, 7, 8 by matching section headers."""
    results: dict[str, str] = {}
    for section_name, pattern in SECTION_PATTERNS.items():
        match = pattern.search(text)
        if not match:
            continue
        start = match.start()
        # Find where next section begins after this one
        next_match = NEXT_SECTION_PATTERN.search(text, match.end() + 200)
        end = next_match.start() if next_match else start + SECTION_CAP * 2
        chunk = text[start:end].strip()
        results[section_name] = chunk[:SECTION_CAP]
    return results


def _fetch_filing(cik: str, form_type: str) -> str:
    meta = _get_filings_metadata(cik)
    filing = _find_latest_filing(meta, form_type)
    if filing is None:
        return f"[No {form_type} filing found in EDGAR]"

    raw_text = _download_filing_text(cik, filing["accession"], filing["primary_doc"])
    sections = _extract_sections(raw_text)

    if sections:
        parts = []
        for name, content in sections.items():
            parts.append(f"=== {name} ===\n{content}")
        return "\n\n".join(parts)
    else:
        # Fallback: first N chars of raw text
        return raw_text[:FALLBACK_CAP]


def fetch_sec_filings(ticker: str) -> dict:
    """
    Public entry point. Returns:
    {
        "10-K": "text...",
        "10-Q": "text...",
        "error": None | "error message"
    }
    Never raises — errors are captured in the "error" field.
    """
    result = {"10-K": "[Not available]", "10-Q": "[Not available]", "error": None}
    try:
        cik = _resolve_cik(ticker)
        # Small delay between EDGAR requests to be polite
        result["10-K"] = _fetch_filing(cik, "10-K")
        time.sleep(0.5)
        result["10-Q"] = _fetch_filing(cik, "10-Q")
    except Exception as exc:
        result["error"] = str(exc)
    return result
