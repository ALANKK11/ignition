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
            if not adv or not pc or v <= 0 or px < float(fcfg.get("ext_min_price", 0.10)):
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


# ---------------------------------------------------------------------------
# extended-hours ignition sweep (pre-market / after-hours, full market)
# ---------------------------------------------------------------------------
def _sess_start(now: dt.datetime, session: str) -> dt.datetime:
    t = dt.time(4, 0) if session == "pre" else dt.time(16, 0)
    return dt.datetime.combine(now.date(), t, tzinfo=NY)


def ext_sweep(ap: AlpacaData, base: dict, now: dt.datetime, sdir: str,
              fcfg: dict, session: str, log) -> list[dict]:
    """Full-market extended-hours sweep, hardened against live-tape pathology:

    AUCTION GUARD   the AH window starts 16:01 — Alpaca's 16:00 bar contains
                    the closing auction, which is regular-session volume and
                    once inflated every name's "after-hours tape" past any
                    dollar floor.
    PRINT ≠ PRICE   the gap is measured from the ext session's dollar-weighted
                    VWAP, never a single latestTrade print; one odd lot far
                    from NBBO is a quote artifact, not a move.
    BREADTH         a real move trades in many minutes: require distinct
                    traded minutes + shares + dollars, all of it doubled for
                    ghost names whose reference price is itself IEX noise.
    PARTICIPATION   ext shares must also clear a fraction of the name's own
                    ADV — a mega-cap "crashing" 15% on 0.1% of its normal
                    volume is corporate-action arithmetic, not selling.
    SPLIT GUARD     names with a split effective today are excluded via the
                    corporate-actions endpoint; if that call fails, a clean
                    split-ratio gap on shallow tape is excluded heuristically.
    """
    vstart = _sess_start(now, session)
    if session == "post":
        vstart = vstart + dt.timedelta(minutes=1)          # drop auction bar
    vend = dt.datetime.combine(now.date(), dt.time(9, 30), tzinfo=NY) \
        if session == "pre" else dt.datetime.combine(now.date(), dt.time(20, 0),
                                                     tzinfo=NY)
    snaps = ap.snapshots(base["symbols"])
    ca = ap.corporate_actions_today()
    splits = set((ca or {}).get("splits") or [])
    divs = (ca or {}).get("dividends") or {}
    gap_min = float(fcfg.get("ext_gap_min", 0.15))
    px_min = float(fcfg.get("ext_min_price", 0.10))
    cands = []
    for sym, s in snaps.items():
        try:
            if sym in splits:
                continue
            lt = s.get("latestTrade") or {}
            px = float(lt.get("p") or 0)
            ts = str(lt.get("t") or "")
            if px < px_min or not ts:
                continue
            tt = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(NY)
            if tt < vstart:                    # not traded in THIS ext window
                continue
            if session == "pre":
                ref = base["prev_close"].get(sym)
            else:
                ref = float((s.get("dailyBar") or {}).get("c") or 0) or \
                    base["prev_close"].get(sym)
            if not ref:
                continue
            ref -= float(divs.get(sym) or 0)   # strip today's cash dividend
            if ref <= 0:
                continue
            gap = px / ref - 1.0
            if abs(gap) >= gap_min:
                cands.append({"ticker": sym, "last": px, "gap": gap, "ref": ref})
        except Exception:
            continue
    cands.sort(key=lambda r: abs(r["gap"]), reverse=True)
    cands = cands[:400]
    if not cands:
        return []
    bars = ap.bars([c["ticker"] for c in cands], "1Min", vstart.isoformat())
    dol_min = float(fcfg.get("ext_min_dollar", 50000))
    sh_min = float(fcfg.get("ext_min_shares", 2500))
    mn_min = int(fcfg.get("ext_min_minutes", 5))
    frac_min = float(fcfg.get("ext_min_frac_adv", 0.005))
    ghost_dollar = float(fcfg.get("ext_ghost_adv_dollar", 10000))
    rows, culled = [], {"tape": 0, "vwap": 0, "frac": 0, "split?": 0}
    for c in cands:
        df = bars.get(c["ticker"])
        if df is None or not len(df):
            culled["tape"] += 1
            continue
        df = df[(df.index >= vstart) & (df.index < vend)]
        shares = float(df["Volume"].sum())
        dollars = float((df["Volume"] * df["Close"]).sum())
        minutes = int((df["Volume"] > 0).sum())
        adv = base["adv"].get(c["ticker"], 0) or 0
        adv_dollar = adv * c["ref"]
        mult = 2.0 if adv_dollar < ghost_dollar else 1.0     # ghosts prove more
        if (dollars < dol_min * mult or shares < sh_min * mult
                or minutes < mn_min * mult):
            culled["tape"] += 1
            continue
        vwap = dollars / max(shares, 1.0)
        vgap = vwap / c["ref"] - 1.0
        if abs(vgap) < gap_min * 0.6 or (vgap > 0) != (c["gap"] > 0):
            culled["vwap"] += 1                # a print the tape didn't confirm
            continue
        if adv > 0 and shares < frac_min * adv:
            culled["frac"] += 1                # gap without participation
            continue
        if ca is None and _looks_like_split(vgap):
            culled["split?"] += 1          # CA feed down: clean-ratio gap =
            continue                       # split until proven otherwise
        rows.append({**c, "gap": vgap, "dollars": dollars, "shares": shares,
                     "minutes": minutes,
                     "vs_adv": (shares / adv) if adv else None,
                     "heat": abs(vgap) * np.log10(1 + dollars / 2e4)})
    rows.sort(key=lambda r: r["heat"], reverse=True)
    log(f"ext[{session}]: {len(cands)} raw gappers → {len(rows)} confirmed "
        f"(culled: thin-tape {culled['tape']}, vwap-disagree {culled['vwap']}, "
        f"no-participation {culled['frac']}, split-like {culled['split?']})")
    return rows[: int(fcfg.get("ext_top", 20))]


