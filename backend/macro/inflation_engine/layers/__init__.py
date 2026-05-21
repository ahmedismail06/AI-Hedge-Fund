"""Inflation scoring layers — each is a pure function returning LayerOutput.

Modules
-------
structural            -- level-based YoY series (CPI/Core/PPI/PCE/sticky/trimmed-mean)
momentum              -- rate-of-change (3m-annualised, slope, acceleration)
market_expectations   -- forward-looking (breakevens, commodities, 5y5y)
surprise              -- actual vs consensus (PR3 stub — returns confidence=0)
"""
