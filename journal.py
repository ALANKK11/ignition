"""Signal engine.

Base pass (cheap, whole universe, daily bars only):
    rvol, vol_trend, atr_expansion, squeeze(+NR7), closing_strength, breakout
Enrichment pass (expensive, top-N only):
    afterhours / premarket, catalyst (earnings), short_fuel, options_heat

Every component is mapped onto a fixed, interpretable [0, 1] scale (not a
cross-sectional z-score) so that a 78 tonight means the same thing as a 78 last
month — which is what makes the journal's self-audit meaningful.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .util import clip, clip01, next_trading_day, safe_div

# ---------------------------------------------------------------------------
# volatility estimators
# ---------------------------------------------------------------------------


def yang_zhang_vol(df: pd.DataFrame, window: int = 20) -> float:
    """Annualized Yang-Zhang realized volatility (uses OHLC, ~7x more efficient
    than close-to-close)."""
    if len(df) < window + 2:
        return float("nan")
    d = df.iloc[-(window + 1):]
    o = np.log(d["Open"].values[1:] / d["Close"].values[:-1])
    c = np.log(d["Close"].values[1:] / d["Open"].values[1:])
    u = np.log(d["High"].values[1:] / d["Open"].values[1:])
    l = np.log(d["Low"].values[1:] / d["Open"].values[1:])
    n = len(c)
    if n < 5:
        return float("nan")
    so = np.var(o, ddof=1)
    sc = np.var(c, ddof=1)
    srs = np.mean(u * (u - c) + l * (l - c))
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    v = so + k * sc + (1 - k) * srs
    return float(np.sqrt(max(v, 0.0) * 252))


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["Close"].shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr


# ---------------------------------------------------------------------------
# base metrics (one dict of raw values per ticker)
# ---------------------------------------------------------------------------


def compute_base_metrics(df: pd.DataFrame) -> dict | None:
    """Raw, human-readable metrics from a daily OHLCV frame (ascending index)."""
    if df is None or len(df) < 45:
        return None
    c = df["Close"].values.astype(float)
    h = df["High"].values.astype(float)
    l = df["Low"].values.astype(float)
    o = df["Open"].values.astype(float)
    v = df["Volume"].values.astype(float)

    close, prev_close = c[-1], c[-2]
    if not np.isfinite(close) or close <= 0:
        return None

    v_hist = v[-21:-1]
    adv20 = float(np.mean(v_hist)) if len(v_hist) else 0.0
    if adv20 <= 0:
        return None
    vstd = float(np.std(v_hist, ddof=1)) if len(v_hist) > 2 else 0.0
    rvol = v[-1] / adv20
    vol_z = (v[-1] - adv20) / vstd if vstd > 0 else 0.0
    dollar_adv = adv20 * float(np.mean(c[-21:-1]))

    ret1 = close / prev_close - 1.0
    ret5 = close / c[-6] - 1.0 if len(c) >= 6 else 0.0
    gap = o[-1] / prev_close - 1.0

    rng = h[-1] - l[-1]
    closing_strength = 0.5 if rng <= 0 else (close - l[-1]) / rng

    tr = true_range(df)
    atr5 = float(tr.iloc[-5:].mean())
    atr20 = float(tr.iloc[-20:].mean())
    atr_ratio = safe_div(atr5, atr20, 1.0)
    atr20_pct = safe_div(atr20, close, 0.0)

    # Bollinger width percentile (squeeze detector)
    cs = df["Close"]
    mid = cs.rolling(20).mean()
    sd = cs.rolling(20).std()
    width = (4.0 * sd / mid).dropna()
    if len(width) >= 40:
        tail = width.iloc[-120:]
        bb_pct = float((tail <= tail.iloc[-1]).mean())
    else:
        bb_pct = 0.5

    ranges = h - l
    nr7 = bool(len(ranges) >= 7 and ranges[-1] <= np.min(ranges[-7:]) + 1e-12)

    max20h = float(np.max(h[-21:-1]))
    min20l = float(np.min(l[-21:-1]))
    prox_hi = safe_div(close, max20h, 0.0)          # >= 1 → new 20d high
    prox_lo = safe_div(min20l, close, 0.0)          # >= 1 → new 20d low

    # consecutive same-direction closes
    streak = 0
    for i in range(len(c) - 1, 0, -1):
        d_ = np.sign(c[i] - c[i - 1])
        if streak == 0:
            streak = int(d_)
        elif np.sign(streak) == d_ and d_ != 0:
            streak += int(d_)
        else:
            break

    return {
        "close": float(close),
        "prev_close": float(prev_close),
        "last_bar_date": df.index[-1].date(),
        "ret1": float(ret1),
        "ret5": float(ret5),
        "gap": float(gap),
        "rvol": float(rvol),
        "vol_z": float(vol_z),
        "adv20": adv20,
        "dollar_adv": float(dollar_adv),
        "closing_strength": float(closing_strength),
        "atr_ratio": float(atr_ratio),
        "atr20_pct": float(atr20_pct),
        "yz_vol": yang_zhang_vol(df),
        "bb_width_pct": bb_pct,
        "nr7": nr7,
        "prox_hi": prox_hi,
        "prox_lo": prox_lo,
        "streak": streak,
    }


def base_metrics_ok(m: dict, min_price: float, min_dollar_volume: float,
                    is_watchlist: bool = False) -> bool:
    """Liquidity / sanity gate. Your own watchlist bypasses the filters."""
    if is_watchlist:
        return True
    if m["close"] < min_price:
        return False
    if m["dollar_adv"] < min_dollar_volume:
        return False
    return True


# ---------------------------------------------------------------------------
# base components → [0, 1]
# ---------------------------------------------------------------------------


def base_components(m: dict, th: dict) -> dict:
    comp: dict[str, float | None] = {}

    # RVOL: log2 scale — 1x→0, 2x→0.33, 4x→0.67, 8x+→1.0
    lr = np.log2(max(m["rvol"], 1e-6))
    comp["rvol"] = clip(lr / th["rvol_log2_full"], 0.0, 1.0)

    # volume z vs own 20d distribution
    comp["vol_trend"] = clip(m["vol_z"] / 4.0, 0.0, 1.0)

    # ATR regime expansion (quiet tape earns 0, not a penalty — a coiled name
    # sitting on a catalyst SHOULD look dead the day before)
    comp["atr_expansion"] = clip((m["atr_ratio"] - 1.0) / 0.6, 0.0, 1.0)

    # squeeze: compressed Bollinger width percentile, plus NR7 kicker
    sq = 1.0 - m["bb_width_pct"]
    if m["nr7"]:
        sq = min(1.0, sq + 0.15)
    comp["squeeze"] = clip01(sq)

    # closing strength extremity: pinned at either end of the day's range
    comp["closing_strength"] = clip01(abs(m["closing_strength"] - 0.5) * 2.0)

    # breakout pressure, direction-agnostic
    up = clip((m["prox_hi"] - 0.95) / 0.05, 0.0, 1.2)
    dn = clip((m["prox_lo"] - 0.95) / 0.05, 0.0, 1.2)
    comp["breakout"] = clip01(max(up, dn))

    return comp


# ---------------------------------------------------------------------------
# enrichment components
# ---------------------------------------------------------------------------


def slice_extended_hours(
    intra: pd.DataFrame, session_date: dt.date, mode: str
) -> pd.DataFrame:
    """mode='evening' → after-hours bars (>=16:00) of session_date.
    mode='premarket' → pre-market bars (<09:30) of the *next* session (today)."""
    if intra is None or intra.empty:
        return pd.DataFrame()
    idx = intra.index
    dates = np.array([ts.date() for ts in idx])
    times = np.array([ts.time() for ts in idx])
    if mode == "premarket":
        target = max(d for d in np.unique(dates))
        mask = (dates == target) & (times < dt.time(9, 30))
    else:
        mask = (dates == session_date) & (times >= dt.time(16, 0))
    return intra.iloc[mask]


def afterhours_component(
    intra: pd.DataFrame, m: dict, th: dict, mode: str
) -> tuple[float | None, dict]:
    ref = m["close"]
    ext = slice_extended_hours(intra, m["last_bar_date"], mode)
    info = {"ah_ret": None, "ah_vol": 0.0, "ah_last": None}
    if ext.empty or ref <= 0:
        return None, info
    last = float(ext["Close"].iloc[-1])
    vol = float(ext["Volume"].sum()) if "Volume" in ext else 0.0
    ah_ret = last / ref - 1.0
    info.update({"ah_ret": ah_ret, "ah_vol": vol, "ah_last": last})
    conf = clip01(safe_div(vol, th["ah_vol_conf"] * m["adv20"]))
    move = clip01(abs(ah_ret) / th["ah_move_full"])
    # even a thin-volume move gets partial credit; heavy tape gets full credit
    score = move * (0.35 + 0.65 * conf)
    return clip01(score), info


def catalyst_component(
    earn_date: dt.date | None, asof: dt.date, mode: str
) -> tuple[float | None, dict]:
    info = {"earnings_date": earn_date}
    if earn_date is None:
        return 0.0, info
    target_session = asof if mode == "premarket" else next_trading_day(asof)
    days = (earn_date - asof).days
    if earn_date == asof or earn_date == target_session:
        return 1.0, info          # reporting tonight or before tomorrow's bell
    if 0 < days <= 4:
        return 0.35, info         # event risk building this week
    return 0.0, info


def short_fuel_component(stats: dict | None, m: dict, th: dict) -> tuple[float | None, dict]:
    if not stats:
        return None, {"short_pct_float": None, "days_to_cover": None}
    spf = stats.get("short_pct_float")
    dtc = stats.get("days_to_cover")
    if spf is None and dtc is None:
        return None, {"short_pct_float": None, "days_to_cover": None}
    a = clip01(safe_div(spf or 0.0, th["short_pct_full"]))
    b = clip01(safe_div(dtc or 0.0, th["dtc_full"]))
    fuel = 0.7 * a + 0.3 * b
    # crowded shorts only ignite with a spark: fade the score in dead tape
    if m["ret5"] < 0 and m["rvol"] < 1.5:
        fuel *= 0.5
    return clip01(fuel), {"short_pct_float": spf, "days_to_cover": dtc}


def options_heat_component(iv: float | None, m: dict) -> tuple[float | None, dict]:
    if iv is None:
        return None, {"atm_iv": None, "vrp": None}
    rv = m.get("yz_vol")
    vrp = safe_div(iv, max(rv, 0.05), 1.0) if rv and np.isfinite(rv) else 1.0
    heat = 0.6 * clip((vrp - 1.1) / 1.5, 0.0, 1.0) + 0.4 * clip((iv - 0.6) / 1.2, 0.0, 1.0)
    return clip01(heat), {"atm_iv": iv, "vrp": vrp}
