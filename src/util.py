"""Shared utilities: NY-time helpers, trading-day math, numeric helpers."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")


def ny_now() -> dt.datetime:
    return dt.datetime.now(tz=NY)


def ny_today() -> dt.date:
    return ny_now().date()


def next_trading_day(d: dt.date) -> dt.date:
    """Next weekday after d (holidays not modeled; close enough for scan labeling)."""
    nxt = pd.Timestamp(d) + pd.tseries.offsets.BDay(1)
    return nxt.date()


def is_weekend(d: dt.date) -> bool:
    return d.weekday() >= 5


def clip01(x: float) -> float:
    if x is None or not np.isfinite(x):
        return 0.0
    return float(min(1.0, max(0.0, x)))


def clip(x: float, lo: float, hi: float) -> float:
    if x is None or not np.isfinite(x):
        return 0.0
    return float(min(hi, max(lo, x)))


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    try:
        if b == 0 or b is None or not np.isfinite(b):
            return default
        v = a / b
        return v if np.isfinite(v) else default
    except Exception:
        return default


def pct(x: float) -> str:
    """Format a fraction as +x.x%."""
    if x is None or not np.isfinite(x):
        return "  --  "
    return f"{x * 100:+.1f}%"


def fmt_big(n: float) -> str:
    """Human format for share/dollar counts."""
    if n is None or not np.isfinite(n):
        return "--"
    a = abs(n)
    if a >= 1e9:
        return f"{n / 1e9:.1f}B"
    if a >= 1e6:
        return f"{n / 1e6:.1f}M"
    if a >= 1e3:
        return f"{n / 1e3:.0f}K"
    return f"{n:.0f}"


def spearman(a, b) -> float:
    """Spearman rank correlation without scipy."""
    s1 = pd.Series(a, dtype=float)
    s2 = pd.Series(b, dtype=float)
    mask = s1.notna() & s2.notna()
    if mask.sum() < 3:
        return float("nan")
    r1 = s1[mask].rank()
    r2 = s2[mask].rank()
    if r1.std() == 0 or r2.std() == 0:
        return float("nan")
    return float(np.corrcoef(r1, r2)[0, 1])
