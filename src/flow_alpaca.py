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
import pandas as pd

from . import flow as flow_mod
from . import splits
from .providers_alpaca import AlpacaData
from .universe import ETF_EXCLUDE
from .util import NY

STATE_V = 4


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
    symbols = [s for s in ap.active_symbols() if s not in ETF_EXCLUDE]
    if len(symbols) < 500:
        log(f"alpaca: assets endpoint returned {len(symbols)} symbols — check keys")
        return None
    daily = ap.daily(symbols, days=25)
    today_d = dt.datetime.now(NY).date()
    # split-aware ADV (item 30): a reverse split inside the window makes a
    # raw share-volume mean overstate ADV by the split factor and the name
    # goes invisible to pace/rvol. Authoritative ex-dates when the CA
    # endpoint answers; clean-ratio heuristic when it doesn't.
    ca_splits = ap.splits_range(
        (today_d - dt.timedelta(days=45)).isoformat(), today_d.isoformat())
    adv, prev_close = {}, {}
    for t, df in daily.items():
        hist = df[df.index.date < today_d]
        if len(hist) < 10:
            continue
        v = splits.split_aware_adv(
            hist["Close"].to_numpy(float), hist["Volume"].to_numpy(float),
            n=20, dates=[d.isoformat() for d in hist.index.date],
            known=(ca_splits or {}).get(t))
        if v:
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
            hod = float(day.get("h") or 0)
            lod = float(day.get("l") or 0)
            rngp = ((hod - lod) / px) if (hod and lod and px) else None
            rows.append({"ticker": sym, "pace": pace, "last": px,
                         "day_pct": px / pc - 1.0, "dollar_day": v * px,
                         "vs_adv": v / adv, "range_pct": rngp,
                         "open": float(day.get("o") or 0) or None,
                         "ssr": bool(lod and lod <= 0.9 * pc),
                         "off_hi": (px / hod - 1.0) if hod else None})
        except Exception:
            continue
    rows.sort(key=lambda r: r["pace"] * min(np.log10(max(r["dollar_day"], 10)), 8),
              reverse=True)
    return rows


def movers_from_radar(radar_rows: list[dict], fcfg: dict,
                      promoted: set[str]) -> list[dict]:
    """Today's REAL tape: names that actually moved, ranked by size of move.
    A 0.3% wiggle on an index fund cannot appear here by construction."""
    mv = [r for r in radar_rows
          if abs(r["day_pct"]) >= float(fcfg.get("mover_min_move", 0.15))
          and r["dollar_day"] >= float(fcfg.get("mover_min_dollar", 75000))
          and r["pace"] >= float(fcfg.get("mover_min_pace", 1.3))]
    mv.sort(key=lambda r: abs(r["day_pct"]), reverse=True)
    mv = mv[: int(fcfg.get("mover_top", 15))]
    for r in mv:
        rp, mvv = r.get("range_pct"), abs(r["day_pct"])
        r["pin"] = bool(rp and mvv >= 0.20 and (rp / mvv) <= 0.25)
        r["promoted"] = r["ticker"] in promoted
    return mv


def dump_movers_state(sdir: str, now: dt.datetime, movers: list[dict]):
    _save(os.path.join(sdir, "latest_movers.json"),
          {"v": STATE_V, "ts": now.isoformat(timespec="seconds"),
           "rows": [{"ticker": r["ticker"], "day_pct": round(r["day_pct"], 4),
                     "last": round(r["last"], 3), "pace": round(r["pace"], 2),
                     "dollar_day": r["dollar_day"],
                     "promoted": bool(r.get("promoted"))} for r in movers]})


def promotions(radar_rows: list[dict], monitor: set[str], sdir: str,
               cap_new: int = 40, force: list[str] | None = None
               ) -> tuple[list[str], set[str]]:
    """Names the radar says deserve the fine-grained engine, persisted all day."""
    today = dt.datetime.now(NY).date().isoformat()
    path = os.path.join(sdir, f"radar_promoted_{today}.json")
    kept = set(_load(path) or [])
    fresh = [t for t in (force or []) if t not in kept and t not in monitor]
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
    movers_pre = movers_from_radar(radar_rows, fcfg, set())
    fresh, kept = promotions(radar_rows, set(monitor), sdir,
                             force=[r["ticker"] for r in movers_pre])
    watch = list(dict.fromkeys(monitor + sorted(kept)))[: fcfg["monitor_cap"] + 40]
    bars = ap.minute_today(watch)
    promoted = set(kept)
    for r in radar_rows:
        r["promoted"] = r["ticker"] in promoted or r["ticker"] in monitor
    movers = movers_from_radar(radar_rows, fcfg, promoted | set(monitor))
    return radar_rows[:60], movers, fresh, watch, bars