_SPLIT_RATIOS = [2, 3, 4, 5, 8, 10, 15, 20, 25, 40, 50, 100]


def _looks_like_split(gap: float) -> bool:
    x = 1.0 + gap
    for r in _SPLIT_RATIOS:
        if abs(x - r) / r < 0.02 or abs(x - 1.0 / r) * r < 0.02:
            return True
    return False


def record_ignitions(rows: list[dict], sdir: str, now: dt.datetime,
                     session: str, journal=None, demo: bool = False) -> list[str]:
    """Persist today's ignition set (feeds the scan universe + promotions)
    and journal only NEW entrants so the hub's transition log reads clean."""
    today = now.date().isoformat()
    path = os.path.join(sdir, f"ignitions_{today}.json")
    known = set(_load(path) or [])
    fresh = [r["ticker"] for r in rows if r["ticker"] not in known]
    known |= set(fresh)
    _save(path, sorted(known))
    if journal is not None:
        label = "PM IGNITION" if session == "pre" else "AH IGNITION"
        for r in rows:
            if r["ticker"] in fresh:
                journal.record_flow_event(demo, {
                    "ts": now, "ticker": r["ticker"], "prev": None,
                    "state": label, "price": r["last"], "tp": None,
                    "note": f"{r['gap']:+.0%} on ${r['dollars'] / 1e6:.2f}M ext tape"})
    _save(os.path.join(sdir, "latest_ext.json"),
          {"ts": now.isoformat(timespec="seconds"), "session": session,
           "rows": [{"ticker": r["ticker"], "last": round(r["last"], 3),
                     "gap": round(r["gap"], 4),
                     "dollars": r["dollars"],
                     "vs_adv": (round(r["vs_adv"], 2) if r["vs_adv"] else None),
                     "new": r["ticker"] in fresh} for r in rows]})
    return fresh


def load_ignitions(sdir: str, day: dt.date) -> list[str]:
    return _load(os.path.join(sdir, f"ignitions_{day.isoformat()}.json")) or []
