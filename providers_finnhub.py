"""
IGNITION FLOW — intraday money-rotation engine.

The problem this solves: raw volume comparisons lie intraday. Volume is
U-shaped, so "5M shares by 10:00" and "5M shares by 14:00" mean wildly
different things. Every participation number here is normalized by the
name's OWN typical cumulative-volume curve for that minute of the session.

Definitions
-----------
PACE    cumulative regular-session volume ÷ (ADV20 × expected fraction of a
        day's volume traded by this minute). "On pace for PACE× a normal day."
TP      trailing-window pace: volume in the last W minutes ÷ (ADV20 × the
        fraction of a normal day that trades in THIS particular W-minute
        slot). This is the instantaneous participation reading — it is the
        thing that collapses when the money leaves, long before the chart
        looks broken.
SHARE   this name's fraction of total dollar volume across the monitored
        set in the trailing window. ΔSHARE against the prior window is the
        rotation signal: whose share the money is leaving, whose it enters.

States (priority order)
-----------------------
LEAVING    ran hard, trailing pace under ~55% of peak, VWAP lost after having
           been extended above it, price rolling. Money out, confirmed.
FADING     the retail trap: peak participation was real (≥2x), trailing pace
           has collapsed below fade_ratio × peak, but price still sits near
           HOD / the day is still green. Tape cooled, chart hasn't. The
           crowd walking in here is buying from the money walking out.
IGNITING   trailing pace ≥ ignite_tp AND accelerating vs the prior window
           AND price actually moving. Fresh aggression, right now.
NEW MONEY  was NOT an opening leader, trailing pace now ≥ 2x and rising,
           taking dollar-flow share. Where the invisible hand went.
CHURN      heavy participation, no price progress, pinned near HOD —
           absorption / distribution fight. Effort without result.
RUNNING    sustained elevated participation, trend intact.
COOLING    pace rolling off peak without the trap conditions.
QUIET      nothing to see.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .util import NY

GRID = 390  # minutes 09:30 → 16:00


# ---------------------------------------------------------------------------
# expected intraday cumulative-volume curves
# ---------------------------------------------------------------------------
def default_curve() -> np.ndarray:
    """Analytic U-shape: heavy open, midday trough, closing ramp."""
    m = np.arange(GRID)
    x = m / (GRID - 1)
    inten = 1.55 * np.exp(-x / 0.11) + 0.42 + 1.15 * np.exp(-(1 - x) / 0.075)
    cum = np.cumsum(inten)
    return cum / cum[-1]


def build_curves(hist: dict[str, pd.DataFrame]) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Per-name cumulative volume-fraction curves from recent 1m history."""
    curves: dict[str, np.ndarray] = {}
    for t, df in (hist or {}).items():
        try:
            idx = df.index
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_convert(NY)
            df = df.copy()
            df.index = idx
            days = []
            for _, day in df.groupby(idx.date):
                dd = day.between_time("09:30", "15:59")
                tot = float(dd["Volume"].sum())
                if len(dd) < 200 or tot <= 0:
                    continue
                mins = (dd.index.hour - 9) * 60 + dd.index.minute - 30
                arr = np.zeros(GRID)
                np.add.at(arr, np.clip(mins, 0, GRID - 1), dd["Volume"].to_numpy(float))
                days.append(np.cumsum(arr) / tot)
            if len(days) >= 3:
                curves[t] = np.mean(days, axis=0)
        except Exception:
            continue
    med = (np.median(np.stack(list(curves.values())), axis=0)
           if len(curves) >= 5 else default_curve())
    return curves, med


