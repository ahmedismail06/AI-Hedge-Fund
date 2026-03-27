"""
Fetch Apple's most recent 10-K from SEC EDGAR using public APIs.
No API key required.
"""

import requests
import json

HEADERS = {
    "User-Agent": "ResearchAgent ahmednaserismail6@gmail.com",  # SEC requires a valid User-Agent
    
}

APPLE_CIK = "0000320193"


def get_recent_10k():
    """Fetch Apple's filing history and return the most recent 10-K metadata."""
    url = f"https://data.sec.gov/submissions/CIK{APPLE_CIK}.json"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()

    filings = data["filings"]["recent"]
    forms        = filings["form"]
    dates        = filings["filingDate"]
    accessions   = filings["accessionNumber"]
    primary_docs = filings["primaryDocument"]

    # Find the most recent 10-K
    for form, date, accession, doc in zip(forms, dates, accessions, primary_docs):
        if form == "10-K":
            return {
                "form": form,
                "filingDate": date,
                "accessionNumber": accession,
                "primaryDocument": doc,
                "accessionFormatted": accession.replace("-", ""),
            }

    raise ValueError("No 10-K found in recent filings")


def build_filing_url(filing: dict) -> str:
    """Build the direct URL to the primary 10-K document."""
    cik_plain = APPLE_CIK.lstrip("0")  # "320193"
    acc = filing["accessionFormatted"]
    doc = filing["primaryDocument"]
    return f"https://www.sec.gov/Archives/edgar/data/{cik_plain}/{acc}/{doc}"


def get_filing_index(filing: dict) -> dict:
    """Fetch the filing index page to see all documents in the submission."""
    cik_plain = APPLE_CIK.lstrip("0")
    acc = filing["accessionFormatted"]
    index_url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={APPLE_CIK}&type=10-K&dateb=&owner=include&count=5"
    )
    # Alternatively, fetch the JSON index directly:
    json_index_url = (
        f"https://data.sec.gov/submissions/CIK{APPLE_CIK}.json"
    )
    return {"index_url": index_url, "json_index_url": json_index_url}


def fetch_10k_text(filing_url: str, max_chars: int = 5000) -> str:
    """Download the raw 10-K HTML and return a preview."""
    resp = requests.get(filing_url, headers=HEADERS)
    resp.raise_for_status()
    # The file is inline XBRL/HTML — strip tags for a plain-text preview
    import re
    text = re.sub(r"<[^>]+>", " ", resp.text)       # remove HTML tags
    text = re.sub(r"\s+", " ", text).strip()          # collapse whitespace
    return text[:max_chars]


if __name__ == "__main__":
    print("Fetching Apple's most recent 10-K from SEC EDGAR...\n")

    # 1. Find the most recent 10-K
    filing = get_recent_10k()
    print(f"Most recent 10-K:")
    print(f"  Filing Date    : {filing['filingDate']}")
    print(f"  Accession No.  : {filing['accessionNumber']}")
    print(f"  Primary Doc    : {filing['primaryDocument']}")

    # 2. Build the direct document URL
    doc_url = build_filing_url(filing)
    print(f"\nDocument URL:\n  {doc_url}")

    # 3. (Optional) Download a text preview
    print("\nFetching document preview (first 2000 chars of text)...")
    preview = fetch_10k_text(doc_url, max_chars=2000)
    print("\n--- PREVIEW ---")
    print(preview)
    print("--- END PREVIEW ---")