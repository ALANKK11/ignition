"""Composite scoring, driver attribution, and archetype tags."""
from __future__ import annotations

import numpy as np

from .util import clip01

COMPONENT_LABELS = {
    "rvol": "RVOL",
    "vol_trend": "VOL TREND",
    "atr_expansion": "ATR EXPAND",
    "squeeze": "COIL",
    "closing_strength": "CLOSE PIN",
    "breakout": "BREAKOUT",
    "afterhours": "AFTER HRS",
    "catalyst": "CATALYST",
    "short_fuel": "SHORT FUEL",
    "options_heat": "OPT HEAT",
}


def composite_score(components: dict, weights: dict) -> tuple[float, dict]:
    """Weighted mean over *available* components (None = not measured →
    excluded from numerator and denominator, so a name isn't punished for
    missing data). Returns (score_0_100, contributions)."""
    num = 0.0
    den = 0.0
    contrib: dict[str, float] = {}
    for key, w in weights.items():
        if w == 0:
            continue
        v = components.get(key)
        if v is None or not np.isfinite(v):
            continue
        num += w * v
        den += w
        contrib[key] = w * v
    score = clip01(num / den) * 100.0 if den > 0 else 0.0
    return score, contrib


def top_drivers(contrib: dict, n: int = 3) -> list[str]:
    ranked = sorted(contrib.items(), key=lambda kv: kv[1], reverse=True)
    return [k for k, v in ranked[:n] if v > 0.03]


def make_tags(components: dict, metrics: dict, extra: dict, mode: str) -> list[tuple[str, str]]:
    """Returns list of (tag_text, color) for the report."""
    tags: list[tuple[str, str]] = []
    ext_label = "PM" if mode == "premarket" else "AH"

    if (components.get("catalyst") or 0) >= 0.9:
        ed = extra.get("earnings_date")
        when = ed.strftime("%b %d") if ed else "SOON"
        tags.append((f"EARNINGS {when}", "bold magenta"))

    ah_ret = extra.get("ah_ret")
    if ah_ret is not None and (components.get("afterhours") or 0) >= 0.30:
        color = "bold green" if ah_ret > 0 else "bold red"
        tags.append((f"{ext_label} {ah_ret * 100:+.1f}%", color))

    if metrics["rvol"] >= 3.0:
        tags.append((f"IGNITED {metrics['rvol']:.1f}x", "bold yellow"))

    if (components.get("squeeze") or 0) >= 0.78:
        tags.append(("COILED", "cyan"))
    if metrics.get("nr7"):
        tags.append(("NR7", "cyan"))

    if (components.get("short_fuel") or 0) >= 0.55:
        spf = extra.get("short_pct_float")
        txt = f"SQUEEZE {spf * 100:.0f}%SI" if spf else "SQUEEZE FUEL"
        tags.append((txt, "bold red"))

    if (components.get("breakout") or 0) >= 0.9:
        tags.append(("20D HIGH" if metrics["prox_hi"] >= metrics["prox_lo"] else "20D LOW",
                     "green" if metrics["prox_hi"] >= metrics["prox_lo"] else "red"))

    if (components.get("options_heat") or 0) >= 0.6:
        iv = extra.get("atm_iv")
        tags.append((f"IV {iv * 100:.0f}%" if iv else "HOT OPTIONS", "yellow"))

    if abs(metrics.get("streak", 0)) >= 4:
        s = metrics["streak"]
        tags.append((f"{abs(s)}D {'UP' if s > 0 else 'DOWN'}", "green" if s > 0 else "red"))

    return tags[:4]
