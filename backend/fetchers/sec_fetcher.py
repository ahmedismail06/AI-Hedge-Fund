"""
SEC Fetcher
Fetches 10-K and 10-Q filings from SEC EDGAR for a given ticker.
Extracts Items 1, 1A, 7 with reduced caps; drops Item 8 in favour of
programmatically pre-extracted financial metrics.
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

FALLBACK_CAP = 15_000

# Per-section caps — Item 8 is 0 (dropped; replaced by pre-extracted metrics)
SECTION_CAPS = {
    "Item 1":  8_000,   # Business narrative; no raw numbers
    "Item 1A": 12_000,  # Risk factors: 6K was truncating mid-section; material disclosures
                        # can appear in the back half (going-concern, regulatory, litigation)
    "Item 7":  8_000,   # MD&A management commentary still needed
    "Item 8":  0,       # Financial tables replaced by extract_financial_metrics()
}

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


def _download_filing_content(cik: str, accession: str, primary_doc: str) -> bytes:
    """Download raw HTML bytes for a filing. Handles 404 fallback to filing index."""
    accession_dashed = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
    cik_int = cik.lstrip("0")

    url = f"{EDGAR_ARCHIVES_BASE}/Archives/edgar/data/{cik_int}/{accession}/{primary_doc}"
    resp = requests.get(url, headers=HEADERS, timeout=30)

    if resp.status_code == 404:
        index_url = (
            f"{EDGAR_ARCHIVES_BASE}/Archives/edgar/data/{cik_int}/"
            f"{accession}/{accession_dashed}-index.json"
        )
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
        url = (
            f"{EDGAR_ARCHIVES_BASE}/Archives/edgar/data/{cik_int}/"
            f"{accession}/{htm_files[0]['name']}"
        )
        resp = requests.get(url, headers=HEADERS, timeout=30)

    resp.raise_for_status()
    return resp.content


def _html_to_narrative_text(content: bytes) -> str:
    """Parse HTML → plain text with tables removed (for LLM narrative context)."""
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "table"]):
        tag.decompose()
    return soup.get_text(separator="\n")


def _html_to_financial_text(content: bytes) -> str:
    """Parse HTML → space-separated text keeping table cell values (for metric extraction)."""
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator=" ")


def extract_financial_metrics(text: str) -> dict:
    """
    Programmatically extract key financial metrics from Item 7/8 text using regex.
    Returns a dict of extracted values; None where not found.
    Called before capping — runs on raw (uncapped) filing text.
    """
    metrics: dict = {
        "revenue_recent": None,
        "revenue_prior": None,
        "gross_margin": None,
        "operating_income": None,
        "net_income": None,
        "cash": None,
        "long_term_debt": None,
        "accounts_payable": None,
        "atm_or_shelf": False,
        "debt_maturities": None,
        "reporting_unit": None,  # "thousands" | "millions" | None (not detected)
    }

    # Detect reporting unit from financial statement header, e.g.:
    #   "(in thousands, except per share data)"
    #   "(in millions)"
    #   "(dollars in thousands)"
    _unit_match = re.search(
        r"\(\s*(?:in|amounts?\s+in|dollars?\s+in)\s+(thousands?|millions?)\b",
        text, re.I
    )
    if _unit_match:
        raw = _unit_match.group(1).lower()
        metrics["reporting_unit"] = "thousands" if raw.startswith("thousand") else "millions"

    # Dollar amount pattern: handles "$ 1,234" (space after $), "$1,234", "1,234" near a keyword
    # Also handles "\xa0" (non-breaking space) as whitespace in tables
    _DOLLAR = r"\$\s*(\d[\d,\.]*)"            # captures digits after optional $+space
    _DOLLAR_OR_NUM = r"(?:\$\s*)?(\d[\d,\.]*)"  # dollar sign optional

    # Revenue — capture both current-period and prior-period values from the same table row
    # Format: "Total revenue $ 2,347,637 $ 871,123" (two adjacent dollar amounts)
    rev = re.search(
        r"(?:total\s+(?:net\s+)?revenue|net\s+revenue)\s+" + _DOLLAR
        + r"[^$\n]{0,60}" + _DOLLAR,
        text, re.I
    )
    if rev:
        metrics["revenue_recent"] = f"${rev.group(1)}"
        metrics["revenue_prior"] = f"${rev.group(2)}"
    else:
        # Fallback: single period
        rev1 = re.search(
            r"(?:total\s+(?:net\s+)?revenue|net\s+revenue)\s+" + _DOLLAR,
            text, re.I
        )
        if rev1:
            metrics["revenue_recent"] = f"${rev1.group(1)}"

    # Gross margin % — look for a % sign after "gross margin"
    gm = re.search(r"gross\s+(?:profit\s+)?margin[^%]{0,100}?(\d+(?:\.\d+)?)\s*%", text, re.I)
    if gm:
        metrics["gross_margin"] = f"{gm.group(1)}%"

    # Operating income / loss
    op = re.search(
        r"(?:income|loss)\s+from\s+operations[\s\S]{0,60}" + _DOLLAR,
        text, re.I
    )
    if op:
        metrics["operating_income"] = f"${op.group(1)}"

    # Net income / loss — anchor tightly to avoid capturing unrelated values
    net = re.search(
        r"\bnet\s+(?:income|loss)\b[\s\S]{0,60}" + _DOLLAR,
        text, re.I
    )
    if net:
        metrics["net_income"] = f"${net.group(1)}"

    # Cash and cash equivalents — only allow horizontal whitespace between label and value.
    # Using [\s\S] here caused the regex to cross row boundaries and match the
    # accumulated deficit or other nearby balance sheet items (Bug 1).
    cash = re.search(
        r"cash\s+and\s+cash\s+equivalents[ \t\xa0]{0,50}" + _DOLLAR,
        text, re.I
    )
    if cash:
        val = cash.group(1)
        if len(val) >= 3:  # at least "X,X" to filter single-char junk
            metrics["cash"] = f"${val}"

    # Long-term debt (dollar sign optional — many SEC table rows omit it)
    ltd = re.search(
        r"long[\-\s]term\s+debt(?:\s*,\s*(?:net|current\s+portion)?)?[\s\S]{0,80}" + _DOLLAR_OR_NUM,
        text, re.I
    )
    if ltd:
        val = ltd.group(1)
        if len(val) >= 5:  # at least "1,234" to filter junk
            metrics["long_term_debt"] = f"${val}"

    # Accounts payable (dollar sign optional).
    # Tightened from [\s\S]{0,80} to horizontal-whitespace-only to prevent
    # jumping across table rows and producing nonsense values like "$0,665" (Bug 2).
    # Also reject values starting with "0," which indicate a partial-number match.
    ap = re.search(
        r"accounts\s+payable(?:\s+and\s+accrued[^\n]{0,40})?[ \t\xa0]{0,50}" + _DOLLAR_OR_NUM,
        text, re.I
    )
    if ap:
        val = ap.group(1)
        if len(val) >= 5 and not val.startswith("0,"):
            metrics["accounts_payable"] = f"${val}"

    # ATM / shelf registration risk
    if re.search(r"at[\-\s]the[\-\s]market|equity\s+offering|shelf\s+registration", text, re.I):
        metrics["atm_or_shelf"] = True

    # Debt maturities
    maturities = re.findall(r"(?:due\s+in|matures?\s+in)\s+(20[2-9]\d)", text, re.I)
    if maturities:
        metrics["debt_maturities"] = ", ".join(sorted(set(maturities)))

    return metrics


def _extract_sections(text: str) -> tuple[dict[str, str], str]:
    """
    Extract Items 1, 1A, 7, 8 by matching section headers.
    Returns:
      - sections dict: capped text for LLM (Item 8 excluded per SECTION_CAPS)
      - financial_text: raw uncapped Item 7 + Item 8 text for metric extraction
    """
    results: dict[str, str] = {}
    financial_text_parts: list[str] = []

    for section_name, pattern in SECTION_PATTERNS.items():
        # Bug 6: SEC filings have a Table of Contents that lists "Item 1 — Business"
        # before the actual section body. In the TOC, the next section header
        # appears within ~50-100 chars; in the real body it's thousands of chars away.
        # Skip any match where another section header appears within 600 chars of it.
        match = pattern.search(text)
        while match:
            next_nearby = NEXT_SECTION_PATTERN.search(text, match.end())
            if next_nearby is None or next_nearby.start() - match.end() > 600:
                break  # no nearby header → real section body
            match = pattern.search(text, match.end() + 1)

        if not match:
            continue
        start = match.start()
        # Find where next section begins after this one
        next_match = NEXT_SECTION_PATTERN.search(text, match.end() + 200)
        # Use a generous raw cap for metric extraction
        end = next_match.start() if next_match else start + 40_000
        raw_chunk = text[start:end].strip()

        # Collect Item 7 and 8 raw text for financial metric extraction
        if section_name in ("Item 7", "Item 8"):
            financial_text_parts.append(raw_chunk)

        cap = SECTION_CAPS.get(section_name, 8_000)
        if cap > 0:
            results[section_name] = raw_chunk[:cap]
        # If cap == 0, section is intentionally dropped from LLM context

    return results, "\n\n".join(financial_text_parts)


def _fetch_filing(cik: str, form_type: str) -> tuple[str, dict]:
    """Returns (sections_text_for_llm, financial_metrics_dict)."""
    meta = _get_filings_metadata(cik)
    filing = _find_latest_filing(meta, form_type)
    if filing is None:
        return f"[No {form_type} filing found in EDGAR]", {}

    # Download once, parse twice: narrative (no tables) for LLM; financial (with tables) for metrics
    raw_content = _download_filing_content(cik, filing["accession"], filing["primary_doc"])
    narrative_text = _html_to_narrative_text(raw_content)
    financial_text_full = _html_to_financial_text(raw_content)

    sections, _ = _extract_sections(narrative_text)

    # Bug 14: 10-Q Item 1A often just says "no material changes to risk factors
    # disclosed in our Annual Report." Detect this boilerplate (short section +
    # "no material changes" language) and replace it so the LLM doesn't treat it
    # as a real risk update. The 10-K Item 1A remains authoritative.
    if form_type == "10-Q" and "Item 1A" in sections:
        item1a = sections["Item 1A"]
        if len(item1a) < 2_000 and re.search(
            r"no\s+material\s+changes?\s+(?:to|from|in)", item1a, re.I
        ):
            sections["Item 1A"] = (
                "[10-Q Item 1A: No material changes vs. annual filing. Refer to 10-K.]"
            )

    # Run metric extraction on the table-preserving version.
    # Financial statements appear anywhere in the filing; use the full text.
    metrics = extract_financial_metrics(financial_text_full)

    if sections:
        parts = []
        for name, content in sections.items():
            parts.append(f"=== {name} ===\n{content}")
        text = "\n\n".join(parts)
    else:
        # Fallback: first N chars of narrative text (Bug 5: raw_text was undefined)
        text = narrative_text[:FALLBACK_CAP]

    return text, metrics


def fetch_sec_filings(ticker: str) -> dict:
    """
    Public entry point. Returns:
    {
        "10-K": "text...",
        "10-Q": "text...",
        "metrics_10k": {...},
        "metrics_10q": {...},
        "error": None | "error message"
    }
    Never raises — errors are captured in the "error" field.
    """
    result = {
        "10-K": "[Not available]",
        "10-Q": "[Not available]",
        "metrics_10k": {},
        "metrics_10q": {},
        "error": None,
    }
    try:
        cik = _resolve_cik(ticker)
        # Small delay between EDGAR requests to be polite
        result["10-K"], result["metrics_10k"] = _fetch_filing(cik, "10-K")
        time.sleep(0.5)
        result["10-Q"], result["metrics_10q"] = _fetch_filing(cik, "10-Q")
    except Exception as exc:
        result["error"] = str(exc)
    return result
