"""Terminal report (rich) + exports (CSV / JSON / TradingView watchlist)."""
from __future__ import annotations

import csv
import json

import numpy as np
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .scoring import COMPONENT_LABELS
from .util import fmt_big

BANNER = r"""
  ___ ____ _   _ ___ _____ ___ ___  _   _
 |_ _/ ___| \ | |_ _|_   _|_ _/ _ \| \ | |
  | | |  _|  \| || |  | |  | | | | |  \| |
  | | |_| | |\  || |  | |  | | |_| | |\  |
 |___\____|_| \_|___| |_| |___\___/|_| \_|
"""


def score_style(s: float) -> str:
    if s >= 55:          # rare multi-signal confluence — the real ones
        return "bold red"
    if s >= 42:
        return "bold yellow"
    if s >= 30:
        return "yellow"
    return "dim"


def score_bar(s: float) -> Text:
    filled = max(0, min(10, int(round(s / 100 * 10))))
    t = Text()
    t.append("█" * filled, style=score_style(s))
    t.append("░" * (10 - filled), style="grey23")
    t.append(f" {s:4.1f}", style=score_style(s))
    return t


def _pct_text(x, bold_at=0.03) -> Text:
    if x is None or not np.isfinite(x):
        return Text("  --", style="dim")
    style = "green" if x > 0 else ("red" if x < 0 else "dim")
    if abs(x) >= bold_at:
        style = "bold " + style
    return Text(f"{x * 100:+.1f}%", style=style)


def render_scan(console: Console, rows: list[dict], meta: dict):
    mode = meta["mode"]
    ext_label = "PM%" if mode == "premarket" else "AH%"
    header = Text.assemble(
        ("IGNITION", "bold red"), ("  ·  find tomorrow's tape, tonight\n", "dim"),
        (f"session scanned  ", "dim"), (str(meta["trade_date"]), "bold"),
        (f"   →   targeting  ", "dim"), (str(meta["target_date"]), "bold cyan"),
        (f"\nmode ", "dim"), (mode, "bold"),
        ("   universe ", "dim"), (str(meta["universe_size"]), "bold"),
        ("   scored ", "dim"), (str(meta["scored"]), "bold"),
        ("   enriched ", "dim"), (str(meta["enriched"]), "bold"),
        ("   provider ", "dim"), (meta["provider"], "bold"),
    )
    console.print(Panel(header, box=box.HEAVY, border_style="red", padding=(0, 2)))

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold", expand=False,
                  pad_edge=False, padding=(0, 1))
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("TICKER", style="bold")
    table.add_column("IGNITION", justify="left", min_width=18)
    table.add_column("CLOSE", justify="right")
    table.add_column("DAY%", justify="right")
    table.add_column(ext_label, justify="right")
    table.add_column("RVOL", justify="right")
    table.add_column("$ADV", justify="right", style="dim")
    table.add_column("WHY", overflow="fold", max_width=46)

    for i, r in enumerate(rows, 1):
        m, x = r["metrics"], r["extra"]
        why = Text()
        for j, (txt, color) in enumerate(r["tags"]):
            if j:
                why.append("  ")
            why.append(txt, style=color)
        drv = [COMPONENT_LABELS.get(d, d) for d in r["drivers"] if d]
        if drv:
            if len(r["tags"]):
                why.append("\n")
            why.append(" · ".join(drv), style="dim")
        rvol_style = "bold yellow" if m["rvol"] >= 3 else ("yellow" if m["rvol"] >= 1.8 else "")
        table.add_row(
            str(i),
            r["ticker"],
            score_bar(r["score"]),
            f"{m['close']:,.2f}",
            _pct_text(m["ret1"]),
            _pct_text(x.get("ah_ret")),
            Text(f"{m['rvol']:.1f}x", style=rvol_style),
            fmt_big(m["dollar_adv"]),
            why,
        )
    console.print(table)
    console.print(
        "[dim]score = weighted blend of: RVOL · vol trend · ATR expansion · coil/NR7 · "
        "close pin · breakout · after-hours tape · catalysts · short fuel · options heat.\n"
        "Ignition finds [/dim][bold]activity[/bold][dim], not direction — a top rank means "
        "expect volume and range, not necessarily green.[/dim]"
    )