def dump_radar_state(sdir: str, now: dt.datetime, radar_rows: list[dict]):
    _save(os.path.join(sdir, "latest_radar.json"),
          {"v": STATE_V, "ts": now.isoformat(timespec="seconds"),
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
              fcfg: dict, session: str, log) -> list[dict]:  # noqa: C901
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
        hi = float(df["High"].max()) if len(df) else c["last"]
        t15 = df[df.index >= df.index.max() - pd.Timedelta(minutes=15)]
        d15 = float((t15["Volume"] * t15["Close"]).sum())
        elapsed = max((df.index.max() - df.index.min()).total_seconds() / 60, 15)
        l15 = d15 / max(dollars, 1.0)
        pstats = path_stats(df, now) or {}
        rows.append({**c, **pstats, "gap": vgap, "dollars": dollars, "shares": shares,
                     "minutes": minutes, "off_hi": c["last"] / hi - 1.0,
                     "hot": l15 >= min(0.9, 2.0 * 15 / elapsed),
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
          {"v": STATE_V, "ts": now.isoformat(timespec="seconds"), "session": session,
           "rows": [{"ticker": r["ticker"], "last": round(r["last"], 3),
                     "gap": round(r["gap"], 4),
                     "dollars": r["dollars"],
                     "vs_adv": (round(r["vs_adv"], 2) if r["vs_adv"] else None),
                     "new": r["ticker"] in fresh} for r in rows]})
    return fresh


def load_ignitions(sdir: str, day: dt.date) -> list[str]:
    return _load(os.path.join(sdir, f"ignitions_{day.isoformat()}.json")) or []


# ---------------------------------------------------------------------------
# THE BOARD — the one list: session-aware, first-seen-stamped, state-joined
# ---------------------------------------------------------------------------
def assemble_board(sdir: str, now: dt.datetime, session: str,
                   rows: list[dict], states: dict[str, dict] | None = None
                   ) -> dict:
    """Every row: move, real tape, freshness, alive-or-dead. `rows` come from
    ext_sweep (pre/post: r['gap']) or movers (rth: r['day_pct'])."""
    seen_path = os.path.join(sdir, f"board_seen_{now.date().isoformat()}.json")
    seen = _load(seen_path) or {}
    hm = now.strftime("%H:%M")
    out = []
    for r in rows:
        tk = r["ticker"]
        first = seen.get(tk)
        if first is None:
            seen[tk] = first = hm
        st = (states or {}).get(tk) or {}
        out.append({
            "headline": r.get("headline"), "pr_ts": r.get("pr_ts"),
            "catalyst": bool(r.get("catalyst")), "flags": r.get("flags"),
            "ticker": tk,
            "move": r.get("gap", r.get("day_pct", 0.0)),
            "last": r["last"],
            "dollars": r.get("dollars", r.get("dollar_day", 0.0)),
            "vs_adv": r.get("vs_adv"),
            "off_hi": r.get("off_hi"),
            "state": st.get("state") or r.get("state"),
            "open": r.get("open"), "ssr": bool(r.get("ssr")),
            "vs_vwap": st.get("vs_vwap", r.get("vs_vwap")),
            "tp": st.get("tp"),
            "hot": bool(r.get("hot") or (st.get("tp") or 0) >= 2.5),
            "pin": bool(r.get("pin")),
            "heat": r.get("heat"),
            "swings": r.get("swings"),
            "path": r.get("path"),
            "first_seen": first,
            "new": first == hm,
        })
    out.sort(key=lambda r: ((r.get("heat") or 0.0), abs(r["move"])), reverse=True)
    _save(seen_path, seen)
    board = {"v": STATE_V, "ts": now.isoformat(timespec="seconds"),
             "session": session,
             "rows": [{**r, "move": round(r["move"], 4),
                       "last": round(r["last"], 3),
                       "vs_adv": round(r["vs_adv"], 2) if r["vs_adv"] else None,
                       "off_hi": round(r["off_hi"], 4) if r["off_hi"] is not None else None,
                       "tp": round(r["tp"], 1) if r.get("tp") else None,
                       "heat": round(r["heat"], 1) if r.get("heat") is not None else None,
                       "path": round(r["path"], 3) if r.get("path") is not None else None,
                       "headline": r.get("headline"), "pr_ts": r.get("pr_ts"),
                       "catalyst": bool(r.get("catalyst")), "flags": r.get("flags")}
                      for r in out]}
    _save(os.path.join(sdir, "latest_board.json"), board)
    return board


# ---------------------------------------------------------------------------
# TRADABILITY — ranks what a human can actually trade, not what printed
# ---------------------------------------------------------------------------
def path_stats(df, now: dt.datetime, swing_th: float = 0.07) -> dict | None:
    """From 1-minute bars: how much did this thing actually TRAVEL, in how
    many swings, and is it still moving NOW.

    path     Σ|1-min returns| — total intraday travel
    trad     path minus the single largest 1-min print. A buyout gap is one
             minute nobody human caught; subtracting it zeroes UTZ-shapes
             while barely denting a real runner's travel.
    swings   zigzag reversals ≥ swing_th — the legs, pullbacks, reclaims
    recent   travel in the last 30 minutes
    heat     0-100: saturating in trad, scaled hard by recency — a name that
             died at 11:30 cools; a name whipping right now burns.
    """
    try:
        if df is None or len(df) < 5:
            return None
        d = df[df.index <= now]
        c = d["Close"].to_numpy(float)
        if len(c) < 5:
            return None
        raw = np.abs(np.diff(c) / c[:-1])
        r = np.where(raw >= 0.0035, raw, 0.0)   # sub-spread jitter is not travel
        path = float(r.sum())
        trad = float(max(path - r.max(), 0.0))
        # zigzag: exclusive pivot tracking, direction seeded by first real leg
        swings, piv, direction = 0, c[0], 0
        for px in c[1:]:
            if direction == 0:
                if px / piv - 1.0 >= swing_th:
                    direction, piv = 1, px
                elif px / piv - 1.0 <= -swing_th:
                    direction, piv = -1, px
                continue
            if direction > 0:
                if px > piv:
                    piv = px
                elif px / piv - 1.0 <= -swing_th:
                    swings, piv, direction = swings + 1, px, -1
            else:
                if px < piv:
                    piv = px
                elif px / piv - 1.0 >= swing_th:
                    swings, piv, direction = swings + 1, px, 1
        i30 = d.index.searchsorted(now - dt.timedelta(minutes=30))
        recent = float(r[max(i30 - 1, 0):].sum()) if len(r) else 0.0
        recency = min(recent / 0.08, 1.0)          # 8% travel in 30m = fully hot
        heat = 100.0 * (1.0 - np.exp(-trad / 0.5)) * (0.25 + 0.75 * recency)
        return {"path": path, "trad": trad, "swings": swings,
                "recent": recent, "heat": round(heat, 1)}
    except Exception:
        return None


def attach_heat(rows: list[dict], bars: dict, now: dt.datetime) -> None:
    for r in rows:
        st = path_stats(bars.get(r["ticker"]), now)
        if st:
            r.update(st)
        r.setdefault("heat", abs(r.get("gap", r.get("day_pct", 0.0))) * 8)
        if r.get("pin"):
            r["heat"] = min(r["heat"], 2.0)        # dead money sinks


def fast_update(ap: AlpacaData, base: dict, sdir: str, now: dt.datetime,
                fcfg: dict, news_events: list[dict]) -> bool:
    """The 45-second lane: refresh ONLY the hot set (current board + fresh
    catalyst names) — two API calls — and re-rank. Catalyst rows land with a
    heat floor so a PR surfaces before its volume has confirmed."""
    board = _load(os.path.join(sdir, "latest_board.json")) or {}
    prev = {r["ticker"]: dict(r) for r in board.get("rows", [])}
    tickers = list(prev)
    for e in news_events:
        if e["symbol"] not in tickers:
            tickers.append(e["symbol"])
    tickers = tickers[:50]
    if not tickers:
        return False
    snaps = ap.snapshots(tickers)
    bars = ap.minute_today(tickers)
    news_by = {}
    for e in news_events:
        news_by.setdefault(e["symbol"], e)
    rows = []
    for t in tickers:
        s = snaps.get(t) or {}
        lt = s.get("latestTrade") or {}
        day = s.get("dailyBar") or {}
        px = float(lt.get("p") or 0) or float(prev.get(t, {}).get("last") or 0)
        pc = base["prev_close"].get(t)
        if not px or not pc:
            continue
        r = dict(prev.get(t) or {})
        r.update({"ticker": t, "last": px, "day_pct": px / pc - 1.0,
                  "dollar_day": float(day.get("v") or 0) * px
                  or r.get("dollars", 0.0)})
        st = path_stats(bars.get(t), now)
        if st:
            r.update(st)
        ne = news_by.get(t)
        if ne:
            r["headline"] = ne["headline"]
            r["pr_ts"] = ne["ts"].strftime("%H:%M")
            r["catalyst"] = True
            r["flags"] = ne["flags"]
            r["heat"] = max(float(r.get("heat") or 0), 55.0)
        rows.append(r)
    if not rows:
        return False
    assemble_board(sdir, now, board.get("session") or "rth", rows,
                   states=None)
    refresh_pulse_from_board(sdir, now)
    return True


def refresh_pulse_from_board(sdir: str, now: dt.datetime):
    """45-second lane: splice the just-refreshed board rows into the last
    pulse dump and bump its timestamp, so the phone's hot-or-not answer for
    tracked names is never older than the fast tick. Full-market rows keep
    their last full-tick values — refreshing 11k names every 45s would be
    two more API calls for nothing."""
    p = _load(os.path.join(sdir, "latest_pulse.json"))
    board = _load(os.path.join(sdir, "latest_board.json"))
    if not p or p.get("v") != STATE_V or not board:
        return
    idx = {r[0]: i for i, r in enumerate(p["rows"])}
    for b in board.get("rows", []):
        row = [b["ticker"], round(float(b.get("last") or 0), 3),
               round(float(b.get("move") or 0), 3), int(b.get("dollars") or 0),
               None, None, b.get("off_hi"), b.get("heat"), b.get("swings"),
               b.get("state"), b.get("first_seen")]
        i = idx.get(b["ticker"])
        if i is None:
            p["rows"].append(row)
        else:                              # keep radar pace/range from full tick
            old = p["rows"][i]
            row[4], row[5] = old[4], old[5]
            p["rows"][i] = row
    p["ts"] = now.isoformat(timespec="seconds")
    _save(os.path.join(sdir, "latest_pulse.json"), p)


# ---------------------------------------------------------------------------
# full-market candidate sourcing for the nightly SCAN
# ---------------------------------------------------------------------------
def dump_pulse_state(sdir: str, now: dt.datetime, radar_rows: list[dict],
                     board: dict | None, pcfg: dict | None = None,
                     watch_rows: list[dict] | None = None):
    """PULSE — hot-or-not for ANY ticker, not just board winners. The user
    holds names the board didn't pick (he traded UTX the day it swung, months
    after it was a ghost-liquidity reject): whether a name is hot is a
    property of TODAY'S tape, not of the ticker. So pulse carries every
    symbol with meaningful tape this session — no price class, no ADV band —
    overlaid with heat/state where the engine already tracks the name. The
    hub copies it into docs/ and answers lookups client-side."""
    p = pcfg or {}
    min_d = float(p.get("min_dollar", 25_000))
    min_mv = float(p.get("min_move", 0.05))
    any_d = float(p.get("always_dollar", 100_000))
    any_pc = float(p.get("always_pace", 2.0))
    cap = int(p.get("cap", 3000))
    over = {r["ticker"]: r for r in (board or {}).get("rows", [])}
    wl = {r["ticker"]: r for r in (watch_rows or [])}
    rows, seen = [], set()
    for r in radar_rows:                      # already sorted hottest-first
        t = r["ticker"]
        if not (r["dollar_day"] >= any_d or r["pace"] >= any_pc
                or (abs(r["day_pct"]) >= min_mv and r["dollar_day"] >= min_d)
                or t in over or t in wl):     # watchlist: zero admission gates
            continue
        b = over.get(t) or {}
        rows.append([
            t, round(r["last"], 3), round(r["day_pct"], 3),
            int(r["dollar_day"]), round(r["pace"], 1),
            round(r["range_pct"], 3) if r.get("range_pct") is not None else None,
            round(r["off_hi"], 3) if r.get("off_hi") is not None else None,
            round(b["heat"], 1) if b.get("heat") is not None else None,
            b.get("swings"), b.get("state"), b.get("first_seen")])
        seen.add(t)
        if len(rows) >= cap:
            break
    for t, b in over.items():                 # board names are always present
        if t in seen:
            continue
        rows.append([t, round(float(b.get("last") or 0), 3),
                     round(float(b.get("move") or 0), 3),
                     int(b.get("dollars") or 0), None, None, b.get("off_hi"),
                     b.get("heat"), b.get("swings"), b.get("state"),
                     b.get("first_seen")])
        seen.add(t)
    for t, w in wl.items():                   # watchlist names too — always
        if t in seen:
            continue
        rows.append([t, round(float(w.get("last") or 0), 3),
                     round(float(w.get("day_pct") or 0), 3),
                     int(w.get("dollars") or 0), None, None, w.get("off_hi"),
                     w.get("heat"), w.get("swings"), w.get("state"), None])
    _save(os.path.join(sdir, "latest_pulse.json"),
          {"v": STATE_V, "ts": now.isoformat(timespec="seconds"),
           "cols": ["t", "last", "d", "$", "pace", "rng", "offh",
                    "heat", "sw", "st", "fs"],
           "rows": rows})


def full_market_candidates(ap: AlpacaData, ucfg: dict, log, cap: int = 400
                           ) -> list[str]:
    """Stage 1 of the scan funnel: screen EVERY US listing from 30 days of
    daily bars, keep only names inside the tradable band (price and dollar
    ADV floors AND ceilings), then pre-rank by relative volume and range so
    the expensive 130-day pass only touches names worth the calls.

    This replaces a hand-written ~200-name seed list that could never have
    contained a CPHI-class ticker — the reason the nightly scan kept
    returning mega-caps.
    """
    symbols = [s for s in ap.active_symbols() if s not in ETF_EXCLUDE]
    if len(symbols) < 500:
        return []
    log(f"full-market scan: screening {len(symbols)} listings…")
    daily = ap.daily(symbols, days=30)
    lo_p = float(ucfg.get("min_price", 0.10))
    hi_p = float(ucfg.get("max_price") or 1e9)
    lo_d = float(ucfg.get("min_dollar_volume", 200_000))
    hi_d = float(ucfg.get("max_dollar_adv") or 1e15)
    out = []
    for t, df in daily.items():
        try:
            if len(df) < 21:
                continue
            c = df["Close"].to_numpy(float)
            # split-aware shares (item 30): mixed pre/post-split units make
            # rvol read ~0.1x after a 1:10 reverse — the INLF failure class
            v = splits.adjusted_volumes(c, df["Volume"].to_numpy(float))
            close = float(c[-1])
            adv = float(v[-21:-1].mean())
            if adv <= 0:
                continue
            dadv = adv * close
            if not (lo_p <= close <= hi_p) or not (lo_d <= dadv <= hi_d):
                continue
            rvol = float(v[-1]) / adv
            ret1 = close / float(c[-2]) - 1.0 if len(c) > 1 else 0.0
            rng = float(np.abs(np.diff(c[-6:]) / c[-6:-1]).mean()) if len(c) > 6 else 0.0
            pre = (np.log1p(max(rvol, 0.01)) * 1.6 + abs(ret1) * 3.0
                   + rng * 4.0)
            out.append((pre, t))
        except Exception:
            continue
    out.sort(reverse=True)
    log(f"full-market scan: {len(out)} in the tradable band → top {cap}")
    return [t for _, t in out[:cap]]
