"""Split-aware ADV — HANDOFF item 30.

A split rescales the share unit mid-window. After a 1:10 reverse split
today's volume prints in tenths of the old unit, so a raw 20-day mean mixes
units and overstates share ADV ~10x; every ratio built on it (pace = v/ADV,
rvol, the radar's participation read) then reads ~0.1x reality and the name
goes structurally invisible — INLF. Dollar volume is split-invariant, but
pace/rvol/radar all run on SHARES, so shares must be converted.

Two layers, merged per name:

  AUTHORITATIVE  Alpaca corporate-actions over the baseline window
                 (`AlpacaData.splits_range`) → [(ex_date_iso, factor)],
                 factor = old-shares-per-new-share (10.0 for a 1:10
                 reverse, 0.25 for a 4:1 forward). Endpoint down → None,
                 heuristic carries on alone (fail-open).
  HEURISTIC      a close-over-close jump within 2% of a clean split ratio
                 WITHOUT the volume a real move that size always brings.
                 A CPHI-class double prints on 10-50x tape; a split prints
                 on ordinary tape — the guard is rvol < 3x that day.

`adjusted_volumes()` converts every pre-split bar into TODAY's share unit;
`split_aware_adv()` is the drop-in replacement for a raw volume mean.
"""
from __future__ import annotations

import numpy as np

# mirror the ratio family used by the ext-sweep gap guard (flow_alpaca)
SPLIT_RATIOS = [2, 3, 4, 5, 8, 10, 15, 20, 25, 40, 50, 100]
_TOL = 0.02
_RVOL_GUARD = 3.0     # a "split" on >=3x normal tape is a real move — keep it


def clean_ratio(x: float) -> float | None:
    """Snap a close-over-close ratio to a clean split factor, else None.
    Returns old-shares-per-new-share: 10.0 for a 1:10 reverse (price x10),
    0.25 for a 4:1 forward (price /4)."""
    if not np.isfinite(x) or x <= 0:
        return None
    for r in SPLIT_RATIOS:
        if abs(x - r) / r < _TOL:
            return float(r)
        if abs(x - 1.0 / r) * r < _TOL:
            return 1.0 / r
    return None


def detect_split_events(closes, volumes) -> list[tuple[int, float]]:
    """Heuristic pass over daily bars: [(i, factor)] where i is the FIRST
    bar in the new share unit."""
    c = np.asarray(closes, float)
    v = np.asarray(volumes, float)
    out: list[tuple[int, float]] = []
    for i in range(1, len(c)):
        if c[i - 1] <= 0 or not np.isfinite(c[i]):
            continue
        f = clean_ratio(c[i] / c[i - 1])
        if f is None:
            continue
        base = v[max(0, i - 20):i]
        base_m = float(base.mean()) if len(base) else 0.0
        # v[i] is in the NEW unit; compare in old-unit terms (x factor)
        if base_m > 0 and (v[i] * f) / base_m >= _RVOL_GUARD:
            continue                       # real move on real tape — not a split
        out.append((i, f))
    return out


def adjusted_volumes(closes, volumes, dates=None,
                     known: list[tuple[str, float]] | None = None) -> np.ndarray:
    """Share volumes converted into the LAST bar's share unit.

    `known` = authoritative [(ex_date_iso, factor)] from corporate actions;
    `dates` = per-bar iso dates (required to place known events). Heuristic
    events are added only when not already explained by a known one."""
    v = np.asarray(volumes, float).copy()
    if len(v) < 2:
        return v
    events: list[tuple[int, float]] = []
    known_idx: set[int] = set()
    if known and dates is not None:
        ds = list(dates)
        for ex, f in known:
            if not f or f <= 0:
                continue
            # first bar on/after the ex-date trades in the new unit
            i = next((k for k, d in enumerate(ds) if str(d) >= str(ex)), None)
            if i and i > 0:
                events.append((i, float(f)))
                known_idx.add(i)
    for i, f in detect_split_events(closes, v):
        if any(abs(i - k) <= 1 for k in known_idx):
            continue
        events.append((i, f))
    for i, f in events:
        v[:i] = v[:i] / f
    return v


def split_aware_adv(closes, volumes, n: int = 20, dates=None,
                    known: list[tuple[str, float]] | None = None,
                    exclude_last: bool = False) -> float | None:
    """Mean share volume over the last `n` bars, all in today's share unit."""
    v = adjusted_volumes(closes, volumes, dates=dates, known=known)
    tail = v[-(n + 1):-1] if exclude_last else v[-n:]
    if not len(tail):
        return None
    m = float(tail.mean())
    return m if m > 0 else None