def render_eval(console: Console, scan_meta: dict, grade: dict):
    ic = grade["ic"]
    ic_style = "bold green" if ic == ic and ic > 0.15 else ("yellow" if ic == ic and ic > 0 else "bold red")
    er, eg = grade["edge_rvol"], grade["edge_range"]

    head = Text.assemble(
        ("SELF-AUDIT  ", "bold"), (f"scan of {scan_meta['trade_date']}", "cyan"),
        (f"  ({scan_meta['mode']})\n", "dim"),
        ("rank IC ", "dim"), (f"{ic:+.2f}" if ic == ic else " n/a", ic_style),
        ("    edge vs random control:  RVOL ", "dim"),
        (f"{er:.2f}x" if er == er else "n/a", "bold" if er == er and er > 1.2 else ""),
        ("   range ", "dim"),
        (f"{eg:.2f}x" if eg == eg else "n/a", "bold" if eg == eg and eg > 1.2 else ""),
        (f"    graded {grade['n']} names", "dim"),
    )
    console.print(Panel(head, box=box.HEAVY, border_style="cyan", padding=(0, 2)))

    t = Table(box=box.SIMPLE, header_style="bold", padding=(0, 1))
    for col, j in (("RANK", "right"), ("TICKER", "left"), ("SCORE", "right"),
                   ("NEXT RVOL", "right"), ("NEXT RANGE", "right"),
                   ("GAP", "right"), ("NEXT RET", "right")):
        t.add_column(col, justify=j)
    for _, r in grade["top"].iterrows():
        t.add_row(
            str(int(r["rank"])), r["ticker"], f"{r['score']:.1f}",
            Text(f"{r['next_rvol']:.1f}x",
                 style="bold yellow" if r["next_rvol"] >= 2.5 else ""),
            Text(f"{r['next_range_pct']:.1f}%",
                 style="bold" if r["next_range_pct"] >= 6 else ""),
            _pct_text(r["next_gap_pct"] / 100, 0.03),
            _pct_text(r["next_ret_pct"] / 100, 0.05),
        )
    console.print(t)
    if grade["ctrl_mean_rvol"] == grade["ctrl_mean_rvol"]:
        console.print(
            f"[dim]random-control baseline: RVOL {grade['ctrl_mean_rvol']:.2f}x · "
            f"range {grade['ctrl_mean_range']:.1f}%[/dim]"
        )


def render_history(console: Console, hist):
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold", title="scan history")
    for col in ("DATE", "MODE", "PICKS", "UNIVERSE", "EVAL", "IC", "EDGE(RVOL)"):
        t.add_column(col, justify="right" if col not in ("DATE", "MODE") else "left")
    ics = []
    for _, r in hist.iterrows():
        ic = r["ic"]
        if ic == ic:
            ics.append(ic)
        t.add_row(
            str(r["trade_date"]), r["mode"], str(int(r["n_picks"])),
            str(int(r["universe_size"] or 0)),
            "✓" if r["evaluated"] else "·",
            f"{ic:+.2f}" if ic == ic else "--",
            f"{r['edge_rvol']:.2f}x" if r["edge_rvol"] == r["edge_rvol"] else "--",
        )
    console.print(t)
    if ics:
        mean_ic = float(np.mean(ics))
        hit = float(np.mean([1 if x > 0 else 0 for x in ics]))
        style = "bold green" if mean_ic > 0.1 else ("yellow" if mean_ic > 0 else "bold red")
        console.print(
            Text.assemble(("rolling mean IC ", "dim"), (f"{mean_ic:+.2f}", style),
                          (f"   positive-IC days {hit * 100:.0f}%   "
                           f"({len(ics)} evaluated scans)", "dim")))


# -- exports ----------------------------------------------------------------

def export_csv(path: str, rows: list[dict]):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "ticker", "score", "close", "day_pct", "ext_pct", "rvol",
                    "dollar_adv", "tags", "drivers"])
        for i, r in enumerate(rows, 1):
            m, x = r["metrics"], r["extra"]
            w.writerow([i, r["ticker"], f"{r['score']:.1f}", f"{m['close']:.2f}",
                        f"{m['ret1'] * 100:.2f}",
                        f"{(x.get('ah_ret') or 0) * 100:.2f}",
                        f"{m['rvol']:.2f}", f"{m['dollar_adv']:.0f}",
                        " | ".join(t for t, _ in r["tags"]),
                        " | ".join(r["drivers"])])


