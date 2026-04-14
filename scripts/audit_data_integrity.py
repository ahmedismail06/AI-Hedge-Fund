#!/usr/bin/env python3
"""
Data Integrity Audit Script — AI Hedge Fund

Read-only. Parses:
  1. backend/db/schema.sql  → {table: {col: sql_type}}
  2. Pydantic model files   → {ClassName: {field: type_str}}
  3. Agent/broker/risk .py  → which columns are written (any key mention in a
     .table("X") context — handles multi-line dicts)

Reports:
  MISSING_IN_MODEL   — schema column absent from its Pydantic model
  NEVER_WRITTEN      — critical column never mentioned in any write context
  CONFIDENCE_FLAGS   — data quality flags set by fetchers but ignored downstream

Usage:
  python scripts/audit_data_integrity.py [--json] [--ci]

Exit 0: no critical issues.
Exit 1: one or more MISSING_IN_MODEL, NEVER_WRITTEN, or CONFIDENCE_FLAGS issues.
"""

import ast
import json
import os
import re
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "backend" / "db" / "schema.sql"
MODELS_DIR  = ROOT / "backend" / "models"
AGENT_DIRS  = [
    ROOT / "backend" / "agents",
    ROOT / "backend" / "broker",
    ROOT / "backend" / "risk",
    ROOT / "backend" / "screener",
    ROOT / "backend" / "portfolio",
    ROOT / "backend" / "macro",
]

# Table → (model_file, Pydantic class name) — tables that have a direct model
TABLE_TO_MODEL = {
    "positions":       ("position.py",       "Position"),
    "watchlist":       ("watchlist.py",      "WatchlistEntry"),
    "memos":           ("memo.py",           "InvestmentMemo"),
    "risk_alerts":     ("risk.py",           "RiskAlert"),
    "macro_briefings": ("macro_briefing.py", "MacroBriefing"),
}

# Columns that are intentionally schema-only (auto-managed by DB, audit-trail,
# or belong to the WRITE model SizingRecommendation rather than the runtime Position).
SCHEMA_ONLY_COLS = {
    "positions": {
        # DB-managed
        "id", "created_at",
        # Write-model (SizingRecommendation) — intentionally absent from runtime Position
        "status", "dollar_size", "share_count", "size_label",
        "stop_loss_price", "target_price", "risk_reward_ratio",
        "sizing_rationale", "correlation_flag", "correlation_note",
        "sector", "regime_at_sizing", "portfolio_state_after",
        "memo_id",
        # Lifecycle timestamps (not needed in runtime snapshot)
        "closed_at", "opened_at", "exit_action", "exit_trim_pct",
    },
    "watchlist": {
        "id", "created_at", "run_date",
        # Stored as separate columns but modelled inside factor_scores nested model
        "quality_score", "value_score", "momentum_score",
        # raw_factors is a JSONB blob — intentionally untyped at model level
        "raw_factors",
    },
    "memos": {
        "id", "created_at", "ticker", "date", "verdict", "conviction_score",
        # Stored as JSONB envelope — the Pydantic model IS the unpacked contents
        "memo_json", "raw_docs",
        # Workflow state, not part of the memo content model
        "status", "deferred_until",
    },
    "risk_alerts": {"id", "created_at"},
    "macro_briefings": {
        "id", "created_at",
        # JSONB envelope for the full MacroBriefing object
        "briefing_json",
        # previous_regime and regime_changed handled inside the model
        "previous_regime", "regime_changed",
    },
}

# Critical write checks: columns that MUST be written by agent code
CRITICAL_WRITE_CHECKS = {
    "positions": {
        "pnl",
        "pnl_pct",
        "current_price",
        "stop_tier1",
        "stop_tier2",
        "stop_tier3",
        "next_earnings_date",
    },
    "watchlist": {
        "material_event",
        "material_event_reason",
        "priority",
    },
}

# Known confidence flags: (fetcher_file_pattern, flag_field, consumer_rel_path, issue_desc)
CONFIDENCE_FLAG_SPECS = [
    (
        "fmp_fetcher.py",
        "ocf_annualized",
        "screener/factors/value.py",
        "P/FCF computed without checking ocf_annualized — single-quarter OCF may "
        "overstate TTM by up to 4× for seasonal businesses",
    ),
    (
        "fmp_fetcher.py",
        "market_cap_source",
        "screener/factors/value.py",
        "EV uses market_cap without verifying market_cap_source — "
        "'polygon_reference' path uses stale reference data",
    ),
]


# ── 1. Parse schema.sql ────────────────────────────────────────────────────────

def _is_valid_col_name(name: str) -> bool:
    """A valid column name is alphanumeric + underscores, starts with a letter."""
    return bool(re.match(r"^[a-z_][a-z0-9_]*$", name))


