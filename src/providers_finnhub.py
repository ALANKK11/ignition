"""Finnhub earnings calendar — one HTTP call covers every listed symbol,
with BMO/AMC timing, replacing Yahoo's flaky per-ticker earnings lookups."""

from __future__ import annotations

import datetime as dt
import os

import requests

from .util import NY


def key() -> str | None:
    k = os.environ.get("FINNHUB_KEY", "").strip()
    return k or None


def earnings_map(token: str, start: dt.date, days: int = 6) -> dict[str, dt.date] | None:
    """symbol → next earnings date within the window. Returns None on any
    failure so callers fall back to the Yahoo per-ticker path.

    Timing refinement: a BMO report whose date is already behind us in session
    terms (this morning, and it's now after the close) is a *spent* catalyst —
    excluded so it can't masquerade as tomorrow's event."""
    try:
        r = requests.get("https://finnhub.io/api/v1/calendar/earnings",
                         params={"from": start.isoformat(),
                                 "to": (start + dt.timedelta(days=days)).isoformat(),
                                 "token": token}, timeout=20)
        if r.status_code != 200:
            return None
        rows = (r.json() or {}).get("earningsCalendar") or []
    except Exception:
        return None
    now = dt.datetime.now(NY)
    after_close = now.hour >= 16
    out: dict[str, dt.date] = {}
    for e in rows:
        try:
            sym = (e.get("symbol") or "").upper()
            d = dt.date.fromisoformat(e["date"])
            hour = (e.get("hour") or "").lower()      # bmo / amc / dmh / ""
            if d == now.date() and hour == "bmo" and after_close:
                continue                               # already reported today
            if sym and (sym not in out or d < out[sym]):
                out[sym] = d
        except Exception:
            continue
    return out
