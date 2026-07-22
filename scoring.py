"""Offline demo provider.

Generates a deterministic synthetic market (131 sessions x ~40 tickers) with
planted archetypes, so you can exercise the entire pipeline — scan, report,
journal, eval — with zero network:

    IGNA — volume ignition: 6.5x RVOL, +9.5% close pinned at highs, heavy AH pop
    COIL — coiled spring: 12 sessions of range compression + earnings tomorrow
    SQZE — squeeze fuel: 35% short interest, 5 straight up days on volume
    DMPD — capitulation: -12% on 5x volume, close at lows, AH still bleeding
    MEME — social favorite: elevated volume, 22% SI, modest AH drift
    NOVA — quiet, but earnings in 3 sessions

The planted names also get planted *next-day* outcomes (high RVOL / wide
range, with noise), so `eval --demo` demonstrates a positive IC and edge > 1x.
"""
from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np
import pandas as pd

from .providers import Provider
from .util import NY, next_trading_day, ny_today

_RANDOM_NAMES = [
    "ROTA",
    "ABLE", "BRXT", "CYGN", "DELT", "EPHR", "FLUX", "GRDN", "HLIX", "IRIS",
    "JOLT", "KRNL", "LMNA", "MTRX", "NBLA", "OPAL", "PRSM", "QNTA", "RVNA",
    "STRL", "TLLY", "UMBR", "VRTX2", "WOLF2", "XENO", "YARO", "ZEPH", "ACRU",
    "BLNK2", "CRTR", "DWLL", "EMBR", "FNCH", "GLDE", "HRZN",
]
PLANTED = ["IGNA", "COIL", "SQZE", "DMPD", "MEME", "NOVA", "MID1", "MID2", "MID3", "MID4"]


def _fix_ohlc(o, h, l, c):
    h = max(h, o, c)
    l = min(l, o, c)
    return o, h, l, c


