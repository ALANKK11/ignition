"""Candidate universe construction.

Funnel: static liquid seed list + user watchlist + live screeners
(most-actives / gainers / losers / small-cap gainers / most-shorted), deduped.
The expensive filtering (price, dollar volume) happens after the daily
download, so this stage just assembles candidates.
"""
from __future__ import annotations

from pathlib import Path

from .config import read_ticker_file
from .providers import Provider


def build_universe(cfg: dict, provider: Provider, console=None) -> tuple[list[str], list[str]]:
    ucfg = cfg["universe"]
    root = Path(cfg["_paths"]["root"])

    seed = read_ticker_file(root / ucfg["seed_file"])
    watch = read_ticker_file(root / ucfg["watchlist_file"])

    screened: list[str] = []
    if ucfg.get("use_screeners", True):
        for key in ucfg.get("screeners", []):
            syms = provider.screen(key, limit=100)
            if console:
                console.log(f"screener [bold]{key}[/bold]: {len(syms)} symbols")
            screened.extend(syms)

    ordered: list[str] = []
    seen = set()
    for src in (watch, screened, seed):
        for t in src:
            if t not in seen:
                seen.add(t)
                ordered.append(t)

    cap = int(ucfg.get("max_universe", 400))
    return ordered[:cap], watch
