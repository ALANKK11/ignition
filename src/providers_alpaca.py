"""
Alpaca Market Data v2 client (free tier, IEX feed) — no SDK, plain REST.

Unit-consistency note that matters for the math: the free feed is IEX, which
prints roughly 2–3% of consolidated tape. That's useless for absolute volume
but fine for RELATIVE participation — as long as every ratio uses IEX on both
sides. So the flow engine on Alpaca runs all-IEX: ADV from IEX daily bars,
curves from IEX minute bars, today's tape from IEX minute bars. PACE and TP
stay internally consistent. The nightly SCAN keeps using Yahoo's consolidated
daily volume, also internally consistent. Never mix the two feeds in a ratio.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import time

import pandas as pd
import requests

from .util import NY

DATA = "https://data.alpaca.markets"
TRADE = "https://paper-api.alpaca.markets"
_SYM_OK = re.compile(r"^[A-Z]{1,5}$")


def creds() -> tuple[str, str] | None:
    k = os.environ.get("ALPACA_KEY_ID", "").strip()
    s = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    return (k, s) if k and s else None


class AlpacaData:
    def __init__(self, key: str, secret: str):
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.name = "alpaca-iex"

    def _get(self, base: str, path: str, params: dict) -> dict | list | None:
        for attempt in (1, 2):
            try:
                r = requests.get(base + path, headers=self.h, params=params,
                                 timeout=25)
                if r.status_code == 429:
                    time.sleep(2.5)
                    continue
                if r.status_code != 200:
                    return None
                return r.json()
            except Exception:
                if attempt == 2:
                    return None
                time.sleep(1.0)
        return None

    # -- universe ----------------------------------------------------------
    def active_symbols(self) -> list[str]:
        js = self._get(TRADE, "/v2/assets",
                       {"status": "active", "asset_class": "us_equity"})
        if not isinstance(js, list):
            return []
        out = []
        for a in js:
            try:
                if not a.get("tradable"):
                    continue
                if a.get("exchange") not in ("NYSE", "NASDAQ", "ARCA", "AMEX",
                                             "BATS", "NYSEARCA"):
                    continue
                s = a.get("symbol", "")
                if _SYM_OK.match(s):
                    out.append(s)
            except Exception:
                continue
        return sorted(set(out))

    # -- snapshots (the full-market radar read) ----------------------------
    def snapshots(self, symbols: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), 400):
            js = self._get(DATA, "/v2/stocks/snapshots",
                           {"symbols": ",".join(symbols[i:i + 400]),
                            "feed": "iex"})
            if isinstance(js, dict):
                out.update({k: v for k, v in js.items() if isinstance(v, dict)})
        return out

    # -- bars --------------------------------------------------------------
    def bars(self, symbols: list[str], timeframe: str, start: str,
             end: str | None = None) -> dict[str, pd.DataFrame]:
        acc: dict[str, list] = {}
        for i in range(0, len(symbols), 200):
            chunk = ",".join(symbols[i:i + 200])
            params = {"symbols": chunk, "timeframe": timeframe, "start": start,
                      "limit": 10000, "feed": "iex", "adjustment": "raw",
                      "sort": "asc"}
            if end:
                params["end"] = end
            token = None
            for _ in range(40):                      # pagination guard
                if token:
                    params["page_token"] = token
                js = self._get(DATA, "/v2/stocks/bars", params)
                if not isinstance(js, dict):
                    break
                for sym, rows in (js.get("bars") or {}).items():
                    acc.setdefault(sym, []).extend(rows or [])
                token = js.get("next_page_token")
                if not token:
                    break
        out: dict[str, pd.DataFrame] = {}
        for sym, rows in acc.items():
            try:
                df = pd.DataFrame(rows)
                if df.empty:
                    continue
                df = df.rename(columns={"o": "Open", "h": "High", "l": "Low",
                                        "c": "Close", "v": "Volume"})
                idx = pd.to_datetime(df["t"], utc=True).dt.tz_convert(NY)
                df = df[["Open", "High", "Low", "Close", "Volume"]]
                df.index = idx
                out[sym] = df.sort_index()
            except Exception:
                continue
        return out

    def daily(self, symbols: list[str], days: int = 30) -> dict[str, pd.DataFrame]:
        start = (dt.date.today() - dt.timedelta(days=int(days * 1.7) + 6)).isoformat()
        return self.bars(symbols, "1Day", start)

    def minute_today(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        start = dt.datetime.combine(dt.datetime.now(NY).date(), dt.time(8, 0),
                                    tzinfo=NY).isoformat()
        return self.bars(symbols, "1Min", start)

    def minute_recent(self, symbols: list[str], days: int = 5) -> dict[str, pd.DataFrame]:
        start = (dt.date.today() - dt.timedelta(days=days + 4)).isoformat()
        return self.bars(symbols, "1Min", start)