# ---------------------------------------------------------------------------
# per-tick snapshot
# ---------------------------------------------------------------------------
def _norm_idx(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.index
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize(NY)
    else:
        idx = idx.tz_convert(NY)
    out = df.copy()
    out.index = idx
    return out.sort_index()


def _fsafe(curve: np.ndarray, minute: int) -> float:
    return float(curve[int(np.clip(minute, 1, GRID)) - 1])


def new_state_store() -> dict:
    return {}


def snapshot(bars: dict[str, pd.DataFrame], adv: dict[str, float],
             prev_close: dict[str, float], curves: dict[str, np.ndarray],
             med_curve: np.ndarray, now: dt.datetime, fcfg: dict,
             store: dict) -> tuple[list[dict], list[dict]]:
    """One flow reading across the monitored set. Mutates `store` (per-name
    running memory: peak TP, opening pace, VWAP extension). Returns
    (rows, events); events are state transitions since the previous tick."""
    w = int(fcfg["window_min"])
    rows: list[dict] = []
    for t, raw in bars.items():
        a = adv.get(t)
        pc = prev_close.get(t)
        if raw is None or raw.empty or not a or not pc:
            continue
        df = _norm_idx(raw)
        df = df[df.index.date == now.date()]
        df = df[df.index <= now]
        if df.empty:
            continue
        reg = df.between_time("09:30", "15:59")
        pm = df.between_time("04:00", "09:29")
        last = float(df["Close"].iloc[-1])
        day_pct = last / pc - 1.0
        pm_dollar = float((pm["Volume"] * pm["Close"]).sum()) if len(pm) else 0.0
        minute = int((now.hour - 9) * 60 + now.minute - 30)
        curve = curves.get(t, med_curve)

        r = {"ticker": t, "last": last, "day_pct": day_pct, "pm_dollar": pm_dollar,
             "pm_frac": pm_dollar / max(a * pc, 1.0), "pace": None, "tp": None,
             "accel": None, "vs_vwap": None, "off_hod": None, "ret_w": None,
             "dollar_w": 0.0, "dollar_prev": 0.0, "share": 0.0, "dshare": 0.0,
             "state": "PRE-OPEN", "note": ""}

        if minute >= 2 and len(reg):
            v = reg["Volume"].to_numpy(float)
            c = reg["Close"].to_numpy(float)
            h = reg["High"].to_numpy(float)
            lo = reg["Low"].to_numpy(float)
            typ = (h + lo + c) / 3.0
            cumv = float(v.sum())
            r["pace"] = cumv / (a * max(_fsafe(curve, minute), 0.02))
            vwap = float((v * typ).sum() / max(cumv, 1.0))
            r["vs_vwap"] = last / vwap - 1.0
            hod = float(h.max())
            r["off_hod"] = last / hod - 1.0

            t_w = now - dt.timedelta(minutes=w)
            t_2w = now - dt.timedelta(minutes=2 * w)
            iw = reg.index.searchsorted(t_w)
            i2 = reg.index.searchsorted(t_2w)
            f_now, f_w, f_2w = (_fsafe(curve, minute),
                                _fsafe(curve, minute - w),
                                _fsafe(curve, minute - 2 * w))
            vol_w = float(v[iw:].sum())
            r["dollar_w"] = float((v[iw:] * c[iw:]).sum())
            r["dollar_prev"] = float((v[i2:iw] * c[i2:iw]).sum())
            r["tp"] = vol_w / (a * max(f_now - f_w, 0.004))
            r["warmup"] = minute < (2 * w + 2)
            if not r["warmup"]:
                tp_prev = float(v[i2:iw].sum()) / (a * max(f_w - f_2w, 0.004))
                r["accel"] = r["tp"] / max(tp_prev, 0.05)
            base_i = max(min(iw, len(c) - 1) - 1, 0)
            r["ret_w"] = last / float(c[base_i]) - 1.0
            rng_w = (float(h[iw:].max()) - float(lo[iw:].min())) / last if len(h[iw:]) else 0.0
            r["progress"] = abs(r["ret_w"]) / max(rng_w, 1e-4)

            st = store.setdefault(t, {"state": None, "peak_tp": 0.0,
                                      "tp_open": None, "vwap_ext": 0.0})
            st["peak_tp"] = max(st["peak_tp"], r["tp"])
            st["vwap_ext"] = max(st["vwap_ext"], r["vs_vwap"])
            if st["tp_open"] is None and minute >= 30:
                st["tp_open"] = cumv / (a * max(_fsafe(curve, min(minute, 35)), 0.02))
            r["peak_tp"] = st["peak_tp"]
        rows.append(r)

    # dollar-flow shares across the monitored set
    tot_base = sum(adv.get(x["ticker"], 0) * prev_close.get(x["ticker"], 0)
                   for x in rows) or 1.0
    tot_w = sum(x["dollar_w"] for x in rows) or 1.0
    tot_p = sum(x["dollar_prev"] for x in rows) or 1.0
    warm = any(x.get("warmup") is False for x in rows)
    for x in rows:
        x["share"] = x["dollar_w"] / tot_w
        x["dshare"] = (x["share"] - x["dollar_prev"] / tot_p) if warm else 0.0
        bshare = adv.get(x["ticker"], 0) * prev_close.get(x["ticker"], 0) / tot_base
        x["surge"] = (x["share"] / bshare) if (bshare > 0 and warm) else None

    events: list[dict] = []
    for x in rows:
        t = x["ticker"]
        st = store.get(t)
        state, note = _classify(x, st, fcfg)
        x["state"], x["note"] = state, note
        if st is not None and st["state"] not in (None, state):
            events.append({"ts": now, "ticker": t, "prev": st["state"],
                           "state": state, "price": x["last"],
                           "tp": x["tp"], "note": note})
        if st is not None:
            st["state"] = state
    rows.sort(key=lambda x: x["dollar_w"] * max(x["tp"] or 0.3, 0.3), reverse=True)
    return rows, events


def _classify(x: dict, st: dict | None, f: dict) -> tuple[str, str]:
    prev = st["state"] if st else None
    if x["pace"] is None or st is None:
        if x["pm_frac"] >= 0.015:
            return "PM HOT", f"pre-mkt ${x['pm_dollar']/1e6:.1f}M ≈ {x['pm_frac']:.1%} of ADV"
        return "PRE-OPEN", ""
    tp, pace, peak = x["tp"], x["pace"], st["peak_tp"]
    if x.get("warmup", True):
        if tp >= 3.0:
            return "OPEN DRIVE", f"{tp:.1f}x tape out of the gate"
        return "QUIET", ""

    if (peak >= f["fade_peak_tp"] and tp < 0.55 * peak and x["vs_vwap"] < 0
            and st["vwap_ext"] >= 0.015 and x["ret_w"] <= 0):
        return "LEAVING", (f"pace {tp:.1f}x off peak {peak:.1f}x · VWAP lost "
                           f"(was +{st['vwap_ext']:.1%} above)")
    if (peak >= f["fade_peak_tp"] and tp < f["fade_ratio"] * peak
            and (x["off_hod"] >= -f["hod_prox"] or x["day_pct"] >= 0.03)
            and x["day_pct"] > 0):
        return "FADING", (f"pace {tp:.1f}x vs peak {peak:.1f}x · "
                          f"{x['off_hod']:+.1%} off HOD · chart still "
                          f"{x['day_pct']:+.0%} — crowd buying a cooled tape")
    if x["accel"] is not None and tp >= f["ignite_tp"] and x["accel"] >= f["accel"] and abs(x["ret_w"]) >= 0.004:
        return "IGNITING", (f"pace {tp:.1f}x and accelerating "
                            f"({x['accel']:.1f}x vs prior window) · {x['ret_w']:+.1%} in window")
    if (tp >= f["newmoney_tp"] and st["tp_open"] is not None
            and st["tp_open"] < 1.3 and (x["accel"] or 0) >= 1.25 and x["dshare"] > 0):
        return "NEW MONEY", (f"quiet open ({(st['tp_open'] or 0):.1f}x) → now {tp:.1f}x · "
                             f"taking {x['dshare']:+.1%} flow share")
    if (tp >= f["churn_tp"] and x.get("progress", 1) < f["churn_progress"]
            and abs(x["ret_w"]) < 0.0025 and x["off_hod"] >= -0.02):
        return "CHURN", f"{tp:.1f}x participation, no progress at highs — absorption"
    if prev == "LEAVING" and tp < 0.75 * peak:
        return "LEAVING", f"still out — pace {tp:.1f}x vs peak {peak:.1f}x"
    if prev == "FADING" and tp < 0.6 * peak and x["day_pct"] > 0:
        return "FADING", (f"pace {tp:.1f}x vs peak {peak:.1f}x · "
                          f"chart still {x['day_pct']:+.0%}")
    if tp >= f["run_tp"]:
        return "RUNNING", f"sustained {tp:.1f}x"
    if peak >= 1.8 and tp < 0.75 * peak:
        return "COOLING", f"pace {tp:.1f}x off peak {peak:.1f}x"
    return "QUIET", ""


def rotation_lists(rows: list[dict], n: int = 4) -> tuple[list[dict], list[dict]]:
    live = [r for r in rows if r["tp"] is not None and r.get("surge") is not None]
    inn = [r for r in live if r["surge"] >= 1.6 and r["tp"] >= 1.5]
    inn.sort(key=lambda r: r["surge"] * min(r["accel"] or 1, 3), reverse=True)
    out = [r for r in live if r["peak_tp"] >= 2.0 and r["tp"] < 0.55 * r["peak_tp"]]
    out.sort(key=lambda r: r["tp"] / max(r["peak_tp"], 0.1))
    return inn[:n], out[:n]
