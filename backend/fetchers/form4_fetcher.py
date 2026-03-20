"""
Form 4 Fetcher
Fetches SEC Form 4 (insider transactions) for a given ticker from EDGAR.
Filters to CEO/CFO open-market purchases (transaction code "P") in the last 90 days.
No API key required — EDGAR is free.
"""

import re
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import requests

HEADERS = {"User-Agent": "ResearchAgent ahmednaserismail6@gmail.com"}
EFTS_BASE = "https://efts.sec.gov"
EDGAR_ARCHIVES_BASE = "https://www.sec.gov"

_CEO_RE = re.compile(r"chief\s+executive\s+officer", re.I)
_CFO_RE = re.compile(r"chief\s+financial\s+officer|chief\s+accounting\s+officer", re.I)
_PRESIDENT_RE = re.compile(r"\bpresident\b", re.I)
_VP_RE = re.compile(r"vice\s+president", re.I)


def _is_ceo_title(title: str) -> bool:
    if _CEO_RE.search(title):
        return True
    # "President" counts only when not "Vice President"
    if _PRESIDENT_RE.search(title) and not _VP_RE.search(title):
        return True
    return False


def _is_cfo_title(title: str) -> bool:
    return bool(_CFO_RE.search(title))


def _parse_form4_xml(xml_text: str) -> list[dict]:
    """
    Parse Form 4 XML and return qualifying open-market purchase transactions.
    Each item: {name, title, is_ceo, is_cfo, shares, price, date}
    """
    transactions = []
    try:
        root = ET.fromstring(xml_text)

        # Extract reporting owner name and title
        owner_name = ""
        officer_title = ""
        for owner in root.findall(".//reportingOwner"):
            name_el = owner.find(".//rptOwnerName")
            if name_el is not None:
                owner_name = (name_el.text or "").strip()
            title_el = owner.find(".//officerTitle")
            if title_el is not None:
                officer_title = (title_el.text or "").strip()

        if not officer_title:
            return []

        is_ceo = _is_ceo_title(officer_title)
        is_cfo = _is_cfo_title(officer_title)
        if not (is_ceo or is_cfo):
            return []

        # Find non-derivative transactions with code "P" (open-market purchase)
        for txn in root.findall(".//nonDerivativeTransaction"):
            code_el = txn.find(".//transactionCode")
            if code_el is None or (code_el.text or "").strip() != "P":
                continue

            date_el = txn.find(".//transactionDate/value")
            shares_el = txn.find(".//transactionShares/value")
            price_el = txn.find(".//transactionPricePerShare/value")

            txn_date = (date_el.text or "").strip() if date_el is not None else ""
            try:
                shares = int(float((shares_el.text or "0").strip())) if shares_el is not None else 0
                price = float((price_el.text or "0").strip()) if price_el is not None else 0.0
            except (ValueError, TypeError):
                continue

            transactions.append({
                "name": owner_name,
                "title": officer_title,
                "is_ceo": is_ceo,
                "is_cfo": is_cfo,
                "shares": shares,
                "price": price,
                "date": txn_date,
            })

    except ET.ParseError:
        pass

    return transactions


def _download_form4_xml(entity_id: str, accession_no: str) -> str | None:
    """Download the primary Form 4 XML document from EDGAR archives."""
    accession_clean = accession_no.replace("-", "")
    cik_int = str(entity_id).lstrip("0")
    accession_dashed = f"{accession_clean[:10]}-{accession_clean[10:12]}-{accession_clean[12:]}"

    index_url = (
        f"{EDGAR_ARCHIVES_BASE}/Archives/edgar/data/{cik_int}/"
        f"{accession_clean}/{accession_dashed}-index.json"
    )
    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        index_data = resp.json()
        files = index_data.get("directory", {}).get("item", [])
        xml_files = [
            f["name"] for f in files
            if f.get("name", "").lower().endswith(".xml")
            and "index" not in f.get("name", "").lower()
        ]
        if not xml_files:
            return None

        xml_url = (
            f"{EDGAR_ARCHIVES_BASE}/Archives/edgar/data/{cik_int}/"
            f"{accession_clean}/{xml_files[0]}"
        )
        xml_resp = requests.get(xml_url, headers=HEADERS, timeout=15)
        xml_resp.raise_for_status()
        return xml_resp.text
    except Exception:
        return None


def fetch_form4(ticker: str) -> dict:
    """
    Returns:
    {
        "ticker": str,
        "ceo_purchase": {"name": str, "shares": int, "price": float, "date": str} | None,
        "cfo_purchase": {"name": str, "shares": int, "price": float, "date": str} | None,
        "conviction_rubric_applies": bool,  # True if CEO or CFO open-market purchase found
        "error": None | str,
    }
    Never raises.
    """
    result: dict = {
        "ticker": ticker.upper(),
        "ceo_purchase": None,
        "cfo_purchase": None,
        "conviction_rubric_applies": False,
        "error": None,
    }

    try:
        today = date.today()
        startdt = (today - timedelta(days=90)).isoformat()
        enddt = today.isoformat()

        # EDGAR EFTS full-text search for Form 4 filings mentioning this ticker
        url = (
            f"{EFTS_BASE}/LATEST/search-index"
            f"?q=%22{ticker.upper()}%22&forms=4"
            f"&dateRange=custom&startdt={startdt}&enddt={enddt}"
        )
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            return result

        ceo_purchase = None
        cfo_purchase = None

        for hit in hits[:20]:  # limit to 20 most recent filings
            source = hit.get("_source", {})
            entity_id = source.get("entity_id", "")
            accession_no = source.get("accession_no", "")
            if not entity_id or not accession_no:
                continue

            xml_text = _download_form4_xml(str(entity_id), accession_no)
            if not xml_text:
                continue

            transactions = _parse_form4_xml(xml_text)
            for txn in transactions:
                if txn["is_ceo"] and ceo_purchase is None:
                    ceo_purchase = {
                        "name": txn["name"],
                        "shares": txn["shares"],
                        "price": txn["price"],
                        "date": txn["date"],
                        "value": txn["shares"] * txn["price"],  # Bug 11: dollar value
                    }
                elif txn["is_cfo"] and cfo_purchase is None:
                    cfo_purchase = {
                        "name": txn["name"],
                        "shares": txn["shares"],
                        "price": txn["price"],
                        "date": txn["date"],
                        "value": txn["shares"] * txn["price"],  # Bug 11: dollar value
                    }

            if ceo_purchase and cfo_purchase:
                break  # Found both; stop iterating

            time.sleep(0.2)  # be polite to EDGAR

        result["ceo_purchase"] = ceo_purchase
        result["cfo_purchase"] = cfo_purchase

        # Bug 11: conviction bonus only applies if the purchase is meaningful.
        # Token purchases (e.g. 500 shares at $5 = $2,500) scored identically to
        # $500K buys under the old logic. Threshold: ≥ $25,000 per purchaser.
        _CONVICTION_THRESHOLD = 25_000
        rubric_ceo = ceo_purchase and ceo_purchase.get("value", 0) >= _CONVICTION_THRESHOLD
        rubric_cfo = cfo_purchase and cfo_purchase.get("value", 0) >= _CONVICTION_THRESHOLD
        result["conviction_rubric_applies"] = bool(rubric_ceo or rubric_cfo)

    except Exception as exc:
        result["error"] = str(exc)

    return result
