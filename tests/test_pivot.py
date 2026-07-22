#!/usr/bin/env python3
"""Fixture tests for the watchlist-first pivot (HANDOFF item 31) and
split-aware ADV (item 30). Plain python, no network, no pytest:

    python tests/test_pivot.py
"""
import datetime as dt
import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.splits import adjusted_volumes, detect_split_events, split_aware_adv
from src.util import NY
from src import watch

PASS = []


def ok(name, cond, detail=""):
    assert cond, f"FAIL: {name} {detail}"
    PASS.append(name)


# ---------------------------------------------------------------------------
# item 30 — split-aware ADV
# ---------------------------------------------------------------------------
def test_splits():
    # INLF class: 1:10 reverse split mid-window. Raw ADV overstates 5.5x,
    # rvol reads 0.18x and the name goes invisible. Split-aware: 1.0x.
    c = np.array([0.5] * 15 + [5.0] * 10)
    v = np.array([1_000_000.0] * 15 + [100_000.0] * 10)
    ok("reverse split detected", detect_split_events(c, v) == [(15, 10.0)])
    ok("reverse split ADV", abs(split_aware_adv(c, v, n=20) - 100_000) < 1)
    # CPHI class: a REAL 2x on 30x tape must never be eaten as a split
    c2 = np.array([1.0] * 20 + [2.0] * 2)
    v2 = np.array([500_000.0] * 20 + [15_000_000.0, 8_000_000.0])
    ok("real 2x kept", detect_split_events(c2, v2) == [])
    # forward 4:1
    c3 = np.array([100.0] * 10 + [25.0] * 10)
    v3 = np.array([50_000.0] * 10 + [200_000.0] * 10)
    ok("forward split", np.allclose(adjusted_volumes(c3, v3), 200_000))
    # authoritative ex-date path (corporate-actions endpoint answered)
    dates = [f"2026-07-{d:02d}" for d in range(1, 26)]
    adj = adjusted_volumes(c, v, dates=dates, known=[("2026-07-16", 10.0)])
    ok("known ex-date path", np.allclose(adj, 100_000))


# ---------------------------------------------------------------------------
# pivot — the $2k-tape MY NAMES card (definition-of-done fixture)
# ---------------------------------------------------------------------------
def _bars_2k(now):
    """A name with NO baseline, NO history: five thin prints, ~$2k total."""
    idx = pd.DatetimeIndex(
        [now - dt.timedelta(minutes=m) for m in (200, 150, 90, 30, 5)][::-1])
    px = [1.00, 1.02, 0.99, 1.05, 1.04]
    vol = [400, 380, 400, 420, 380]          # ~$2k of tape
    return pd.DataFrame({"Open": px, "High": [p * 1.01 for p in px],
                         "Low": [p * 0.99 for p in px], "Close": px,
                         "Volume": vol}, index=idx)


def test_watch_card_no_baseline():
    now = dt.datetime.now(NY).replace(hour=11, minute=0)
    bars = {"ZZAP": _bars_2k(now)}
    snaps = {"ZZAP": {"latestTrade": {"p": 1.04},
                      "dailyBar": {"o": 1.00, "h": 1.06, "l": 0.98,
                                   "c": 1.04, "v": 1980},
                      "prevDailyBar": {"c": 1.10}}}
    rows = watch.build_rows(["ZZAP"], snaps, bars, base=None, states=None,
                            now=now)
    r = rows[0]
    ok("card exists", r["ticker"] == "ZZAP")
    ok("card last", r["last"] == 1.04)
    ok("card day% from prevDailyBar", abs(r["day_pct"] - (1.04 / 1.10 - 1)) < 1e-9)
    ok("card vwap side", r["vs_vwap"] is not None)
    ok("card off_hi", r["off_hi"] is not None)
    ok("honest thin-tape reason", r["reason"] and "thin IEX tape" in r["reason"],
       repr(r["reason"]))
    # zero tape → still a card, with the no-prints reason
    rows2 = watch.build_rows(["GHST"], {}, {}, None, None, now)
    ok("no-tape card present", rows2[0]["ticker"] == "GHST")
    ok("no-tape honest reason", rows2[0]["reason"] == "no IEX prints yet today")


