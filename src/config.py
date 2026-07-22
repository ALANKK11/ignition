"""Configuration: defaults, YAML overrides, data-dir paths."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "universe": {
        "seed_file": "universe_seed.txt",
        "watchlist_file": "watchlist.txt",
        "use_screeners": True,
        "screeners": [
            "most_actives",
            "day_gainers",
            "day_losers",
            "small_cap_gainers",
            "most_shorted_stocks",
        ],
        "min_price": 1.0,
        "min_dollar_volume": 5_000_000,   # 20d avg $ volume floor
        "max_universe": 400,
    },
    "scan": {
        "history_days": 130,      # trading-day lookback target
        "enrich_top": 60,         # names that get the expensive second pass
        "show_top": 25,           # rows printed in the report
        "control_sample": 25,     # random non-picks journaled for self-audit baseline
        "workers": 6,             # enrichment thread pool
    },
    "weights": {
        "rvol": 2.2,              # today's volume vs 20d average (log-scaled)
        "vol_trend": 0.7,         # volume z-score vs own 20d distribution
        "atr_expansion": 1.1,     # ATR(5)/ATR(20) - range regime shifting up
        "squeeze": 1.0,           # Bollinger-width percentile compression (+NR7)
        "closing_strength": 0.6,  # close pinned at high OR low of day
        "breakout": 0.9,          # pressing 20d high / breaking 20d low
        "afterhours": 2.4,        # extended-hours move, volume-weighted
        "catalyst": 2.2,          # earnings tonight/tomorrow, ex-div
        "short_fuel": 1.1,        # short % float + days-to-cover, momentum-gated
        "options_heat": 1.0,      # ATM IV level + IV vs realized (variance premium)
    },
    "flow": {
        "window_min": 15, "refresh_sec": 75, "monitor_cap": 60,
        "fade_ratio": 0.35, "fade_peak_tp": 2.0, "hod_prox": 0.03,
        "ignite_tp": 2.8, "accel": 1.6, "newmoney_tp": 2.0,
        "run_tp": 1.8, "churn_tp": 2.2, "churn_progress": 0.2,
        "ext_gap_min": 0.15, "ext_min_price": 0.10,
        "ext_min_dollar": 50000, "ext_min_shares": 2500,
        "ext_min_minutes": 5, "ext_min_frac_adv": 0.005,
        "ext_ghost_adv_dollar": 10000, "ext_top": 20,
    },
    "thresholds": {
        "ah_move_full": 0.08,     # |AH move| that earns full component score
        "ah_vol_conf": 0.02,      # AH volume as fraction of ADV for full confidence
        "rvol_log2_full": 3.0,    # log2(rvol) mapped to 1.0 at 8x volume
        "short_pct_full": 0.20,   # short % float that saturates the fuel score
        "dtc_full": 10.0,         # days-to-cover that saturates
    },
}


def data_dir() -> Path:
    d = Path(os.environ.get("IGNITION_HOME", Path.home() / ".ignition"))
    (d / "cache").mkdir(parents=True, exist_ok=True)
    return d


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | None = None) -> dict:
    cfg_path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    user = {}
    if cfg_path.exists():
        with open(cfg_path) as f:
            user = yaml.safe_load(f) or {}
    cfg = _deep_merge(DEFAULTS, user)
    cfg["_paths"] = {
        "root": str(PROJECT_ROOT),
        "config": str(cfg_path),
        "data": str(data_dir()),
        "journal": str(data_dir() / "journal.db"),
        "cache": str(data_dir() / "cache"),
    }
    return cfg


def read_ticker_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip().upper()
        if not line:
            continue
        for tok in line.replace(",", " ").split():
            if tok and all(c.isalnum() or c in ".-" for c in tok):
                out.append(tok)
    return out
