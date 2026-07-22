"""Data providers.

LiveProvider wraps yfinance defensively: every call that can fail returns None /
empty instead of raising, so a flaky ticker never kills a scan. The Provider
interface is deliberately small — swap in Polygon/IBKR/etc. by implementing the
same six methods (see README).
"""
from __future__ import annotations

import datetime as dt
import logging
import time
import warnings

import numpy as np
import pandas as pd

from .util import NY, ny_today

warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


class Provider:
    """Interface. All methods must be exception-safe."""

    name = "base"

    def get_history(self, tickers: list[str], days: int = 130) -> dict[str, pd.DataFrame]:
        raise NotImplementedError

    def get_intraday_1m(self, ticker: str) -> pd.DataFrame | None:
        raise NotImplementedError

    def get_next_earnings(self, ticker: str) -> dt.date | None:
        raise NotImplementedError

    def get_short_stats(self, ticker: str) -> dict | None:
        raise NotImplementedError

    def get_atm_iv(self, ticker: str, spot: float) -> float | None:
        raise NotImplementedError

    def screen(self, key: str, limit: int = 100) -> list[str]:
        raise NotImplementedError


# ---------------------------------------------------------------------------


def _clean_symbol(s: str) -> str | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip().upper()
    if not s or any(c in s for c in "^=/ "):
        return None
    if not all(c.isalnum() or c in ".-" for c in s):
        return None
    return s