def export_json(path: str, rows: list[dict], meta: dict):
    def clean(d):
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                for k, v in d.items() if not k.startswith("_")}
    payload = {
        "meta": clean(meta),
        "rows": [{
            "rank": i, "ticker": r["ticker"], "score": round(r["score"], 2),
            "components": {k: round(v, 4) for k, v in r["components"].items()
                           if v is not None},
            "metrics": {k: (v.isoformat() if hasattr(v, "isoformat") else
                            (round(v, 6) if isinstance(v, float) else v))
                        for k, v in r["metrics"].items()},
            "extra": {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                      for k, v in r["extra"].items()},
            "tags": [t for t, _ in r["tags"]],
        } for i, r in enumerate(rows, 1)],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)


def export_tradingview(path: str, rows: list[dict]):
    with open(path, "w") as f:
        f.write(",".join(r["ticker"] for r in rows))


# ---------------------------------------------------------------------------
# FLOW board
# ---------------------------------------------------------------------------
STATE_STYLE = {
    "IGNITING": "bold red", "NEW MONEY": "bold magenta", "RUNNING": "yellow",
    "CHURN": "cyan", "FADING": "bold rgb(255,135,0)", "LEAVING": "red",
    "COOLING": "dim yellow", "QUIET": "dim", "PM HOT": "bold green", "OPEN DRIVE": "bold yellow",
    "PRE-OPEN": "dim",
}


def render_flow(console: Console, now, rows: list[dict], events: list[dict],
                rot_in: list[dict], rot_out: list[dict], provider: str,
                window_min: int, clear: bool = False) -> None:
    if clear:
        console.clear()
    hdr = Text()
    hdr.append("IGNITION FLOW", style="bold red")
    hdr.append(f"  ·  where the money is, right now", style="dim")
    hdr.append(f"\n{now.strftime('%a %Y-%m-%d %H:%M ET')}", style="bold")
    hdr.append(f"   window {window_min}m   monitored {len(rows)}   "
               f"provider {provider}", style="dim")
    console.print(Panel(hdr, border_style="red", expand=True))

    tb = Table(box=box.SIMPLE_HEAVY, expand=False, pad_edge=False,
               header_style="bold")
    for col, j in [("TICKER", "left"), ("LAST", "right"), ("DAY%", "right"),
                   ("vsVWAP", "right"), ("PACE", "right"), (f"{window_min}m", "right"),
                   ("FLOW$", "right"), ("ΔSHARE", "right"), ("STATE", "left"),
                   ("READ", "left")]:
        tb.add_column(col, justify=j, max_width=44 if col == "READ" else None)
    for r in rows:
        st = r["state"]
        sty = STATE_STYLE.get(st, "")
        tb.add_row(
            Text(r["ticker"], style="bold"),
            f"{r['last']:.2f}",
            _pct_text(r["day_pct"], bold_at=0.05),
            _pct_text(r["vs_vwap"], bold_at=0.02) if r["vs_vwap"] is not None else Text("--", style="dim"),
            Text(f"{r['pace']:.1f}x", style="bold yellow" if (r["pace"] or 0) >= 2 else "")
            if r["pace"] is not None else Text("--", style="dim"),
            Text(f"{r['tp']:.1f}x", style="bold yellow" if (r["tp"] or 0) >= 2 else "")
            if r["tp"] is not None else Text("--", style="dim"),
            Text(fmt_big(r["dollar_w"]) if r["dollar_w"] else
                 (fmt_big(r["pm_dollar"]) + " pm" if r["pm_dollar"] else "--"), style="dim"),
            _pct_text(r["dshare"], bold_at=0.03) if r["tp"] is not None else Text("--", style="dim"),
            Text(st, style=sty),
            Text(r["note"], style="dim"),
        )
    console.print(tb)

    if rot_in or rot_out:
        line = Text("ROTATION ", style="bold")
        line.append(f"(last {window_min}m, vs each name's normal flow)   ", style="dim")
        line.append("IN → ", style="bold magenta")
        line.append("  ".join(f"{r['ticker']} {r['surge']:.1f}x its norm"
                              for r in rot_in) or "—")
        line.append("     OUT → ", style="bold red")
        line.append("  ".join(f"{r['ticker']} {r['tp']/max(r['peak_tp'],.1):.0%} of peak"
                              for r in rot_out) or "—")
        console.print(Panel(line, border_style="grey37", expand=True))

    for ev in events:
        t = Text(f"  {ev['ts'].strftime('%H:%M')}  ", style="dim")
        t.append(f"{ev['ticker']:<6}", style="bold")
        t.append(f"{ev['prev'] or '—'} → ", style="dim")
        t.append(ev["state"], style=STATE_STYLE.get(ev["state"], "bold"))
        t.append(f"   {ev['note']}", style="dim")
        console.print(t)