class DemoProvider(Provider):
    name = "demo"

    def __init__(self, include_next: bool = False):
        self.include_next = include_next
        self.asof = self._last_bday(ny_today())
        end = next_trading_day(self.asof)
        self.dates = pd.bdate_range(end=pd.Timestamp(end), periods=131)
        self.tickers = PLANTED + _RANDOM_NAMES
        self.data = {t: self._series(t) for t in self.tickers}
        self._plant_daily()
        self._plant_next_day()

    @staticmethod
    def _last_bday(d: dt.date) -> dt.date:
        ts = pd.Timestamp(d)
        while ts.weekday() >= 5:
            ts -= pd.Timedelta(days=1)
        return ts.date()

    # -- synthetic daily bars ---------------------------------------------
    def _rng(self, ticker: str, salt: str = "") -> np.random.Generator:
        key = f"ignition-demo|{ticker}|{salt}".encode()
        seed = int.from_bytes(hashlib.sha256(key).digest()[:4], "big")
        return np.random.default_rng(seed)

    def _series(self, t: str) -> pd.DataFrame:
        rng = self._rng(t)
        n = len(self.dates)
        p0 = float(np.exp(rng.uniform(np.log(4), np.log(280))))
        rets = rng.normal(0.0004, 0.022, n)
        close = p0 * np.exp(np.cumsum(rets))
        openp = np.empty(n)
        openp[0] = close[0]
        openp[1:] = close[:-1] * (1 + rng.normal(0, 0.004, n - 1))
        span = np.abs(rng.normal(0, 0.012, n)) + 0.006
        high = np.maximum(openp, close) * (1 + span / 2)
        low = np.minimum(openp, close) * (1 - span / 2)
        vbase = float(np.exp(rng.uniform(np.log(8e5), np.log(2.5e7))))
        vol = vbase * np.exp(rng.normal(0, 0.35, n))
        df = pd.DataFrame(
            {"Open": openp, "High": high, "Low": low, "Close": close, "Volume": vol},
            index=self.dates,
        )
        return df

    def _adv(self, df, i):
        return float(df["Volume"].iloc[i - 20 : i].mean())

    def _set_day(self, t, i, ret=None, vol_x=None, cs=None, gap=None, span=None):
        df = self.data[t]
        prev = float(df["Close"].iloc[i - 1])
        c = prev * (1 + ret) if ret is not None else float(df["Close"].iloc[i])
        o = prev * (1 + (gap if gap is not None else 0.002))
        sp = span if span is not None else max(abs(c / prev - 1) * 1.3, 0.02)
        hi = max(o, c) * (1 + sp * 0.25)
        lo = min(o, c) * (1 - sp * 0.25)
        if cs is not None:  # force close position within the range
            full = hi - lo
            if full > 0:
                c = lo + cs * full
        o, hi, lo, c = _fix_ohlc(o, hi, lo, c)
        v = self._adv(df, i) * vol_x if vol_x is not None else float(df["Volume"].iloc[i])
        df.iloc[i] = [o, hi, lo, c, v]

    def _plant_daily(self):
        i = -2  # scan day (last visible bar when include_next=False)
        self._set_day("IGNA", i, ret=0.095, vol_x=6.5, cs=0.95, gap=0.02, span=0.11)
        self._set_day("DMPD", i, ret=-0.12, vol_x=5.0, cs=0.06, gap=-0.03, span=0.14)
        self._set_day("MEME", i, ret=0.041, vol_x=2.3, cs=0.82, gap=0.01, span=0.06)
        for k, mret, mvol in (("MID1", 0.028, 2.4), ("MID2", -0.024, 2.1),
                              ("MID3", 0.019, 1.8), ("MID4", 0.015, 1.7)):
            self._set_day(k, i, ret=mret, vol_x=mvol, cs=0.7, span=0.05)
        # SQZE: five-day momentum ramp
        for j, (r, vx) in enumerate([(0.03, 1.3), (0.034, 1.5), (0.038, 1.8),
                                     (0.042, 2.1), (0.05, 2.6)]):
            self._set_day("SQZE", i - 4 + j, ret=r, vol_x=vx, cs=0.85, span=0.06)
        # COIL: 12 sessions of compression, last day NR7-tight
        df = self.data["COIL"]
        for j in range(13, 0, -1):
            idx = i - j + 1
            prev = float(df["Close"].iloc[idx - 1])
            rng = self._rng("COIL", f"c{j}")
            c = prev * (1 + rng.normal(0, 0.0018))
            tight = 0.0009 if j == 1 else 0.0022
            o = prev * (1 + rng.normal(0, 0.0008))
            hi, lo = max(o, c) * (1 + tight), min(o, c) * (1 - tight)
            o, hi, lo, c = _fix_ohlc(o, hi, lo, c)
            df.iloc[idx] = [o, hi, lo, c, self._adv(df, idx) * 0.75]

    def _plant_next_day(self):
        i = -1
        nxt = {
            "IGNA": (0.04, 5.0, 0.13, 0.06), "COIL": (-0.05, 4.2, 0.09, -0.06),
            "SQZE": (0.07, 3.1, 0.10, 0.04), "DMPD": (-0.02, 3.0, 0.08, 0.01),
            "MEME": (0.03, 2.2, 0.06, 0.02), "NOVA": (0.005, 1.4, 0.03, 0.0),
            "MID1": (0.012, 1.7, 0.045, 0.01), "MID2": (-0.01, 1.5, 0.04, -0.005),
            "MID3": (0.008, 1.4, 0.035, 0.005), "MID4": (0.004, 1.3, 0.03, 0.0),
        }
        for t, (ret, vx, span, gap) in nxt.items():
            noise = self._rng(t, "next").normal(0, 0.15)
            self._set_day(t, i, ret=ret, vol_x=max(vx + vx * noise, 0.8),
                          cs=None, gap=gap, span=span)
        # unplanted names: mild real-world persistence — today's relative
        # volume and range bleed into tomorrow, plus noise
        for t in self.tickers:
            if t in nxt:
                continue
            df = self.data[t]
            rng = self._rng(t, "carry")
            adv = self._adv(df, i - 1)
            prev_rvol = float(df["Volume"].iloc[i - 1]) / max(adv, 1.0)
            prev_span = float((df["High"].iloc[i - 1] - df["Low"].iloc[i - 1])
                              / df["Close"].iloc[i - 1])
            vx = max(0.4, (0.55 + 0.45 * prev_rvol) * np.exp(rng.normal(0, 0.22)))
            span = max(0.008, (0.010 + 0.55 * prev_span) * np.exp(rng.normal(0, 0.25)))
            self._set_day(t, i, ret=float(rng.normal(0, span / 3)), vol_x=vx,
                          gap=float(rng.normal(0, span / 5)), span=span * 1.6)

    # -- Provider interface ------------------------------------------------
    def get_history(self, tickers, days: int = 130):
        out = {}
        for t in tickers:
            if t not in self.data:
                continue
            df = self.data[t] if self.include_next else self.data[t].iloc[:-1]
            out[t] = df.copy()
        return out

    def get_intraday_1m(self, ticker):
        if ticker not in self.data:
            return None
        rng = self._rng(ticker, "intra")
        day = pd.Timestamp(self.asof)
        pm = pd.date_range(day + pd.Timedelta(hours=7),
                           day + pd.Timedelta(hours=9, minutes=29), freq="1min", tz=NY)
        reg = pd.date_range(day + pd.Timedelta(hours=9, minutes=30),
                            day + pd.Timedelta(hours=15, minutes=59), freq="1min", tz=NY)
        ah = pd.date_range(day + pd.Timedelta(hours=16),
                           day + pd.Timedelta(hours=17, minutes=59), freq="1min", tz=NY)
        df = self.data[ticker].iloc[:-1]
        close = float(df["Close"].iloc[-1])
        openp = float(df["Open"].iloc[-1])
        adv = float(df["Volume"].iloc[-21:-1].mean())
        ah_plans = {"IGNA": (0.052, 0.060), "DMPD": (-0.041, 0.040),
                    "MEME": (0.028, 0.015), "COIL": (0.004, 0.003),
                    "NOVA": (0.012, 0.006), "SQZE": (0.018, 0.012)}
        ah_ret, ah_frac = ah_plans.get(ticker, (rng.normal(0, 0.004), 0.002))
        pm_path = np.linspace(close, close * (1 + ah_ret * 0.7), len(pm)) * (
            1 + rng.normal(0, 0.0012, len(pm)))
        reg_path = np.linspace(openp, close, len(reg)) * (1 + rng.normal(0, 0.0015, len(reg)))
        ah_path = np.linspace(close, close * (1 + ah_ret), len(ah)) * (
            1 + rng.normal(0, 0.0012, len(ah)))
        ah_path[-1] = close * (1 + ah_ret)
        prices = np.concatenate([pm_path, reg_path, ah_path])
        idx = pm.append(reg).append(ah)
        u = np.abs(np.sin(np.linspace(0.2, np.pi - 0.2, len(reg))) - 0.5) + 0.3
        reg_vol = u / u.sum() * float(df["Volume"].iloc[-1])
        pm_vol = np.full(len(pm), (adv * max(ah_frac * 0.5, 0.001)) / len(pm))
        ah_vol = np.full(len(ah), (adv * ah_frac) / len(ah))
        vols = np.concatenate([pm_vol, reg_vol, ah_vol])
        return pd.DataFrame({"Open": prices, "High": prices * 1.0005,
                             "Low": prices * 0.9995, "Close": prices,
                             "Volume": vols}, index=idx)

    def get_next_earnings(self, ticker):
        if ticker == "COIL":
            return next_trading_day(self.asof)
        if ticker == "NOVA":
            return self.asof + dt.timedelta(days=3)
        return None

    def get_short_stats(self, ticker):
        plans = {"SQZE": (0.35, 12.0), "IGNA": (0.28, 6.0), "MEME": (0.22, 4.0)}
        if ticker in plans:
            spf, dtc = plans[ticker]
            return {"short_pct_float": spf, "days_to_cover": dtc, "float_shares": 5e7}
        rng = self._rng(ticker, "short")
        return {"short_pct_float": float(rng.uniform(0.005, 0.06)),
                "days_to_cover": float(rng.uniform(0.5, 3.0)), "float_shares": 1e8}

    def get_atm_iv(self, ticker, spot):
        plans = {"IGNA": 1.40, "COIL": 1.05, "SQZE": 0.95, "MEME": 0.85, "DMPD": 1.10}
        if ticker in plans:
            return plans[ticker]
        return float(self._rng(ticker, "iv").uniform(0.30, 0.55))

    def screen(self, key, limit: int = 100):
        table = {
            "most_actives": PLANTED + _RANDOM_NAMES,
            "day_gainers": ["IGNA", "MEME", "SQZE", "MID1", "MID3"],
            "day_losers": ["DMPD", "MID2"],
            "small_cap_gainers": ["MID1", "MID4", "JOLT"],
            "most_shorted_stocks": ["SQZE", "MEME", "IGNA"],
        }
        return table.get(key, [])[:limit]