class LiveProvider(Provider):
    name = "live"

    def __init__(self):
        import yfinance as yf  # deferred so --demo works fully offline

        self.yf = yf

    # -- daily history -----------------------------------------------------
    def get_history(self, tickers: list[str], days: int = 130) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        start = (ny_today() - dt.timedelta(days=int(days * 1.7) + 10)).isoformat()
        chunks = [tickers[i : i + 120] for i in range(0, len(tickers), 120)]
        for chunk in chunks:
            try:
                df = self.yf.download(
                    tickers=chunk,
                    start=start,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=False,
                    actions=False,
                    threads=True,
                    progress=False,
                )
            except Exception:
                continue
            if df is None or df.empty:
                continue
            if isinstance(df.columns, pd.MultiIndex):
                for t in chunk:
                    try:
                        sub = df[t].dropna(how="all")
                    except Exception:
                        continue
                    sub = self._normalize(sub)
                    if sub is not None:
                        out[t] = sub
            else:  # single ticker came back flat
                sub = self._normalize(df.dropna(how="all"))
                if sub is not None and len(chunk) == 1:
                    out[chunk[0]] = sub
        return out

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame | None:
        try:
            if df is None or df.empty:
                return None
            cols = {c.title(): c for c in df.columns}
            if not all(k in cols for k in REQUIRED_COLS):
                return None
            df = df.rename(columns={v: k for k, v in cols.items()})[REQUIRED_COLS]
            df = df.apply(pd.to_numeric, errors="coerce")
            df = df.dropna(subset=["Close", "Volume"])
            df = df[df["Volume"] >= 0]
            if len(df) < 40:
                return None
            idx = pd.to_datetime(df.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_convert(NY).tz_localize(None)
            df.index = idx.normalize()
            df = df[~df.index.duplicated(keep="last")].sort_index()
            return df
        except Exception:
            return None

    # -- intraday (regular + extended hours) -------------------------------
    def get_intraday_1m(self, ticker: str) -> pd.DataFrame | None:
        try:
            tk = self.yf.Ticker(ticker)
            df = tk.history(period="2d", interval="1m", prepost=True, auto_adjust=False)
            if df is None or df.empty:
                return None
            df = df.rename(columns={c: c.title() for c in df.columns})
            keep = [c for c in REQUIRED_COLS if c in df.columns]
            df = df[keep].dropna(subset=["Close"])
            idx = pd.to_datetime(df.index)
            if getattr(idx, "tz", None) is None:
                idx = idx.tz_localize(NY)
            else:
                idx = idx.tz_convert(NY)
            df.index = idx
            return df.sort_index()
        except Exception:
            return None

    # -- earnings ----------------------------------------------------------
    def get_next_earnings(self, ticker: str) -> dt.date | None:
        today = ny_today()
        try:
            tk = self.yf.Ticker(ticker)
            try:
                ed = tk.get_earnings_dates(limit=8)
                if ed is not None and len(ed) > 0:
                    dates = sorted(
                        d.date() if hasattr(d, "date") else d
                        for d in pd.to_datetime(ed.index)
                    )
                    fut = [d for d in dates if d >= today]
                    if fut:
                        return fut[0]
            except Exception:
                pass
            try:  # fallback: calendar dict
                cal = tk.calendar
                if isinstance(cal, dict):
                    raw = cal.get("Earnings Date") or []
                    if not isinstance(raw, (list, tuple)):
                        raw = [raw]
                    fut = sorted(
                        d for d in (pd.Timestamp(x).date() for x in raw if x) if d >= today
                    )
                    if fut:
                        return fut[0]
            except Exception:
                pass
        except Exception:
            pass
        return None

    # -- short interest ----------------------------------------------------
    def get_short_stats(self, ticker: str) -> dict | None:
        try:
            info = self.yf.Ticker(ticker).info or {}
            spf = info.get("shortPercentOfFloat")
            dtc = info.get("shortRatio")
            flt = info.get("floatShares")
            if spf is None and dtc is None:
                return None
            return {
                "short_pct_float": float(spf) if spf is not None else None,
                "days_to_cover": float(dtc) if dtc is not None else None,
                "float_shares": float(flt) if flt is not None else None,
            }
        except Exception:
            return None

    # -- options: nearest-expiry ATM implied vol ---------------------------
    def get_atm_iv(self, ticker: str, spot: float) -> float | None:
        try:
            tk = self.yf.Ticker(ticker)
            exps = tk.options
            if not exps:
                return None
            today = ny_today()
            pick = None
            for e in exps:
                try:
                    d = dt.date.fromisoformat(e)
                except Exception:
                    continue
                if d >= today:
                    pick = e
                    break
            if pick is None:
                pick = exps[-1]
            ch = tk.option_chain(pick)
            ivs = []
            for side in (ch.calls, ch.puts):
                if side is None or side.empty or "impliedVolatility" not in side:
                    continue
                side = side.dropna(subset=["strike", "impliedVolatility"])
                if side.empty:
                    continue
                row = side.iloc[(side["strike"] - spot).abs().argsort().iloc[0]]
                iv = float(row["impliedVolatility"])
                if 0.01 < iv < 8.0:
                    ivs.append(iv)
            if not ivs:
                return None
            return float(np.mean(ivs))
        except Exception:
            return None

    # -- predefined screeners ---------------------------------------------
    def screen(self, key: str, limit: int = 100) -> list[str]:
        quotes = None
        try:  # yfinance >= 0.2.4x
            res = self.yf.screen(key)
            if isinstance(res, dict):
                quotes = res.get("quotes")
        except Exception:
            quotes = None
        if quotes is None:
            try:  # older API
                s = self.yf.Screener()
                s.set_predefined_body(key)
                res = s.response
                if isinstance(res, dict):
                    quotes = res.get("quotes")
            except Exception:
                quotes = None
        out = []
        for q in quotes or []:
            sym = _clean_symbol(q.get("symbol") if isinstance(q, dict) else None)
            if sym:
                out.append(sym)
        time.sleep(0.2)  # be polite between screener hits
        return out[:limit]

    # -- batched intraday for FLOW mode ------------------------------------
    def _batch_1m(self, tickers: list[str], period: str,
                  prepost: bool) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(tickers), 80):
            chunk = tickers[i:i + 80]
            try:
                raw = self.yf.download(chunk, period=period, interval="1m",
                                       prepost=prepost, auto_adjust=False,
                                       group_by="ticker", threads=True,
                                       progress=False)
            except Exception:
                continue
            if raw is None or raw.empty:
                continue
            for t in chunk:
                try:
                    df = raw[t].dropna(subset=["Close"]) if len(chunk) > 1 \
                        else raw.dropna(subset=["Close"])
                    if df.empty:
                        continue
                    df = df.rename(columns={c: str(c).title() for c in df.columns})
                    keep = [c for c in REQUIRED_COLS if c in df.columns]
                    df = df[keep]
                    idx = pd.to_datetime(df.index)
                    idx = idx.tz_localize(NY) if getattr(idx, "tz", None) is None \
                        else idx.tz_convert(NY)
                    df.index = idx
                    out[t] = df.sort_index()
                except Exception:
                    continue
        return out

    def get_intraday_batch(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """Today's 1m bars including extended hours, one batched call."""
        return self._batch_1m(tickers, period="1d", prepost=True)

    def get_intraday_recent(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """~5 sessions of 1m bars (regular hours) for volume-curve baselines."""
        return self._batch_1m(tickers, period="5d", prepost=False)
