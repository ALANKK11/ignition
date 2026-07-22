"""INTEL — why it runs / why it dies, joined onto the live board.

Three free primary sources, each answering a question the tape alone cannot:

  HALTS  (nasdaqtrader RSS)  — a LULD pause IS a >=10%-move-in-5-minutes
         event, market-wide, for free: the user's exact instrument. One feed
         covers Nasdaq- AND other-exchange-listed names. Poll <= 1/minute
         (Nasdaq's stated limit). 3+ halts up = exhaustion risk.
  EDGAR  (data.sec.gov)      — the most common hidden reason a runner gets
         smashed is an open dilution vehicle. Fresh 424B* paper, pending
         S-1, or an effective S-3 shelf are all detectable from the
         submissions JSON minutes after filing. 10 req/s cap, UA required.
  FLOAT  (yfinance, cached)  — day volume / float = rotation. >1x in play,
         >5x hot, >10x parabolic fuel. yfinance floats are stale for
         micro-caps; treat as order-of-magnitude, never gate on it.

The EDGE verdict fuses these with the tape (HOD extension fade table, VWAP
side, halt count) into one word per board row. The fade percentages are
single-vendor gapper stats (~3k events) — strong priors, not laws; they are
displayed as "hist" and never silently gate anything.

Parser note: the halt parser was built against a LIVE pull of the real feed
(2026-07-22 13:21 GMT), which contained an undocumented reason code ("D")
and items dating back to 2019 — hence: tolerate unknown codes, filter to
today for the day view, and diff polls to build the day log.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET

from .util import NY

STATE_V = 4
NDAQ = "{http://www.nasdaqtrader.com/}"
HALT_URL = "http://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
UA = "ignition-hub open-source (github.com/ALANKK11/ignition)"

# HOD-extension -> historical fade odds (close below open), per gapper stats
EXT_FADE = [(0.10, 93), (0.25, 74), (0.50, 57), (1.00, 31), (9e9, 11)]

DIL_ORDER = ["FRESH PAPER", "S-1 PENDING", "OPEN SHELF", "CLEAN"]


def _get(url: str, timeout: float = 8.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _save(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


# ---------------------------------------------------------------------------
# HALT RADAR
# ---------------------------------------------------------------------------
def parse_halt_rss(raw: bytes) -> list[dict]:
    """Tolerant parse of the real feed. Every field may be missing/empty."""
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return out
    for it in root.iter("item"):
        def g(tag):
            e = it.find(NDAQ + tag)
            v = (e.text or "").strip() if e is not None and e.text else ""
            return v or None
        sym = g("IssueSymbol")
        if not sym:
            continue
        out.append({"sym": sym, "name": g("IssueName"), "date": g("HaltDate"),
                    "hm": (g("HaltTime") or "")[:5] or None,
                    "code": g("ReasonCode") or "?",
                    "thr": g("PauseThresholdPrice"),
                    "res_d": g("ResumptionDate"),
                    "res_t": (g("ResumptionTradeTime") or "")[:5] or None,
                    "mkt": g("Market")})
    return out


def poll_halts(sdir: str, now: dt.datetime, log=None) -> dict:
    """Fetch (>=60s apart), diff into a per-day log, summarize per symbol.
    Returns {"today": [...], "by_sym": {...}, "halted_now": [...]}."""
    meta_p = os.path.join(sdir, "halt_poll_meta.json")
    day_p = os.path.join(sdir, f"halts_day_{now.date().isoformat()}.json")
    meta = _load(meta_p) or {}
    day = _load(day_p) or {"items": []}
    if time.time() - float(meta.get("t", 0)) >= 60:
        try:
            items = parse_halt_rss(_get(HALT_URL))
            meta = {"t": time.time(), "n": len(items)}
            _save(meta_p, meta)
            today = now.strftime("%m/%d/%Y")
            seen = {(i["sym"], i["hm"]) for i in day["items"]}
            fresh = [i for i in items if i.get("date") == today]
            for i in fresh:
                k = (i["sym"], i["hm"])
                if k in seen:            # re-seen: update resume fields
                    for o in day["items"]:
                        if (o["sym"], o["hm"]) == k:
                            o.update(i)
                    continue
                day["items"].append(i)
                seen.add(k)
            # names halted right now (no resume yet), any vintage
            day["halted_now"] = sorted({i["sym"] for i in items
                                        if not i.get("res_t")})
            _save(day_p, day)
        except Exception as e:          # feed down != shift down
            if log:
                log(f"halt feed: {e}")
    by = {}
    for i in day["items"]:
        b = by.setdefault(i["sym"], {"n": 0, "codes": [], "last_hm": None,
                                     "thr": None, "res_t": None})
        b["n"] += 1
        b["codes"].append(i["code"])
        b["last_hm"], b["thr"], b["res_t"] = i["hm"], i.get("thr"), i.get("res_t")
    return {"today": day["items"], "by_sym": by,
            "halted_now": day.get("halted_now", [])}


# ---------------------------------------------------------------------------
# EDGAR dilution grade
# ---------------------------------------------------------------------------
def ensure_cik_map(sdir: str, log=None) -> dict:
    p = os.path.join(sdir, "cik_map.json")
    m = _load(p)
    if m and time.time() - float(m.get("_t", 0)) < 3 * 86400:
        return m
    try:
        js = json.loads(_get("https://www.sec.gov/files/company_tickers.json",
                             timeout=20))
        m = {v["ticker"].upper(): int(v["cik_str"]) for v in js.values()}
        m["_t"] = time.time()
        _save(p, m)
    except Exception as e:
        if log:
            log(f"cik map: {e}")
        m = m or {"_t": 0}
    return m


def _grade_filings(forms: list[str], dates: list[str], today: dt.date):
    """Newest-first columnar arrays -> (grade, why)."""
    fresh_d, shelf_d, s1_d = 7, 540, 120
    why = []
    grade = "CLEAN"
    for f, d in zip(forms, dates):
        try:
            age = (today - dt.date.fromisoformat(d)).days
        except Exception:
            continue
        if age > shelf_d:
            break
        base = f.split("/")[0].upper()
        if base in ("424B5", "424B4", "424B3", "424B2") and age <= fresh_d:
            return "FRESH PAPER", f"{f} {d} — shares being sold NOW"
        if base in ("S-1", "F-1") and age <= fresh_d:
            return "FRESH PAPER", f"{f} {d} — new offering registered"
        if base in ("S-1", "F-1") and age <= s1_d and grade != "S-1 PENDING":
            grade, why = "S-1 PENDING", [f"{f} {d}"]
        if base in ("S-3", "S-3ASR", "F-3", "EFFECT") and grade == "CLEAN":
            grade, why = "OPEN SHELF", [f"{f} {d} — can sell into strength"]
    return grade, (why[0] if why else "no dilution vehicle in filings")


def dilution(sym: str, sdir: str, now: dt.datetime, cache: dict,
             ttl_h: float = 6.0, log=None) -> dict:
    """Grade one ticker from its EDGAR submissions JSON, cached."""
    c = cache.get(sym)
    if c and time.time() - float(c.get("_t", 0)) < ttl_h * 3600:
        return c
    out = {"grade": "UNKNOWN", "why": "not on EDGAR map", "_t": time.time()}
    try:
        cik = ensure_cik_map(sdir, log).get(sym.upper())
        if cik:
            js = json.loads(_get(
                f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                timeout=12))
            rec = (js.get("filings") or {}).get("recent") or {}
            g, w = _grade_filings(rec.get("form") or [],
                                  rec.get("filingDate") or [], now.date())
            out = {"grade": g, "why": w, "_t": time.time(),
                   "recent": [[f, d] for f, d in
                              zip((rec.get("form") or [])[:5],
                                  (rec.get("filingDate") or [])[:5])]}
        time.sleep(0.15)                 # stay far under SEC's 10 req/s
    except Exception as e:
        out = {"grade": "UNKNOWN", "why": f"edgar: {e}", "_t": time.time()}
    cache[sym] = out
    return out


# ---------------------------------------------------------------------------
# float & rotation
# ---------------------------------------------------------------------------
def float_rot(sym: str, cache: dict, ttl_h: float = 24.0, log=None) -> dict:
    c = cache.get(sym)
    if c and time.time() - float(c.get("_t", 0)) < ttl_h * 3600:
        return c
    out = {"flo": None, "vol": None, "_t": time.time()}
    try:
        import yfinance as yf
        tk = yf.Ticker(sym)
        fi = tk.fast_info
        flo = None
        try:
            flo = float(tk.info.get("floatShares") or 0) or None
        except Exception:
            pass
        flo = flo or float(getattr(fi, "shares", 0) or 0) or None
        vol = float(getattr(fi, "last_volume", 0) or 0) or None
        out = {"flo": flo, "vol": vol, "_t": time.time()}
    except Exception as e:
        if log:
            log(f"float {sym}: {e}")
    cache[sym] = out
    return out


def rot_of(fr: dict) -> float | None:
    if fr and fr.get("flo") and fr.get("vol"):
        return fr["vol"] / fr["flo"]
    return None


# ---------------------------------------------------------------------------
# EDGE verdict
# ---------------------------------------------------------------------------
def ext_of(row: dict) -> float | None:
    """HOD extension above the open: how far the day actually lifted."""
    op, off, last = row.get("open"), row.get("off_hi"), row.get("last")
    if not op or last is None or off is None:
        return None
    hod = last / (1.0 + off) if off > -0.999 else None
    return (hod / op - 1.0) if (hod and op) else None


def fade_odds(ext: float | None) -> int | None:
    if ext is None:
        return None
    for cut, pct in EXT_FADE:
        if ext <= cut:
            return pct
    return None


def edge_verdict(row: dict, dil: dict | None, halts: dict | None,
                 rot: float | None) -> tuple[str, str, str]:
    """(word, color, why) for one board row. Direction-agnostic tape, but
    dilution only smashes longs — which is his 'preferably green' side."""
    why = []
    grade = (dil or {}).get("grade") or "UNKNOWN"
    hn = (halts or {}).get("n", 0)
    up = row.get("move", 0) > 0
    ext = ext_of(row)
    fo = fade_odds(ext)
    vs = row.get("vs_vwap")

    if ext is not None:
        why.append(f"ext {ext:+.0%} → {fo}% fade hist")
    if vs is not None:
        why.append(("above" if vs >= 0 else "BELOW") + " vwap")
    if hn:
        why.append(f"{hn} halt{'s' if hn > 1 else ''} today")
    if rot is not None:
        why.append(f"rot {rot:.1f}x")
    if grade == "FRESH PAPER":
        why.insert(0, (dil or {}).get("why") or "offering live")
    elif grade == "OPEN SHELF":
        why.insert(0, "shelf open")     # the verdict-changing fact leads
    elif grade == "S-1 PENDING":
        why.insert(0, "S-1 pending")

    if up and grade == "FRESH PAPER":
        return "SMASH RISK", "#f87171", " · ".join(why[:4])
    if hn >= 3 and up:
        return "SMASH RISK", "#f87171", " · ".join(
            [f"{hn} halts up — exhaustion"] + why[:2])
    if row.get("move", 0) >= 0.25 and ext is not None and ext < 0.10:
        return "FADE RISK", "#fb923c", " · ".join(
            ["no lift off open"] + why[:2])
    if (vs is not None and vs < 0) or (fo is not None and fo >= 74):
        return "FADE RISK", "#fb923c", " · ".join(why[:4])
    if (ext is not None and ext >= 0.50 and (vs is None or vs >= 0)
            and grade in ("CLEAN", "UNKNOWN") and (rot is None or rot >= 1)):
        return "GO", "#4ade80", " · ".join(why[:4])
    return "WATCH", "#facc15", " · ".join(why[:4]) or "reading the tape"


# ---------------------------------------------------------------------------
# orchestration — called once per full tick from the live loop
# ---------------------------------------------------------------------------
def refresh_intel(sdir: str, now: dt.datetime, board: dict | None,
                  icfg: dict | None = None, log=None,
                  priority: list[str] | None = None,
                  rsplits: dict | None = None) -> dict:
    """`priority` (the watchlist) is looked up BEFORE board names inside the
    per-tick budget — his names get EDGAR/float intel first, always.
    Watchlist names additionally get the PEDIGREE pass (item 36): who is
    this company, structurally — cached a week, so it's ~free per tick."""
    icfg = icfg or {}
    st = _load(os.path.join(sdir, "latest_intel.json")) or {}
    dil_c = st.get("dil") or {}
    flo_c = st.get("flo") or {}
    ped_c = st.get("ped") or {}
    halts = poll_halts(sdir, now, log)
    tickers = list(dict.fromkeys(
        (priority or []) + [r["ticker"] for r in (board or {}).get("rows", [])]))
    for t in tickers[: int(icfg.get("max_lookups", 15))]:
        dilution(t, sdir, now, dil_c, float(icfg.get("dil_ttl_h", 6)), log)
        float_rot(t, flo_c, float(icfg.get("float_ttl_h", 24)), log)
    from .pedigree import pedigree
    for t in (priority or [])[: int(icfg.get("max_lookups", 15))]:
        fr = flo_c.get(t) or {}
        pedigree(t, sdir, now, ped_c,
                 rsplits_13m=int((rsplits or {}).get(t, 0)),
                 float_sh=fr.get("flo"), log=log)
    out = {"v": STATE_V, "ts": now.isoformat(timespec="seconds"),
           "halts_today": halts["today"], "halts_by": halts["by_sym"],
           "halted_now": halts["halted_now"], "dil": dil_c, "flo": flo_c,
           "ped": ped_c}
    _save(os.path.join(sdir, "latest_intel.json"), out)
    return out
