"""
Catalyst lane — the press wires, polled raw.

CPHI-class ignitions are born as press releases, not prints. The wires
publish free RSS; the release timestamp IS t=0 of the move. This module
polls the feeds, pulls tickers straight out of headlines (validated against
the live Alpaca symbol master so junk can't leak), scores the catalyst, and
hands events to the live shift, which puts the name on the board with the
headline attached before the volume has finished confirming.

Feed URLs live in config.yaml (news.feeds) — wires occasionally move their
RSS endpoints, so dead feeds are skipped, reported once per shift, and
editable without touching code.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

from .util import NY

SYM_RE = re.compile(
    r"\b(?:NASDAQ|Nasdaq|NYSE(?:\s+American)?|AMEX|CBOE|OTC(?:QB|QX)?)"
    r"\s*[:\-]\s*\"?\s*([A-Z]{1,5})\b")

KEYWORDS = {
    "merger": 5, "acquisition": 5, "acquire": 5, "to be acquired": 6,
    "fda": 5, "approval": 4, "clearance": 4, "breakthrough": 3,
    "contract": 4, "purchase order": 5, "order": 2, "award": 3,
    "partnership": 4, "collaboration": 3, "strategic": 2, "agreement": 2,
    "artificial intelligence": 3, " ai ": 3, "ai-powered": 3,
    "bitcoin": 3, "crypto": 3, "blockchain": 2, "treasury strategy": 4,
    "uplisting": 4, "uplist": 4, "record revenue": 3, "launch": 2,
    "patent": 2, "wins": 2, "granted": 2, "phase 3": 3, "phase 2": 2,
}
NEG = {"offering": -4, "pricing of": -3, "reverse split": -5,
       "dilution": -4, "warrant": -2, "compliance": -2, "deficiency": -3}


def _hash(title: str) -> str:
    return hashlib.sha1(title.encode()).hexdigest()[:10]


def score_text(text: str) -> tuple[int, list[str]]:
    t = " " + text.lower() + " "
    score, flags = 0, []
    for k, w in KEYWORDS.items():
        if k in t:
            score += w
            flags.append(k.strip().upper())
    for k, w in NEG.items():
        if k in t:
            score += w
            flags.append("⚠" + k.strip().upper())
    return score, flags[:4]


def poll(feeds: list[str], seen: set[str], valid: set[str],
         exclude: set[str], min_score: int = 2,
         log=lambda m: None) -> list[dict]:
    """One pass over all feeds. Returns NEW catalyst events only; mutates
    `seen` with every processed item so nothing repeats within a shift."""
    events: list[dict] = []
    for url in feeds:
        try:
            r = requests.get(url, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0 (ignition)"})
            if r.status_code != 200:
                log(f"newswire: {url.split('/')[2]} → HTTP {r.status_code}")
                continue
            root = ET.fromstring(r.content)
        except Exception as e:
            log(f"newswire: {url.split('/')[2]} unreadable ({type(e).__name__})")
            continue
        for item in root.iter("item"):
            try:
                title = (item.findtext("title") or "").strip()
                desc = (item.findtext("description") or "")[:600]
                if not title:
                    continue
                key = _hash(title)
                if key in seen:
                    continue
                seen.add(key)
                text = f"{title} {desc}"
                syms = {s for s in SYM_RE.findall(text)
                        if s in valid and s not in exclude}
                if not syms:
                    continue
                sc, flags = score_text(text)
                if -3 < sc < min_score:
                    continue         # weak either way; strong negatives (offerings,
                                     # reverse splits) are tradable dumps — keep them
                pub = item.findtext("pubDate")
                try:
                    ts = parsedate_to_datetime(pub).astimezone(NY)
                except Exception:
                    ts = dt.datetime.now(NY)
                if (dt.datetime.now(NY) - ts) > dt.timedelta(hours=20):
                    continue                     # stale reprints aren't catalysts
                for s in syms:
                    events.append({"symbol": s, "headline": title[:140],
                                   "ts": ts, "score": sc, "flags": flags,
                                   "link": (item.findtext("link") or "")[:300]})
            except Exception:
                continue
    events.sort(key=lambda e: e["score"], reverse=True)
    return events
