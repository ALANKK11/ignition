"""PEDIGREE — what am I actually holding? (HANDOFF item 36)

The class of stock he trades — sub-$10 momentum runners — is thick with a
specific vehicle: an offshore-incorporated shell with the real business in
China, a serial reverse-splitter that re-registers shares every few months
and rugs the chart when the pump peaks. These names are TRADABLE (he trades
them on purpose); the point of this layer is never to hide them — it is to
label the anatomy so he rides the wave knowing exactly what he's holding
and never holds one overnight thinking it's a company.

Everything comes from the EDGAR submissions JSON we already fetch for the
dilution grade (free, cached), plus the split history already in the day's
baselines. All fail-open: EDGAR down -> UNKNOWN, never a crashed tick.

Flag set (each is a fact with a source, not an opinion):
  CN-LINKED        incorporated OR business-addressed in mainland China / HK
  OFFSHORE SHELL   incorporated in Cayman/BVI/Marshall Is. with the business
                   address somewhere else (the classic listing structure)
  SERIAL DILUTER   >=3 registration/offering filings (S-1/F-1/424B*/S-3/F-3)
                   in the last 12 months
  REVERSE SPLITS   >=1 reverse split in the last ~13 months (x2 = serial)
  MICRO FLOAT      float under 5M shares — one buyer moves it, one seller
                   nukes it
  FRESH REGISTRANT first SEC filing on record less than ~18 months ago

Grade: 0 flags -> CLEAN RECORD, 1-2 -> n FLAGS, >=3 -> PUMP ANATOMY n/6.
"""
from __future__ import annotations

import datetime as dt
import json
import time

# EDGAR state-or-country codes (subset that matters here). US states are the
# usual two-letter postal codes; foreign jurisdictions use EDGAR's own codes.
CN_CODES = {"F4": "China", "K3": "Hong Kong"}
OFFSHORE_CODES = {"E9": "Cayman Is.", "D8": "BVI", "1T": "Marshall Is.",
                  "C5": "Bermuda"}
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR"}

DILUTIVE_FORMS = ("S-1", "F-1", "S-3", "F-3", "424B")


def _jur(code: str | None) -> tuple[str, str]:
    """(kind, human) for a stateOrCountry code: cn / offshore / us / other."""
    c = (code or "").strip().upper()
    if not c:
        return "unknown", ""
    if c in CN_CODES:
        return "cn", CN_CODES[c]
    if c in OFFSHORE_CODES:
        return "offshore", OFFSHORE_CODES[c]
    if c in US_STATES:
        return "us", c
    return "other", c


def grade_pedigree(inc_code: str | None, biz_code: str | None,
                   forms: list[str], dates: list[str], today: dt.date,
                   rsplits_13m: int = 0, float_sh: float | None = None
                   ) -> dict:
    """Pure, offline-testable core. forms/dates are EDGAR's newest-first
    columnar arrays (same shape dilution() reads)."""
    flags: list[str] = []
    inc_k, inc_h = _jur(inc_code)
    biz_k, biz_h = _jur(biz_code)
    if inc_k == "cn" or biz_k == "cn":
        flags.append("CN-LINKED: "
                     + (f"business in {biz_h}" if biz_k == "cn"
                        else f"incorporated in {inc_h}")
                     + (f", incorporated in {inc_h}"
                        if biz_k == "cn" and inc_k in ("cn", "offshore") else ""))
    elif inc_k == "offshore":
        flags.append(f"OFFSHORE SHELL: incorporated in {inc_h}"
                     + (f", business in {biz_h or biz_code}"
                        if biz_k not in ("unknown",) and biz_code else ""))
    dil_12m = 0
    earliest = None
    for f, d in zip(forms, dates):
        try:
            fd = dt.date.fromisoformat(d)
        except Exception:
            continue
        earliest = fd if earliest is None or fd < earliest else earliest
        base = f.split("/")[0].upper()
        if (today - fd).days <= 365 and any(
                base.startswith(x) for x in DILUTIVE_FORMS):
            dil_12m += 1
    if dil_12m >= 3:
        flags.append(f"SERIAL DILUTER: {dil_12m} offering/registration "
                     "filings in 12mo")
    if rsplits_13m >= 1:
        flags.append(("SERIAL " if rsplits_13m >= 2 else "")
                     + f"REVERSE SPLIT{'S' if rsplits_13m > 1 else ''}: "
                     f"{rsplits_13m} in ~13mo")
    if float_sh and float_sh < 5e6:
        flags.append(f"MICRO FLOAT: ~{float_sh / 1e6:.1f}M shares")
    # "recent registrant" is only claimable when the (truncated) history we
    # see genuinely starts recently AND isn't truncated at the API cap
    if earliest and (today - earliest).days < 550 and len(forms) < 900:
        flags.append(f"FRESH REGISTRANT: first filing {earliest.isoformat()}")
    n = len(flags)
    grade = ("CLEAN RECORD" if n == 0
             else f"PUMP ANATOMY {n}/6" if n >= 3
             else f"{n} FLAG{'S' if n > 1 else ''}")
    return {"grade": grade, "n": n, "flags": flags,
            "jur": (f"{inc_h or inc_code or '?'} inc"
                    + (f" · biz {biz_h or biz_code}" if biz_code else ""))}


def pedigree(sym: str, sdir: str, now: dt.datetime, cache: dict,
             rsplits_13m: int = 0, float_sh: float | None = None,
             ttl_h: float = 168.0, log=None) -> dict:
    """EDGAR-backed pedigree for one ticker, cached a week (corporate
    anatomy changes slowly). Fail-open: any error -> UNKNOWN, cached briefly
    so a dead feed doesn't burn the budget every tick."""
    c = cache.get(sym)
    if c and time.time() - float(c.get("_t", 0)) < ttl_h * 3600:
        return c
    from .intel import _get, ensure_cik_map
    out = {"grade": "UNKNOWN", "n": 0, "flags": [], "jur": "",
           "_t": time.time() - (ttl_h - 1) * 3600}   # retry within ~1h
    try:
        cik = ensure_cik_map(sdir, log).get(sym.upper())
        if cik:
            js = json.loads(_get(
                f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                timeout=12))
            rec = (js.get("filings") or {}).get("recent") or {}
            biz = ((js.get("addresses") or {}).get("business") or {})
            out = grade_pedigree(
                js.get("stateOfIncorporation"), biz.get("stateOrCountry"),
                rec.get("form") or [], rec.get("filingDate") or [],
                now.date(), rsplits_13m=rsplits_13m, float_sh=float_sh)
            out["cik"] = cik
            out["_t"] = time.time()
        time.sleep(0.15)                 # stay far under SEC's 10 req/s
    except Exception as e:
        if log:
            log(f"pedigree {sym}: {e}")
    cache[sym] = out
    return out
