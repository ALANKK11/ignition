"""Self-audit journal.

Every scan writes its ranked picks AND a random control sample from the same
universe into SQLite. `eval` later pulls the next session's actual bars and
grades the scan on two questions:

  1. Rank IC  — Spearman correlation between tonight's score and tomorrow's
                realized activity (blend of realized RVOL rank + range rank).
  2. Edge     — mean realized RVOL / range of the top-10 picks divided by the
                random control sample. Edge 1.0x = the scanner is decorative.

No trust-me. The tool keeps receipts on itself.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

import numpy as np
import pandas as pd

from .util import spearman

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    mode TEXT NOT NULL,
    demo INTEGER NOT NULL DEFAULT 0,
    universe_size INTEGER,
    evaluated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS picks (
    scan_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    rank INTEGER,
    score REAL,
    close REAL,
    adv20 REAL,
    atr20_pct REAL,
    is_control INTEGER NOT NULL DEFAULT 0,
    components TEXT,
    PRIMARY KEY (scan_id, ticker)
);
CREATE TABLE IF NOT EXISTS flow_events (
    id INTEGER PRIMARY KEY,
    ts TEXT, demo INTEGER, ticker TEXT,
    prev_state TEXT, state TEXT,
    price REAL, tp REAL, note TEXT
);
CREATE TABLE IF NOT EXISTS ignition_evals (
    day TEXT, ticker TEXT, session TEXT,
    hit INTEGER, rvol REAL, dollar REAL,
    PRIMARY KEY (day, ticker)
);
CREATE TABLE IF NOT EXISTS evals (
    scan_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    next_date TEXT,
    next_rvol REAL,
    next_range_pct REAL,
    next_gap_pct REAL,
    next_ret_pct REAL,
    PRIMARY KEY (scan_id, ticker)
);
"""