# ---------------------------------------------------------------------------
# pivot — dispatch tickers → watchlist.txt → docs/watch.json round-trip
# ---------------------------------------------------------------------------
def test_roundtrip_and_hub():
    with tempfile.TemporaryDirectory() as tmp:
        wf = os.path.join(tmp, "watchlist.txt")
        got = watch.write_watchlist(wf, "cphi, omh slgb\ninlf zzap")
        ok("dispatch input sanitized",
           got == ["CPHI", "OMH", "SLGB", "INLF", "ZZAP"], got)
        cfg = {"_paths": {"root": tmp, "data": tmp,
                          "journal": os.path.join(tmp, "j.db")},
               "universe": {"watchlist_file": "watchlist.txt"}}
        ok("file round-trips", watch.load_watchlist(cfg) == got)

        # state written by the engine lane
        sdir = os.path.join(tmp, "state")
        os.makedirs(sdir)
        now = dt.datetime.now(NY).replace(hour=11, minute=0)
        bars = {"ZZAP": _bars_2k(now)}
        snaps = {"ZZAP": {"latestTrade": {"p": 1.04},
                          "dailyBar": {"o": 1.00, "h": 1.06, "l": 0.98,
                                       "c": 1.04, "v": 1980},
                          "prevDailyBar": {"c": 1.10}}}
        rows = watch.build_rows(got, snaps, bars, None, None, now)
        ok("every name gets a row, his order",
           [r["ticker"] for r in rows] == got)
        watch.dump_state(sdir, now, rows)

        # hub renders MY NAMES above the discovery board + writes watch.json
        from src import hub
        out = os.path.join(tmp, "docs")
        hub.build(cfg, out, demo=False)
        html = open(os.path.join(out, "index.html")).read()
        ok("MY NAMES rendered", "MY NAMES" in html)
        for t in got:
            ok(f"card {t} present", f">{t}<" in html)
        ok("$2k card honest reason", "thin IEX tape" in html)
        ok("no-tape names never blank",
           "engine picks it up on the next tick" in html
           or "no IEX prints yet today" in html)
        wjs = json.load(open(os.path.join(out, "watch.json")))
        ok("docs/watch.json tickers", wjs["tickers"] == got)
        ok("docs/watch.json gated", wjs["v"] == 4)


def test_hub_order_and_empty():
    """MY NAMES sits above the discovery board; empty list explains itself."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"_paths": {"root": tmp, "data": tmp,
                          "journal": os.path.join(tmp, "j.db")},
               "universe": {"watchlist_file": "watchlist.txt"}}
        sdir = os.path.join(tmp, "state")
        os.makedirs(sdir)
        now = dt.datetime.now(NY)
        watch.write_watchlist(os.path.join(tmp, "watchlist.txt"), "ZCMD")
        watch.dump_state(sdir, now, watch.build_rows(["ZCMD"], {}, {}, None,
                                                     None, now))
        board = {"v": 4, "ts": now.isoformat(timespec="seconds"),
                 "session": "rth",
                 "rows": [{"ticker": "LABT", "move": 2.08, "last": 5.1,
                           "dollars": 4e6, "vs_adv": 8.0, "off_hi": -0.05,
                           "state": "RUNNING", "open": 1.7, "ssr": False,
                           "vs_vwap": 0.03, "tp": 4.0, "hot": True,
                           "pin": False, "heat": 88.0, "swings": 6,
                           "path": 2.9, "first_seen": "09:35", "new": False}]}
        with open(os.path.join(sdir, "latest_board.json"), "w") as f:
            json.dump(board, f)
        from src import hub
        out = os.path.join(tmp, "docs")
        hub.build(cfg, out, demo=False)
        html = open(os.path.join(out, "index.html")).read()
        ok("MY NAMES above discovery board",
           html.index("MY NAMES") < html.index("Discovery"))
        ok("board name still shown", ">LABT<" in html)
        # empty watchlist → the section still exists and explains itself
        watch.write_watchlist(os.path.join(tmp, "watchlist.txt"), "")
        watch.dump_state(sdir, now, [])
        hub.build(cfg, out, demo=False)
        html = open(os.path.join(out, "index.html")).read()
        ok("empty list explained", "no names yet" in html)


# ---------------------------------------------------------------------------
# item 32 — NOW read + sticky MOOD (no green-red-green flicker)
# ---------------------------------------------------------------------------
def test_now_and_mood():
    from src.flow_alpaca import (mood_candidate, now_stats, path_stats,
                                 sticky_mood)
    now = dt.datetime.now(NY).replace(hour=13, minute=0, second=0,
                                      microsecond=0)
    # morning runner that went SIDEWAYS for the last 2 hours
    idx, px, vol = [], [], []
    p = 1.0
    for m in range(210):                       # 9:30 → 13:00
        t_ = now - dt.timedelta(minutes=210 - m)
        if m < 60:
            p *= 1.012                         # runs +100%-ish into 10:30
            v = 80_000
        else:
            p *= 1.0002 if m % 2 else 0.9998   # dead sideways after
            v = 2_000
        idx.append(t_)
        px.append(p)
        vol.append(v)
    df = pd.DataFrame({"Open": px, "High": [x * 1.001 for x in px],
                       "Low": [x * 0.999 for x in px], "Close": px,
                       "Volume": vol}, index=pd.DatetimeIndex(idx))
    ns = now_stats(df, now)
    ok("sideways name reads tiny vs its peak", ns["r15"] is not None
       and ns["r15"] < 0.12, ns)
    ok("stalled detected", ns["stalled_min"] >= 90, ns["stalled_min"])
    cand = mood_candidate(ns, 210)
    ok("mood candidate stalled/dead", cand in ("STALLED", "DEAD"), cand)
    ps = path_stats(df, now)
    ok("stale heat sinks (floor 0.10)", ps["heat"] <= 12, ps["heat"])
    # the same name while it was RUNNING reads alive
    mid = now - dt.timedelta(minutes=155)      # ~10:25, mid-run
    ns2 = now_stats(df[df.index <= mid], mid)
    ok("running name reads alive", ns2["r15"] >= 0.55, ns2)
    ok("running mood", mood_candidate(ns2, 55) == "MONEY HERE")

    # sticky mood: borderline flicker input must NOT flip the label
    store = {}
    ok("mood seeds", sticky_mood(store, "X", "MONEY HERE") == "MONEY HERE")
    seq = ["COOLING", "MONEY HERE", "COOLING", "MONEY HERE", "COOLING"]
    outs = [sticky_mood(store, "X", c) for c in seq]
    ok("alternating candidates never flip", set(outs) == {"MONEY HERE"}, outs)
    # a REAL degrade (2 consecutive ticks) flips
    sticky_mood(store, "X", "MONEY LEAVING")
    out = sticky_mood(store, "X", "MONEY LEAVING")
    ok("2-tick degrade flips", out == "MONEY LEAVING", out)
    # recovery needs 3 ticks
    a = sticky_mood(store, "X", "MONEY HERE")
    b = sticky_mood(store, "X", "MONEY HERE")
    c = sticky_mood(store, "X", "MONEY HERE")
    ok("3-tick recover", (a, b, c) == ("MONEY LEAVING", "MONEY LEAVING",
                                       "MONEY HERE"), (a, b, c))


def test_watch_json_rows_and_hub_now():
    """docs/watch.json carries card-ready rows; hub renders MOOD + now line."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"_paths": {"root": tmp, "data": tmp,
                          "journal": os.path.join(tmp, "j.db")},
               "universe": {"watchlist_file": "watchlist.txt"}}
        sdir = os.path.join(tmp, "state")
        os.makedirs(sdir)
        now = dt.datetime.now(NY)
        watch.write_watchlist(os.path.join(tmp, "watchlist.txt"), "ZZAP")
        rows = [{"ticker": "ZZAP", "last": 2.0, "day_pct": 0.4, "move": 0.4,
                 "open": 1.5, "off_hi": -0.02, "vs_vwap": 0.01, "ssr": False,
                 "dollars": 3e6, "shares": 1.5e6, "vs_adv": None,
                 "state": "RUNNING", "tp": 3.0, "heat": 80.0, "swings": 5,
                 "path": 1.2, "mood": "MONEY HERE", "f15": 2.4e5, "r15": 0.7,
                 "travel15": 0.04, "stalled_min": 0, "reason": None}]
        watch.dump_state(sdir, now, rows)
        from src import hub
        out = os.path.join(tmp, "docs")
        hub.build(cfg, out, demo=False)
        html = open(os.path.join(out, "index.html")).read()
        ok("mood chip rendered", "MONEY HERE" in html)
        ok("story rendered", "money is here NOW" in html)
        ok("editor present", 'id="wq"' in html and 'id="wsetup"' in html)
        wjs = json.load(open(os.path.join(out, "watch.json")))
        r = wjs["rows"][0]
        ok("watch.json card-ready", r["present"] and r["mood"] == "MONEY HERE"
           and r["ev"] and r["read"], r.get("ev"))


