"""
Full-market radar on Alpaca (free IEX feed).

Two tiers, by design:
  RADAR   one pass over ~10k active US listings via chunked snapshot calls
          (~25 requests). For each: cumulative IEX day volume vs its IEX ADV,
          time-of-day adjusted → market-wide PACE. This finds abnormal
          participation ANYWHERE, including names nobody put on a list.
  ENGINE  the existing per-minute state machine (src/flow.py) on a monitor
          set = last scan's picks + watchlist + names the radar promotes.
          Promotions persist for the rest of the day — once the money shows
          up somewhere, that somewhere stays watched.

All ratios are IEX-over-IEX (see providers_alpaca docstring) so the ~3%
sample cancels out of every number shown.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np

from . import flow as flow_mod
from .providers_alpaca import AlpacaData
from .util import NY


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _save(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def prepare(ap: AlpacaData, sdir: str, seed_monitor: list[str],
            fcfg: dict, log) -> dict | None:
    """Build (or load) the day's baselines: full-market symbol list, IEX ADV +
    prev close for every symbol, median intraday curve, per-name curves for
    the seed monitor set. Cached to one file — costs ~15 API calls once/day."""
    today = dt.datetime.now(NY).date().isoformat()
    path = os.path.join(sdir, f"alpaca_base_{today}.json")
    base = _load(path)
    if base:
        base["med"] = np.array(base["med"])
        base["curves"] = {t: np.array(c) for t, c in base["curves"].items()}
        return base
    log("alpaca: building full-market baselines (once per day)…")
    symbols = ap.active_symbols()
    if len(symbols) < 500:
        log(f"alpaca: assets endpoint returned {len(symbols)} symbols — check keys")
        return None
    daily = ap.daily(symbols, days=25)
    adv, prev_close = {}, {}
    today_d = dt.datetime.now(NY).date()
    for t, df in daily.items():
        hist = df[df.index.date < today_d]
        if len(hist) < 10:
            continue
        v = float(hist["Volume"].iloc[-20:].mean())
        if v > 0:
            adv[t] = v
            prev_close[t] = float(hist["Close"].iloc[-1])
    sample = sorted(adv, key=lambda t: adv[t] * prev_close[t], reverse=True)[:250]
    curve_src = ap.minute_recent(list(set(sample) | set(seed_monitor)), days=5)
    curves_all, med = flow_mod.build_curves(curve_src)
    curves = {t: c for t, c in curves_all.items() if t in seed_monitor}
    base = {"symbols": [s for s in symbols if s in adv], "adv": adv,
            "prev_close": prev_close, "med": med, "curves": curves}
    _save(path, {**base, "med": list(map(float, med)),
                 "curves": {t: list(map(float, c)) for t, c in curves.items()}})
    log(f"alpaca: baselines for {len(adv)} symbols · curves for {len(curves)} monitors")
    return base


def radar_scan(ap: AlpacaData, base: dict, now: dt.datetime,
               fcfg: dict) -> list[dict]:
    """One full-market participation read from chunked snapshots."""
    minute = max((now.hour - 9) * 60 + now.minute - 30, 1)
    f_now = max(float(base["med"][min(minute, flow_mod.GRID) - 1]), 0.03)
    snaps = ap.snapshots(base["symbols"])
    rows = []
    for sym, s in snaps.items():
        try:
            adv = base["adv"].get(sym)
            pc = base["prev_close"].get(sym)
            day = s.get("dailyBar") or {}
            trade = s.get("latestTrade") or {}
            v = float(day.get("v") or 0)
            px = float(trade.get("p") or day.get("c") or 0)
            if not adv or not pc or v <= 0 or px < 1.0:
                continue
            pace = v / (adv * f_now)
            rows.append({"ticker": sym, "pace": pace, "last": px,
                         "day_pct": px / pc - 1.0,
                         "dollar_day": v * px})
        except Exception:
            continue
    rows.sort(key=lambda r: r["pace"] * min(np.log10(max(r["dollar_day"], 10)), 8),
              reverse=True)
    return rows


def promotions(radar_rows: list[dict], monitor: set[str], sdir: str,
               cap_new: int = 40) -> tuple[list[str], set[str]]:
    """Names the radar says deserve the fine-grained engine, persisted all day."""
    today = dt.datetime.now(NY).date().isoformat()
    path = os.path.join(sdir, f"radar_promoted_{today}.json")
    kept = set(_load(path) or [])
    fresh = []
    for r in radar_rows:
        if len(kept) + len(fresh) >= cap_new + len(kept):
            break
        if (r["pace"] >= 3.0 and r["dollar_day"] >= 1.5e5
                and r["ticker"] not in monitor and r["ticker"] not in kept):
            fresh.append(r["ticker"])
        if len(fresh) >= 12:                    # per-run promotion budget
            break
    kept |= set(fresh)
    _save(path, sorted(kept))
    return fresh, kept


def fetch_tick(ap: AlpacaData, base: dict, monitor: list[str],
               now: dt.datetime, sdir: str, fcfg: dict):
    """Everything one flow refresh needs: radar rows, updated monitor set,
    and 1-minute bars for that monitor set."""
    radar_rows = radar_scan(ap, base, now, fcfg)
    fresh, kept = promotions(radar_rows, set(monitor), sdir)
    watch = list(dict.fromkeys(monitor + sorted(kept)))[: fcfg["monitor_cap"] + 40]
    bars = ap.minute_today(watch)
    promoted = set(kept)
    for r in radar_rows:
        r["promoted"] = r["ticker"] in promoted or r["ticker"] in monitor
    return radar_rows[:60], fresh, watch, bars


def dump_radar_state(sdir: str, now: dt.datetime, radar_rows: list[dict]):
    _save(os.path.join(sdir, "latest_radar.json"),
          {"ts": now.isoformat(timespec="seconds"),
           "rows": [{"ticker": r["ticker"], "pace": round(r["pace"], 2),
                     "last": round(r["last"], 2),
                     "day_pct": round(r["day_pct"], 4),
                     "dollar_day": r["dollar_day"],
                     "promoted": bool(r.get("promoted"))}
                    for r in radar_rows[:16]]})