class Journal:
    def __init__(self, path: str):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.executescript(SCHEMA)
        self.con.commit()

    # -- write -------------------------------------------------------------
    def record_scan(
        self,
        trade_date: dt.date,
        mode: str,
        demo: bool,
        universe_size: int,
        rows: list[dict],
        controls: list[dict],
    ) -> int:
        cur = self.con.cursor()
        cur.execute(
            "INSERT INTO scans (ts, trade_date, mode, demo, universe_size) VALUES (?,?,?,?,?)",
            (dt.datetime.now().isoformat(timespec="seconds"), trade_date.isoformat(),
             mode, int(demo), universe_size),
        )
        sid = cur.lastrowid
        for is_ctrl, batch in ((0, rows), (1, controls)):
            for r in batch:
                cur.execute(
                    "INSERT OR REPLACE INTO picks VALUES (?,?,?,?,?,?,?,?,?)",
                    (sid, r["ticker"], r.get("rank"), r.get("score"), r.get("close"),
                     r.get("adv20"), r.get("atr20_pct"), is_ctrl,
                     json.dumps(r.get("components", {}))),
                )
        self.con.commit()
        return sid

    # -- read --------------------------------------------------------------
    def pending_scans(self, demo: bool) -> list[dict]:
        cur = self.con.execute(
            "SELECT id, trade_date, mode FROM scans WHERE evaluated=0 AND demo=? "
            "ORDER BY trade_date", (int(demo),))
        return [{"id": i, "trade_date": dt.date.fromisoformat(d), "mode": m}
                for i, d, m in cur.fetchall()]

    def scan_picks(self, scan_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT ticker, rank, score, close, adv20, atr20_pct, is_control "
            "FROM picks WHERE scan_id=?", self.con, params=(scan_id,))

    # -- grade -------------------------------------------------------------
    def evaluate_scan(self, scan_id: int, trade_date: dt.date,
                      history: dict[str, pd.DataFrame]) -> dict | None:
        picks = self.scan_picks(scan_id)
        if picks.empty:
            return None
        realized = []
        for _, row in picks.iterrows():
            df = history.get(row["ticker"])
            if df is None or df.empty:
                continue
            after = df[df.index.normalize() > pd.Timestamp(trade_date)]
            asof = df[df.index.normalize() <= pd.Timestamp(trade_date)]
            if after.empty or asof.empty:
                continue
            nxt = after.iloc[0]
            prev_close = float(asof["Close"].iloc[-1])
            adv = row["adv20"]
            if not adv or adv <= 0:
                adv = float(asof["Volume"].iloc[-21:-1].mean() or np.nan)
            if not prev_close or prev_close <= 0 or not np.isfinite(adv) or adv <= 0:
                continue
            realized.append({
                "ticker": row["ticker"],
                "score": row["score"],
                "rank": row["rank"],
                "is_control": row["is_control"],
                "next_date": after.index[0].date().isoformat(),
                "next_rvol": float(nxt["Volume"]) / adv,
                "next_range_pct": (float(nxt["High"]) - float(nxt["Low"])) / prev_close * 100,
                "next_gap_pct": (float(nxt["Open"]) / prev_close - 1) * 100,
                "next_ret_pct": (float(nxt["Close"]) / prev_close - 1) * 100,
            })
        if len(realized) < 5:
            return None
        rdf = pd.DataFrame(realized)
        cur = self.con.cursor()
        for _, r in rdf.iterrows():
            cur.execute("INSERT OR REPLACE INTO evals VALUES (?,?,?,?,?,?,?)",
                        (scan_id, r["ticker"], r["next_date"], r["next_rvol"],
                         r["next_range_pct"], r["next_gap_pct"], r["next_ret_pct"]))
        cur.execute("UPDATE scans SET evaluated=1 WHERE id=?", (scan_id,))
        self.con.commit()
        return self._grade(rdf)

    @staticmethod
    def _grade(rdf: pd.DataFrame) -> dict:
        activity = rdf["next_rvol"].rank() + rdf["next_range_pct"].rank()
        ic = spearman(rdf["score"], activity)
        picks = rdf[rdf["is_control"] == 0].sort_values("rank")
        top = picks.head(10)
        ctrl = rdf[rdf["is_control"] == 1]
        edge_rvol = edge_range = float("nan")
        if len(ctrl) >= 5 and len(top) >= 3:
            edge_rvol = top["next_rvol"].mean() / max(ctrl["next_rvol"].mean(), 1e-9)
            edge_range = top["next_range_pct"].mean() / max(ctrl["next_range_pct"].mean(), 1e-9)
        return {
            "n": len(rdf),
            "ic": ic,
            "edge_rvol": edge_rvol,
            "edge_range": edge_range,
            "top": top,
            "ctrl_mean_rvol": ctrl["next_rvol"].mean() if len(ctrl) else float("nan"),
            "ctrl_mean_range": ctrl["next_range_pct"].mean() if len(ctrl) else float("nan"),
            "detail": rdf,
        }

    # -- history summary ---------------------------------------------------
    def history(self, demo: bool, limit: int = 20) -> pd.DataFrame:
        q = """
        SELECT s.id, s.trade_date, s.mode, s.evaluated, s.universe_size,
               COUNT(p.ticker) AS n_picks
        FROM scans s LEFT JOIN picks p ON p.scan_id = s.id AND p.is_control = 0
        WHERE s.demo = ?
        GROUP BY s.id ORDER BY s.trade_date DESC, s.id DESC LIMIT ?
        """
        scans = pd.read_sql_query(q, self.con, params=(int(demo), limit))
        ics, edges = [], []
        for sid in scans["id"]:
            e = pd.read_sql_query(
                "SELECT e.*, p.score, p.rank, p.is_control FROM evals e "
                "JOIN picks p ON p.scan_id=e.scan_id AND p.ticker=e.ticker "
                "WHERE e.scan_id=?", self.con, params=(sid,))
            if e.empty:
                ics.append(np.nan); edges.append(np.nan); continue
            g = self._grade(e)
            ics.append(g["ic"]); edges.append(g["edge_rvol"])
        scans["ic"] = ics
        scans["edge_rvol"] = edges
        return scans

    # -- flow -------------------------------------------------------------
    def record_flow_event(self, demo: bool, ev: dict) -> None:
        self.con.execute(
            "INSERT INTO flow_events (ts, demo, ticker, prev_state, state, price, tp, note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ev["ts"].isoformat(), int(demo), ev["ticker"], ev["prev"],
             ev["state"], ev["price"], ev["tp"], ev["note"]))
        self.con.commit()

    def latest_picks(self, demo: bool, limit: int = 30) -> list[str]:
        cur = self.con.execute(
            "SELECT p.ticker FROM picks p JOIN scans s ON p.scan_id=s.id "
            "WHERE s.demo=? AND p.is_control=0 AND s.id=(SELECT MAX(id) FROM scans WHERE demo=?) "
            "ORDER BY p.rank LIMIT ?", (int(demo), int(demo), limit))
        return [r[0] for r in cur.fetchall()]

    def events_for_day(self, demo: bool, day_prefix: str) -> list[dict]:
        cur = self.con.execute(
            "SELECT ts, ticker, prev_state, state, note FROM flow_events "
            "WHERE demo=? AND ts LIKE ? ORDER BY id", (int(demo), day_prefix + "%"))
        return [{"ts": r[0], "ticker": r[1], "prev_state": r[2],
                 "state": r[3], "note": r[4]} for r in cur.fetchall()]

    def pending_ignitions(self, demo: bool, before_day: str) -> list[tuple[str, str, str]]:
        cur = self.con.execute(
            "SELECT DISTINCT substr(f.ts,1,10) d, f.ticker, f.state "
            "FROM flow_events f LEFT JOIN ignition_evals e "
            "ON e.day = substr(f.ts,1,10) AND e.ticker = f.ticker "
            "WHERE f.demo=? AND f.state LIKE '%IGNITION' AND e.day IS NULL "
            "AND substr(f.ts,1,10) < ?", (int(demo), before_day))
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]

    def record_ignition_eval(self, day, ticker, session, hit, rvol, dollar):
        self.con.execute(
            "INSERT OR REPLACE INTO ignition_evals VALUES (?,?,?,?,?,?)",
            (day, ticker, session, int(hit), rvol, dollar))
        self.con.commit()

    def ignition_stats(self, days: int = 30) -> tuple[int, int]:
        import datetime as _dt
        cut = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
        r = self.con.execute("SELECT SUM(hit), COUNT(*) FROM ignition_evals "
                             "WHERE day >= ?", (cut,)).fetchone()
        return (int(r[0] or 0), int(r[1] or 0))