# ---------------------------------------------------------------------------
# item 33 — the card speaks English (story generator)
# ---------------------------------------------------------------------------
def test_story():
    from src.hub import _story
    # his literal INLF screenshot numbers (2026-07-22)
    r = {"present": True, "day_pct": 0.635, "off_hi": -0.27,
         "vs_vwap": -0.112, "mood": "MONEY LEAVING", "f15": 8000, "r15": 0.14,
         "swings": 10, "travel15": 0.123, "stalled_min": 0}
    s = _story(r)
    ok("INLF story: vwap", "lost the vwap" in s, s)
    ok("INLF story: money walking", "walking" in s and "$8K" in s, s)
    ok("INLF story: green trap named", "chart still shows green" in s, s)
    ok("INLF story: still whipping", "whipping (10 legs" in s, s)
    r2 = {"present": True, "day_pct": 0.4, "off_hi": -0.01, "vs_vwap": 0.02,
          "mood": "STALLED", "f15": 500, "r15": 0.05, "stalled_min": 115,
          "swings": 2, "travel15": 0.001}
    s2 = _story(r2)
    ok("stalled story", "1h55m" in s2 and "dead sideways" in s2, s2)
    ok("no-row story is None", _story({"present": False}) is None)


if __name__ == "__main__":
    test_splits()
    test_watch_card_no_baseline()
    test_roundtrip_and_hub()
    test_hub_order_and_empty()
    test_now_and_mood()
    test_watch_json_rows_and_hub_now()
    test_story()
    print(f"OK — {len(PASS)} checks passed")