# ---------------------------------------------------------------------------
# FLOW demo: a scripted "morning after the scan" session
# ---------------------------------------------------------------------------
FLOW_CAST = ["IGNA", "SQZE", "COIL", "ROTA", "MEME", "DMPD",
             "MID1", "MID2", "MID3", "MID4", "FLUX", "ABLE", "XENO", "PRSM"]

# anchors: (minute-of-session, price multiple vs prev close, trailing pace ×)
_FLOW_SCRIPTS = {
    # last night's #1 pick: pumps at the open, then the money quietly leaves
    # while the chart still looks perfect  →  FADING by late morning
    "IGNA": {"pm": (0.5, 1.048, 0.030),
             "a": [(0, 1.052, 7.0), (25, 1.115, 6.0), (50, 1.121, 3.2),
                   (80, 1.109, 1.7), (120, 1.112, 0.85), (170, 1.106, 0.55),
                   (250, 1.098, 0.5), (330, 1.088, 0.6), (389, 1.082, 0.9)]},
    # keeps working all day: the kind of tape you stay with
    "SQZE": {"pm": (0.2, 1.018, 0.008),
             "a": [(0, 1.020, 3.0), (60, 1.046, 2.6), (150, 1.058, 2.2),
                   (250, 1.075, 2.4), (389, 1.092, 2.9)]},
    # earnings gap-down, failed bounce, VWAP lost on volume  →  LEAVING
    "COIL": {"pm": (0.9, 0.941, 0.024),
             "a": [(0, 0.944, 5.5), (20, 0.976, 5.0), (45, 0.956, 4.2),
                   (90, 0.928, 3.6), (150, 0.914, 2.9), (240, 0.906, 2.2),
                   (389, 0.898, 1.9)]},
    # nobody's watching it at the open; the money rotates in before lunch
    # →  NEW MONEY → IGNITING while the crowd is still staring at IGNA
    "ROTA": {"pm": None,
             "a": [(0, 1.001, 0.6), (90, 0.998, 0.6), (120, 1.004, 1.3),
                   (150, 1.019, 2.7), (185, 1.040, 4.8), (225, 1.054, 5.4),
                   (300, 1.061, 3.6), (389, 1.067, 3.1)]},
    # heavy tape, zero progress: absorption fight at the highs  →  CHURN
    "MEME": {"pm": (0.3, 1.028, 0.012), "wiggle": 0.004,
             "a": [(0, 1.030, 3.2), (40, 1.052, 3.0), (389, 1.049, 2.5)]},
    # yesterday's flush keeps bleeding on shrinking tape  →  COOLING
    "DMPD": {"pm": None,
             "a": [(0, 0.990, 2.2), (120, 0.968, 1.5), (389, 0.954, 1.0)]},
}