def parse_schema(schema_path: Path) -> dict[str, dict[str, str]]:
    """Returns {table_name: {col_name: sql_type}}."""
    text = schema_path.read_text()
    # Strip SQL comments
    text = re.sub(r"--[^\n]*", "", text)

    tables: dict[str, dict[str, str]] = {}

    # ── CREATE TABLE blocks ────────────────────────────────────────────────────
    create_iter = re.finditer(
        r"create\s+table\s+(?:if\s+not\s+exists\s+)?(\w+)\s*\(",
        text, re.IGNORECASE,
    )
    for m in create_iter:
        table_name = m.group(1).lower()
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        body = text[start : i - 1]

        cols: dict[str, str] = {}
        for line in body.split(","):
            line = line.strip()
            if not line:
                continue
            upper = line.upper()
            # Skip table constraints
            if any(upper.lstrip().startswith(k) for k in (
                "PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT",
                "INDEX", "KEY",
            )):
                continue
            parts = line.split()
            if len(parts) >= 2:
                col = parts[0].strip('"').lower()
                sql_type = parts[1].upper().rstrip(",").rstrip("(")
                # Only keep valid column names (filter CHECK constraint artefacts)
                if _is_valid_col_name(col):
                    cols[col] = sql_type
        tables[table_name] = cols

    # ── ALTER TABLE ... ADD COLUMN ─────────────────────────────────────────────
    alter_pattern = re.compile(
        r"alter\s+table\s+(\w+)\s+add\s+column\s+(?:if\s+not\s+exists\s+)?(\w+)\s+([\w\(\)]+)",
        re.IGNORECASE,
    )
    for m in alter_pattern.finditer(text):
        tbl = m.group(1).lower()
        col = m.group(2).lower()
        typ = m.group(3).upper().rstrip("(")
        if _is_valid_col_name(col):
            if tbl not in tables:
                tables[tbl] = {}
            tables[tbl][col] = typ

    return tables


# ── 2. Parse Pydantic models ───────────────────────────────────────────────────

def _annotation_to_str(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Subscript):
        return f"{_annotation_to_str(node.value)}[{_annotation_to_str(node.slice)}]"
    if isinstance(node, ast.Attribute):
        return f"{_annotation_to_str(node.value)}.{node.attr}"
    if isinstance(node, ast.Tuple):
        return ", ".join(_annotation_to_str(e) for e in node.elts)
    if isinstance(node, ast.BinOp):
        return f"{_annotation_to_str(node.left)} | {_annotation_to_str(node.right)}"
    return "<complex>"


def parse_models(models_dir: Path) -> dict[str, dict[str, str]]:
    """Returns {ClassName: {field_name: type_str}} for all Pydantic BaseModel subclasses."""
    result: dict[str, dict[str, str]] = {}
    for py_file in models_dir.glob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute):
                    base_names.append(base.attr)
            if "BaseModel" not in base_names:
                continue
            fields: dict[str, str] = {}
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields[item.target.id] = (
                        _annotation_to_str(item.annotation) if item.annotation else "Any"
                    )
            result[node.name] = fields
    return result


# ── 3. Detect written columns (multi-line-safe) ────────────────────────────────

def scan_written_columns(agent_dirs: list[Path]) -> dict[str, set[str]]:
    """
    Returns {table_name: {col_name, ...}} for all columns written to each table.

    Two-pass strategy per file:
      Pass 1 — direct: find .table("X").(insert|update|upsert)({...}) blocks
               and extract quoted keys from the inline dict literal.
      Pass 2 — indirect: if a file references .table("X") AND also contains
               any quoted key that matches a known column name anywhere in the
               file (handles helper functions like _upsert_position({...}) where
               the dict is built at the call site, not at the .table() line).

    This avoids false NEVER_WRITTEN reports for helper-function write patterns.
    """
    written: dict[str, set[str]] = {}

    def _ensure(t: str):
        if t not in written:
            written[t] = set()

    for d in agent_dirs:
        for py_file in d.rglob("*.py"):
            text = py_file.read_text(errors="replace")

            # Collect all tables this file touches with any write operation
            tables_written_here: set[str] = set()
            for m in re.finditer(r'\.table\(["\'](\w+)["\']\)', text):
                tbl = m.group(1)
                # Check 500 chars ahead for a write verb
                snippet_fwd = text[m.start() : m.start() + 500]
                if re.search(r"\.(insert|update|upsert)\s*\(", snippet_fwd, re.IGNORECASE):
                    tables_written_here.add(tbl)

            if not tables_written_here:
                continue

            # Pass 1: extract quoted dict keys from all inline dict literals
            # near write operations (covers most cases)
            for m in re.finditer(r'\.table\(["\'](\w+)["\']\)', text):
                tbl = m.group(1)
                if tbl not in tables_written_here:
                    continue
                snippet = text[m.start() : m.start() + 4000]
                if not re.search(r"\.(insert|update|upsert)\s*\(", snippet, re.IGNORECASE):
                    continue
                _ensure(tbl)
                for key_m in re.finditer(r'["\']([a-z_][a-z0-9_]*)["\'\s]*:', snippet):
                    written[tbl].add(key_m.group(1).lower())

            # Pass 2: for each table written in this file, collect ALL quoted keys
            # anywhere in the file (catches helper-function dict-at-call-site patterns)
            for tbl in tables_written_here:
                _ensure(tbl)
                for key_m in re.finditer(r'["\']([a-z_][a-z0-9_]*)["\'\s]*:', text):
                    written[tbl].add(key_m.group(1).lower())

    return written


