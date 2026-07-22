"""WATCH — the watchlist-first lane (HANDOFF item 31).

His decision, 2026-07-22: discovery is secondary; the product re-centers on
tickers HE enters. `watchlist.txt` is the spine. Every name on it gets full
telemetry in every lane, every tick, with ZERO admission gates — no ADV
baseline, no mover threshold, no pulse dollar floor, no price band. A name
that printed one lot today still renders a complete card; a name with no
tape renders a card that says exactly why. (This also moots the INLF
failure class for his names — gates can't hide what isn't gated.)

The lane costs two API calls per tick (snapshots + minute bars for a
handful of symbols) and never blocks the shift: every fetch is
exception-safe, and missing data degrades to an honest reason line, never
to an absent card.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

from .flow_alpaca import (STATE_V, mood_candidate, now_stats, path_stats,
                          sticky_mood)
from .util import NY

_TOK = re.compile(r"^[A-Z0-9.\-]{1,6}$")

WATCHLIST_HEADER = """\
# IGNITION watchlist — THE spine of the system. One ticker per line (or
# comma/space separated). Every name here gets a MY NAMES card with full
# telemetry every tick, no admission gates. Edit here, or from your phone:
# Actions -> live shift -> Run workflow -> tickers.
"""


def parse_tickers(raw: str) -> list[str]:
    """Sanitize free-form ticker input (dispatch box, file) — uppercase,
    deduped, order preserved."""
    out: list[str] = []
    for line in (raw or "").splitlines():
        line = line.split("#", 1)[0]
        for tok in line.replace(",", " ").upper().split():
            if _TOK.match(tok) and tok not in out:
                out.append(tok)
    return out


def load_watchlist(cfg: dict) -> list[str]:
    p = Path(cfg["_paths"]["root"]) / cfg["universe"]["watchlist_file"]
    try:
        return parse_tickers(p.read_text())
    except Exception:
        return []


def write_watchlist(path: str | Path, raw: str) -> list[str]:
    """The workflow_dispatch `tickers` input lands here: overwrite
    watchlist.txt for the day. Returns the sanitized list actually written."""
    tickers = parse_tickers(raw)
    Path(path).write_text(WATCHLIST_HEADER + "\n".join(tickers) + "\n")
    return tickers


# ---------------------------------------------------------------------------
# row assembly — zero gates, honest gaps
# ---------------------------------------------------------------------------
def _sess_vwap(df, now: dt.datetime):
    """Dollar-weighted session VWAP from whatever minutes exist (regular
    session preferred, all tape if the day hasn't opened)."""
    try:
        d = df[df.index <= now]
        reg = d.between_time("09:30", "15:59")
        use = reg if len(reg) else d
        v = use["Volume"].to_numpy(float)
        typ = (use["High"] + use["Low"] + use["Close"]).to_numpy(float) / 3.0
        cv = float(v.sum())
        return (float((v * typ).sum()) / cv) if cv > 0 else None
    except Exception:
        return None


def build_rows(watchlist: list[str], snaps: dict, bars: dict,
               base: dict | None, states: dict | None,
               now: dt.datetime, mood_store: dict | None = None) -> list[dict]:
    """One row per watchlist ticker, in HIS input order, always. Missing
    data degrades to a `reason` string, never to a missing row. When a
    `mood_store` is passed, each row carries the sticky MOOD + NOW read
    (item 32): last-15-min flow/travel anchored to the session's own peak."""
    base = base or {}
    states = states or {}
    rows = []
    open_t = dt.datetime.combine(now.date(), dt.time(9, 30), tzinfo=NY)
    elapsed = max((now - open_t).total_seconds() / 60.0, 0.0)
    for t in watchlist:
        s = snaps.get(t) or {}
        day = s.get("dailyBar") or {}
        lt = s.get("latestTrade") or {}
        prevd = s.get("prevDailyBar") or {}
        df = bars.get(t)
        st = states.get(t) or {}

        last = float(lt.get("p") or 0) or float(day.get("c") or 0) or None
        if last is None and df is not None and len(df):
            last = float(df["Close"].iloc[-1])
        # prev close: engine baseline first, snapshot's prev bar as the
        # no-baseline fallback (new listing / fresh split has no baseline)
        pc = (base.get("prev_close") or {}).get(t) \
            or (float(prevd.get("c") or 0) or None)
        hod = float(day.get("h") or 0) or None
        lod = float(day.get("l") or 0) or None
        opn = float(day.get("o") or 0) or None
        shares = float(day.get("v") or 0)
        dollars = shares * last if last else 0.0
        if df is not None and len(df):
            d_ = df[df.index <= now]
            dollars = max(dollars,
                          float((d_["Volume"] * d_["Close"]).sum()))
            if hod is None and len(d_):
                hod = float(d_["High"].max())
            if opn is None and len(d_):
                opn = float(d_["Open"].iloc[0])
        vwap = _sess_vwap(df, now) if df is not None else None
        ps = path_stats(df, now) if df is not None else None
        ns = now_stats(df, now) if df is not None else None
        mood = None
        if mood_store is not None:
            mood = sticky_mood(mood_store, t, mood_candidate(ns, elapsed))

        adv = (base.get("adv") or {}).get(t)
        r = {
            "ticker": t,
            "last": last,
            "day_pct": (last / pc - 1.0) if (last and pc) else None,
            "move": (last / pc - 1.0) if (last and pc) else 0.0,
            "open": opn,
            "off_hi": (last / hod - 1.0) if (last and hod) else None,
            "vs_vwap": st.get("vs_vwap",
                              (last / vwap - 1.0) if (last and vwap) else None),
            "ssr": bool(lod and pc and lod <= 0.9 * pc),
            "dollars": dollars,
            "shares": shares,
            "vs_adv": (shares / adv) if (adv and shares) else None,
            "state": st.get("state"),
            "tp": st.get("tp"),
            "heat": (ps or {}).get("heat"),
            "swings": (ps or {}).get("swings"),
            "path": (ps or {}).get("path"),
            "mood": mood,
            "f15": (ns or {}).get("f15"),
            "r15": (ns or {}).get("r15"),
            "travel15": (ns or {}).get("travel15"),
            "stalled_min": (ns or {}).get("stalled_min"),
        }
        # the honest reason line — a card is NEVER blank
        if last is None:
            r["reason"] = "no IEX prints yet today"
        elif not dollars:
            r["reason"] = "quote only — no IEX volume yet today"
        elif dollars < 25_000:
            r["reason"] = (f"thin IEX tape: ${dollars / 1e3:.1f}k today — "
                           "second-scale reads unreliable")
        elif pc is None:
            r["reason"] = ("no prior close on file (new listing or fresh "
                           "split) — day% unavailable")
        else:
            r["reason"] = None
        rows.append(r)
    return rows


def dump_state(sdir: str, now: dt.datetime, rows: list[dict]) -> dict:
    payload = {"v": STATE_V, "ts": now.isoformat(timespec="seconds"),
               "tickers": [r["ticker"] for r in rows],
               "rows": [{k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in r.items()} for r in rows]}
    with open(os.path.join(sdir, "latest_watch.json"), "w") as f:
        json.dump(payload, f)
    return payload


def refresh(ap, sdir: str, now: dt.datetime, watchlist: list[str],
            base: dict | None = None, states: dict | None = None,
            bars: dict | None = None, log=None) -> list[dict]:
    """Fetch + assemble + persist the watch lane. Called from every lane:
    full flow tick, ext sweeps, and the 45s fast lane. Two API calls for
    the whole list; both exception-safe (a feed error degrades the card,
    never the shift)."""
    if not watchlist:
        dump_state(sdir, now, [])
        return []
    snaps = {}
    try:
        snaps = ap.snapshots(watchlist)
    except Exception as e:
        if log:
            log(f"watch snapshots: {e}")
    if bars is None:
        try:
            start = dt.datetime.combine(now.date(), dt.time(4, 0),
                                        tzinfo=NY).isoformat()
            bars = ap.bars(watchlist, "1Min", start)
        except Exception as e:
            bars = {}
            if log:
                log(f"watch bars: {e}")
    # sticky-mood memory survives across ticks (and shift restarts)
    mood_p = os.path.join(sdir, f"watch_mood_{now.date().isoformat()}.json")
    try:
        with open(mood_p) as f:
            mood_store = json.load(f)
    except Exception:
        mood_store = {}
    rows = build_rows(watchlist, snaps, bars, base, states, now,
                      mood_store=mood_store)
    try:
        with open(mood_p, "w") as f:
            json.dump(mood_store, f)
    except Exception:
        pass
    dump_state(sdir, now, rows)
    return rows
