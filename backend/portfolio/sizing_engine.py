"""
sizing_engine.py — Pure-quant fractional Kelly position sizing.

Converts a conviction score, portfolio value, and entry price into a concrete
dollar size and share count using 25% fractional Kelly.  No LLM call is made
here; this module is deterministic and side-effect-free.

Conviction tiers and Kelly parameters
--------------------------------------
Conviction 9.0–10.0 → large   (win_rate 0.65, b 2.0, max 8%)
Conviction 7.0–8.99 → medium  (win_rate 0.58, b 2.0, max 5%)
Conviction 5.0–6.99 → small   (win_rate 0.52, b 2.0, max 2%)
Conviction  < 5.0   → SKIP    (raise ValueError — caller must not size)

Kelly formula: f* = (b·p − q) / b  where p=win_rate, q=1−p, b=win/loss ratio.
Applied at 25% fractional Kelly to reduce volatility.

Hard position cap: pct_of_portfolio ≤ 0.15 (15%) regardless of Kelly output.

Stop-loss tiers (Tier 1)
------------------------
Risk-On / Transitional : entry × (1 − 0.08)
Risk-Off / Stagflation : entry × (1 − 0.05)  (tighter)
"""


# ── Conviction parameter table ────────────────────────────────────────────────

_VALID_REGIMES = {"Risk-On", "Risk-Off", "Transitional", "Stagflation"}

_TIERS: list[dict] = [
    {
        "min_conviction": 9.0,
        "max_conviction": 10.0,
        "win_rate": 0.65,
        "avg_win_loss_ratio": 2.0,
        "size_label": "large",
        "max_pct": 0.08,
    },
    {
        "min_conviction": 7.0,
        "max_conviction": 8.99,
        "win_rate": 0.58,
        "avg_win_loss_ratio": 2.0,
        "size_label": "medium",
        "max_pct": 0.05,
    },
    {
        "min_conviction": 5.0,
        "max_conviction": 6.99,
        "win_rate": 0.52,
        "avg_win_loss_ratio": 2.0,
        "size_label": "small",
        "max_pct": 0.02,
    },
]

_HARD_POSITION_CAP = 0.15  # 15% of portfolio — absolute ceiling
_KELLY_FRACTION = 0.25     # 25% fractional Kelly


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_tier(conviction_score: float) -> dict:
    """
    Return the parameter dict for the given conviction score.

    Raises ValueError if conviction_score < 5.0 (caller must skip this position)
    or > 10.0 (invalid input).
    """
    if conviction_score > 10.0:
        raise ValueError(
            f"conviction_score {conviction_score:.2f} exceeds maximum of 10.0"
        )
    for tier in _TIERS:
        if tier["min_conviction"] <= conviction_score <= tier["max_conviction"]:
            return tier
    raise ValueError(
        f"conviction_score {conviction_score:.2f} is below 5.0 — position should be skipped."
    )


def _compute_stop_loss(entry_price: float, regime: str) -> float:
    """
    Return the Tier 1 stop-loss price based on the macro regime.

    Risk-Off and Stagflation use a tighter 5% stop; all other regimes use 8%.
    Raises ValueError for unrecognised regime strings.
    """
    if regime not in _VALID_REGIMES:
        raise ValueError(
            f"Unknown regime '{regime}'. Must be one of {_VALID_REGIMES}"
        )
    tight_regimes = {"Risk-Off", "Stagflation"}
    stop_pct = 0.05 if regime in tight_regimes else 0.08
    return entry_price * (1.0 - stop_pct)


# ── Public entry point ────────────────────────────────────────────────────────

def calculate_size(
    conviction_score: float,
    portfolio_value: float,
    entry_price: float,
    regime: str = "Risk-On",
) -> dict:
    """
    Convert a conviction score into a concrete position size using 25% fractional Kelly.

    Kelly formula: f* = (b·p − q) / b, where p = win_rate, q = 1−p, b = avg_win/avg_loss.
    Applied at 25% fractional to reduce portfolio volatility.

    Parameters
    ----------
    conviction_score : float
        LLM-assigned conviction on the [0, 10] scale.
    portfolio_value : float
        Current total portfolio value in USD.
    entry_price : float
        Intended entry price per share in USD.
    regime : str
        Current macro regime: "Risk-On", "Risk-Off", "Transitional", or "Stagflation".

    Returns
    -------
    dict with keys:
        kelly_fraction    – raw full-Kelly fraction (float)
        dollar_size       – position size in USD (float)
        share_count       – number of whole shares to buy (int)
        size_label        – "large" | "medium" | "small"
        pct_of_portfolio  – final allocation as a fraction of portfolio (float)
        stop_loss_price   – Tier 1 stop-loss price per share (float)
        sizing_rationale  – human-readable explanation string

    Raises
    ------
    ValueError
        If conviction_score < 5.0 or > 10.0, portfolio_value/entry_price ≤ 0,
        computed share_count is 0, or regime string is unrecognised.
    """
    if portfolio_value <= 0:
        raise ValueError(f"portfolio_value must be positive, got {portfolio_value}")
    if entry_price <= 0:
        raise ValueError(f"entry_price must be positive, got {entry_price}")

    tier = _get_tier(conviction_score)

    win_rate = tier["win_rate"]
    b = tier["avg_win_loss_ratio"]
    max_pct = tier["max_pct"]
    size_label = tier["size_label"]

    # Standard Kelly criterion: f* = (b·p − q) / b
    q = 1.0 - win_rate
    kelly_fraction = (b * win_rate - q) / b
    adjusted_kelly = kelly_fraction * _KELLY_FRACTION

    # Apply tier cap first, then absolute hard cap
    raw_pct = min(adjusted_kelly, max_pct)
    pct_of_portfolio = min(raw_pct, _HARD_POSITION_CAP)

    dollar_size = pct_of_portfolio * portfolio_value
    share_count = int(dollar_size // entry_price)

    if share_count == 0:
        raise ValueError(
            f"Computed share_count is 0: dollar_size ${dollar_size:.2f} < entry_price ${entry_price:.2f}. "
            f"Increase portfolio_value or lower entry_price threshold."
        )

    stop_loss_price = _compute_stop_loss(entry_price, regime)

    sizing_rationale = (
        f"Conviction {conviction_score:.1f} → {size_label} ({pct_of_portfolio * 100:.1f}%): "
        f"Kelly f*={kelly_fraction:.4f} (p={win_rate}, b={b}), adjusted {adjusted_kelly:.4f}, "
        f"capped at {pct_of_portfolio * 100:.1f}% of "
        f"${portfolio_value:,.2f} = ${dollar_size:,.2f}"
    )

    return {
        "kelly_fraction": round(kelly_fraction, 6),
        "dollar_size": round(dollar_size, 2),
        "share_count": share_count,
        "size_label": size_label,
        "pct_of_portfolio": round(pct_of_portfolio, 6),
        "stop_loss_price": round(stop_loss_price, 4),
        "sizing_rationale": sizing_rationale,
    }