# ── 4. Check confidence flags ──────────────────────────────────────────────────

def check_confidence_flags(root: Path) -> list[dict]:
    findings = []
    for fetcher_pat, flag, consumer_rel, issue in CONFIDENCE_FLAG_SPECS:
        consumer_path = root / "backend" / consumer_rel
        if not consumer_path.exists():
            continue
        if flag not in consumer_path.read_text(errors="replace"):
            findings.append({"fetcher": fetcher_pat, "flag": flag,
                             "consumer": consumer_rel, "issue": issue})
    return findings


# ── 5. Main ────────────────────────────────────────────────────────────────────

def main():
    use_json = "--json" in sys.argv
    ci_mode  = "--ci"   in sys.argv

    schema      = parse_schema(SCHEMA_FILE)
    models      = parse_models(MODELS_DIR)
    written     = scan_written_columns(AGENT_DIRS)
    conf_flags  = check_confidence_flags(ROOT)

    report = {
        "MISSING_IN_MODEL": [],
        "NEVER_WRITTEN":    [],
        "CONFIDENCE_FLAGS": [],
    }

    # ── A. MISSING_IN_MODEL ────────────────────────────────────────────────────
    for table, (model_file, model_class) in TABLE_TO_MODEL.items():
        schema_cols  = schema.get(table, {})
        model_fields = models.get(model_class, {})
        skip         = SCHEMA_ONLY_COLS.get(table, set())
        for col, sql_type in schema_cols.items():
            if col in skip:
                continue
            if col not in model_fields:
                report["MISSING_IN_MODEL"].append({
                    "table":       table,
                    "column":      col,
                    "sql_type":    sql_type,
                    "model_class": model_class,
                    "model_file":  f"backend/models/{model_file}",
                })

    # ── B. NEVER_WRITTEN ──────────────────────────────────────────────────────
    for table, required_cols in CRITICAL_WRITE_CHECKS.items():
        tbl_written = written.get(table, set())
        for col in sorted(required_cols):
            if col not in tbl_written:
                report["NEVER_WRITTEN"].append({
                    "table":  table,
                    "column": col,
                    "issue":  f"defined in schema but no agent file writes it",
                })

    # ── C. CONFIDENCE FLAGS ────────────────────────────────────────────────────
    report["CONFIDENCE_FLAGS"] = conf_flags

    # ── Output ─────────────────────────────────────────────────────────────────
    has_critical = bool(
        report["MISSING_IN_MODEL"]
        or report["NEVER_WRITTEN"]
        or report["CONFIDENCE_FLAGS"]
    )

    if use_json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report, ci_mode)

    sys.exit(1 if has_critical else 0)


def _print_report(report: dict, ci_mode: bool):
    width = 72

    def section(title, items, formatter):
        print(f"\n{'=' * width}")
        print(f"  {title}  ({len(items)} issue{'s' if len(items) != 1 else ''})")
        print(f"{'=' * width}")
        if not items:
            print("  OK — no issues found.")
        else:
            for item in items:
                print(formatter(item))

    section(
        "MISSING_IN_MODEL — schema column not in Pydantic model",
        report["MISSING_IN_MODEL"],
        lambda x: (
            f"  [{x['table']}] {x['column']} ({x['sql_type']})"
            f"\n    → not in {x['model_class']} ({x['model_file']})"
        ),
    )

    section(
        "NEVER_WRITTEN — critical column never written by any agent file",
        report["NEVER_WRITTEN"],
        lambda x: (
            f"  [{x['table']}] {x['column']}"
            f"\n    → {x['issue']}"
        ),
    )

    section(
        "CONFIDENCE_FLAGS — fetcher quality flags ignored downstream",
        report["CONFIDENCE_FLAGS"],
        lambda x: (
            f"  [{x['fetcher']}] flag: {x['flag']}"
            f"\n    → consumer: {x['consumer']}"
            f"\n    → {x['issue']}"
        ),
    )

    print(f"\n{'=' * width}")
    total = (
        len(report["MISSING_IN_MODEL"])
        + len(report["NEVER_WRITTEN"])
        + len(report["CONFIDENCE_FLAGS"])
    )
    status = "FAIL" if total else "PASS"
    print(f"  AUDIT RESULT: {status} — {total} critical issue(s) found")
    print(f"{'=' * width}\n")


if __name__ == "__main__":
    main()