def _bg_script(t: str) -> dict:
    h = int.from_bytes(hashlib.sha256(f"bg|{t}".encode()).digest()[:2], "big")
    pace = 0.7 + (h % 60) / 100.0
    drift = ((h >> 8) % 21 - 10) / 1000.0
    return {"pm": None, "a": [(0, 1.0, pace), (389, 1.0 + drift, pace)]}


class FlowDemo:
    """Scripted 1m session for `flow --demo`, continuous with the scan demo:
    the day being watched is the target day of last night's evening scan."""

    def __init__(self):
        self.p = DemoProvider(include_next=False)
        daily = self.p.get_history(FLOW_CAST, days=40)
        self.prev_close = {t: float(d["Close"].iloc[-1]) for t, d in daily.items()}
        self.adv = {t: float(d["Volume"].iloc[-20:].mean()) for t, d in daily.items()}
        self.day = next_trading_day(self.p.asof)
        cum = None
        from .flow import default_curve
        cum = default_curve()
        self.u = np.diff(cum, prepend=0.0) * len(cum)   # intensity, mean 1
        self.bars = {t: self._script(t) for t in FLOW_CAST}

    def _script(self, t: str) -> pd.DataFrame:
        sc = _FLOW_SCRIPTS.get(t) or _bg_script(t)
        rng = np.random.default_rng(
            int.from_bytes(hashlib.sha256(f"flow|{t}".encode()).digest()[:4], "big"))
        pc, adv = self.prev_close[t], self.adv[t]
        day = pd.Timestamp(self.day)
        mins = np.arange(390)
        am, mm, pm_ = zip(*sc["a"])
        px = np.interp(mins, am, mm) * pc
        if sc.get("wiggle"):
            px = px * (1 + sc["wiggle"] * np.sin(mins / 9.0))
        px = px * (1 + rng.normal(0, 0.0007, 390))
        pace = np.interp(mins, am, pm_)
        vol = adv / 390.0 * self.u * pace * np.exp(rng.normal(0, 0.12, 390))
        idx = pd.date_range(day + pd.Timedelta(hours=9, minutes=30),
                            periods=390, freq="1min", tz=NY)
        o = np.concatenate([[px[0]], px[:-1]])
        h = np.maximum(o, px) * (1 + np.abs(rng.normal(0, 0.0006, 390)))
        lo = np.minimum(o, px) * (1 - np.abs(rng.normal(0, 0.0006, 390)))
        df = pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": px,
                           "Volume": vol}, index=idx)
        if sc.get("pm"):
            move_end, mult_end, frac = sc["pm"]
            pidx = pd.date_range(day + pd.Timedelta(hours=8),
                                 day + pd.Timedelta(hours=9, minutes=29),
                                 freq="1min", tz=NY)
            path = np.linspace(pc * (1 + (mult_end - 1) * 0.2), pc * mult_end,
                               len(pidx)) * (1 + rng.normal(0, 0.001, len(pidx)))
            pv = np.full(len(pidx), adv * frac / len(pidx))
            pmdf = pd.DataFrame({"Open": path, "High": path * 1.0006,
                                 "Low": path * 0.9994, "Close": path,
                                 "Volume": pv}, index=pidx)
            df = pd.concat([pmdf, df])
        return df

    def bars_until(self, now) -> dict[str, pd.DataFrame]:
        return {t: d[d.index <= now] for t, d in self.bars.items()}
