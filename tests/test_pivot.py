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


if __name__ == "__main__":
    test_splits()
    test_watch_card_no_baseline()
    test_roundtrip_and_hub()
    test_hub_order_and_empty()
    print(f"OK — {len(PASS)} checks passed")
