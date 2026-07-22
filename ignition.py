#!/usr/bin/env python3
"""IGNITION — pre-session volume & volatility scanner.

    python ignition.py scan               # evening scout (run ~6-9pm ET)
    python ignition.py scan --premarket   # morning confirm (run ~7-9am ET)
    python ignition.py scan --demo        # offline synthetic market
    python ignition.py eval               # grade past scans vs what happened
    python ignition.py history            # rolling IC / edge across scans
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console

from pathlib import Path
from src import flow as flow_mod
from src import report
from src.config import load_config, read_ticker_file
from src.journal import Journal
from src.report import (export_csv, export_json, export_tradingview,
                        render_eval, render_history, render_scan)
from src.scoring import composite_score, make_tags, top_drivers
from src.signals import (afterhours_component, base_components, base_metrics_ok,
                         catalyst_component, compute_base_metrics,
                         options_heat_component, short_fuel_component)
from src.universe import build_universe
from src.util import NY, next_trading_day, ny_now, ny_today

console = Console()


def get_provider(demo: bool, include_next: bool = False):
    if demo:
        from src.demo import DemoProvider
        return DemoProvider(include_next=include_next)
    from src.providers import LiveProvider
    return LiveProvider()


# ---------------------------------------------------------------------------


def enrich_one(provider, ticker, metrics, th, mode):
    """Second-pass signals for one ticker. Exception-safe."""
    comp, extra = {}, {}
    try:
        intra = provider.get_intraday_1m(ticker)
        comp["afterhours"], ah_info = afterhours_component(intra, metrics, th, mode)
        extra.update(ah_info)
    except Exception:
        comp["afterhours"] = None
    try:
        ed = provider.get_next_earnings(ticker)
        comp["catalyst"], cat_info = catalyst_component(ed, metrics["last_bar_date"], mode)
        extra.update(cat_info)
    except Exception:
        comp["catalyst"] = None
    try:
        ss = provider.get_short_stats(ticker)
        comp["short_fuel"], s_info = short_fuel_component(ss, metrics, th)
        extra.update(s_info)
    except Exception:
        comp["short_fuel"] = None
    try:
        iv = provider.get_atm_iv(ticker, metrics["close"])
        comp["options_heat"], o_info = options_heat_component(iv, metrics)
        extra.update(o_info)
    except Exception:
        comp["options_heat"] = None
    if provider.name == "live":
        time.sleep(0.05)
    return ticker, comp, extra



def _state_dir(cfg) -> str:
    d = os.path.join(cfg["_paths"]["data"], "state")
    os.makedirs(d, exist_ok=True)
    return d


def _dump_scan_state(cfg, meta, show, mode):
    ext_key = "pm_ret" if mode == "premarket" else "ah_ret"
    rows = []
    for i, r in enumerate(show, 1):
        m, e = r["metrics"], r["extra"]
        rows.append({
            "rank": i, "ticker": r["ticker"], "score": round(r["score"], 1),
            "close": round(m["close"], 2), "day_pct": round(m["ret1"], 4),
            "ext_pct": (round(e[ext_key], 4) if e.get(ext_key) is not None else None),
            "rvol": round(m["rvol"], 2), "dollar_adv": m["dollar_adv"],
            "tags": [[t, c] for t, c in r["tags"]], "drivers": r["drivers"],
        })
    payload = {"ts": meta["generated_at"], "trade_date": str(meta["trade_date"]),
               "target_date": str(meta["target_date"]), "mode": mode,
               "provider": meta["provider"], "universe": meta["universe_size"],
               "scored": meta["scored"], "ext_label": "PM" if mode == "premarket" else "AH",
               "rows": rows}
    with open(os.path.join(_state_dir(cfg), "latest_scan.json"), "w") as f:
        json.dump(payload, f)


def _dump_flow_state(cfg, now, rows, ri, ro, provider, window):
    payload = {"ts": now.isoformat(timespec="seconds"), "provider": provider,
               "window_min": window,
               "rows": [{k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in r.items()
                         if k in ("ticker", "last", "day_pct", "vs_vwap", "pace",
                                  "tp", "dollar_w", "dshare", "surge", "state",
                                  "note", "peak_tp")} for r in rows],
               "rot_in": [{"ticker": r["ticker"], "surge": round(r["surge"], 2)}
                          for r in ri],
               "rot_out": [{"ticker": r["ticker"],
                            "frac": round(r["tp"] / max(r["peak_tp"], .1), 3)}
                           for r in ro]}
    with open(os.path.join(_state_dir(cfg), "latest_flow.json"), "w") as f:
        json.dump(payload, f)


def _attach_finnhub(provider, console, demo):
    if demo:
        return
    from src.providers_finnhub import earnings_map, key
    k = key()
    if not k:
        return
    m = earnings_map(k, ny_today(), 6)
    if m is not None:
        provider.earnings_map = m
        console.print(f"[dim]finnhub earnings calendar: {len(m)} symbols in window[/dim]")
    else:
        console.print("[dim]finnhub unavailable — using yahoo per-ticker earnings[/dim]")


def cmd_scan(args):
    cfg = load_config(args.config)
    th = cfg["thresholds"]
    weights = dict(cfg["weights"])
    provider = get_provider(args.demo)
    _attach_finnhub(provider, console, args.demo)

    with console.status("[bold]building candidate universe…"):
        universe, watchlist = build_universe(cfg, provider)
    if not universe:
        console.print("[bold red]No candidates — check universe_seed.txt / network.")
        sys.exit(1)
    console.print(f"[dim]universe candidates:[/dim] [bold]{len(universe)}[/bold]")

    with console.status(f"[bold]downloading daily history for {len(universe)} names…"):
        hist = provider.get_history(universe, days=cfg["scan"]["history_days"])
    if not hist:
        console.print("[bold red]No market data returned. If you're offline, try --demo.")
        sys.exit(1)
    console.print(f"[dim]history loaded:[/dim] [bold]{len(hist)}[/bold] tickers")

    # ---- pass 1: base metrics + filters + base score ----------------------
    ucfg = cfg["universe"]
    candidates = {}
    for t, df in hist.items():
        m = compute_base_metrics(df)
        if m is None:
            continue
        if not base_metrics_ok(m, min_price=ucfg["min_price"],
                               min_dollar_volume=ucfg["min_dollar_volume"],
                               is_watchlist=(t in watchlist)):
            continue
        comps = base_components(m, th)
        score, contrib = composite_score(comps, weights)
        candidates[t] = {"ticker": t, "metrics": m, "components": comps,
                         "score": score, "contrib": contrib, "extra": {}}
    if not candidates:
        console.print("[bold red]Nothing passed the liquidity filters.")
        sys.exit(1)

    last_dates = [c["metrics"]["last_bar_date"] for c in candidates.values()]
    trade_date = max(set(last_dates), key=last_dates.count)
    if args.demo:
        mode = "premarket" if args.premarket else "evening"
    else:
        mode = "premarket" if (args.premarket or trade_date < ny_today()) else "evening"
    target_date = trade_date if mode == "premarket" and trade_date >= ny_today() \
        else next_trading_day(trade_date)

    ranked = sorted(candidates.values(), key=lambda r: r["score"], reverse=True)

    # ---- pass 2: enrichment on the funnel top ----------------------------
    n_enrich = 0
    if not args.fast:
        enrich_n = args.enrich or cfg["scan"]["enrich_top"]
        targets = [r["ticker"] for r in ranked[:enrich_n]]
        for w in watchlist:            # your own list always gets the full pass
            if w in candidates and w not in targets:
                targets.append(w)
        with console.status(f"[bold]enrichment pass on {len(targets)} names "
                            "(after-hours tape · earnings · shorts · options)…"):
            workers = 1 if args.demo else cfg["scan"]["workers"]
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = [ex.submit(enrich_one, provider, t, candidates[t]["metrics"],
                                  th, mode) for t in targets]
                for fut in as_completed(futs):
                    try:
                        t, comp, extra = fut.result()
                    except Exception:
                        continue
                    candidates[t]["components"].update(comp)
                    candidates[t]["extra"].update(extra)
                    n_enrich += 1
        for t in targets:
            r = candidates[t]
            r["score"], r["contrib"] = composite_score(r["components"], weights)

    ranked = sorted(candidates.values(), key=lambda r: r["score"], reverse=True)
    for r in ranked:
        r["drivers"] = top_drivers(r["contrib"])
        r["tags"] = make_tags(r["components"], r["metrics"], r["extra"], mode)

    show = ranked[: args.top or cfg["scan"]["show_top"]]
    meta = {
        "trade_date": trade_date, "target_date": target_date, "mode": mode,
        "universe_size": len(universe), "scored": len(candidates),
        "enriched": n_enrich, "provider": provider.name,
        "generated_at": ny_now().isoformat(timespec="seconds"),
    }
    render_scan(console, show, meta)
    _dump_scan_state(cfg, meta, show, mode)

    # ---- journal: picks + random control sample --------------------------
    if not args.no_journal:
        jr = Journal(cfg["_paths"]["journal"])
        n_pick = 60 if len(ranked) >= 120 else max(10, len(ranked) // 3)
        pick_rows = [{
            "ticker": r["ticker"], "rank": i + 1, "score": r["score"],
            "close": r["metrics"]["close"], "adv20": r["metrics"]["adv20"],
            "atr20_pct": r["metrics"]["atr20_pct"],
            "components": {k: v for k, v in r["components"].items() if v is not None},
        } for i, r in enumerate(ranked[:n_pick])]
        pool = ranked[n_pick:]
        rng = random.Random(str(trade_date))
        ctrl = rng.sample(pool, min(cfg["scan"]["control_sample"], len(pool))) if pool else []
        ctrl_rows = [{"ticker": r["ticker"], "rank": None, "score": r["score"],
                      "close": r["metrics"]["close"], "adv20": r["metrics"]["adv20"],
                      "atr20_pct": r["metrics"]["atr20_pct"], "components": {}}
                     for r in ctrl]
        jr.record_scan(trade_date, mode, args.demo, len(universe), pick_rows, ctrl_rows)
        console.print(f"[dim]journaled {len(pick_rows)} picks + {len(ctrl_rows)} controls "
                      f"→ run [bold]eval[/bold] after the next close to grade this scan.[/dim]")

    # ---- exports ---------------------------------------------------------
    if args.csv:
        export_csv(args.csv, show)
        console.print(f"[dim]csv → {args.csv}[/dim]")
    if args.json:
        export_json(args.json, show, meta)
        console.print(f"[dim]json → {args.json}[/dim]")
    if args.tv:
        export_tradingview(args.tv, show)
        console.print(f"[dim]tradingview watchlist → {args.tv}[/dim]")


def cmd_eval(args):
    cfg = load_config(args.config)
    jr = Journal(cfg["_paths"]["journal"])
    pending = jr.pending_scans(demo=args.demo)
    if not pending:
        console.print("[dim]No un-graded scans. Run a scan first.[/dim]")
        return
    provider = get_provider(args.demo, include_next=True)
    graded = 0
    for scan in pending:
        picks = jr.scan_picks(scan["id"])
        tickers = picks["ticker"].tolist()
        with console.status(f"[bold]grading scan of {scan['trade_date']} "
                            f"({len(tickers)} names)…"):
            hist = provider.get_history(tickers, days=60)
            grade = jr.evaluate_scan(scan["id"], scan["trade_date"], hist)
        if grade is None:
            console.print(f"[dim]scan {scan['trade_date']}: next session not complete "
                          "yet (or no data) — skipping.[/dim]")
            continue
        render_eval(console, scan, grade)
        graded += 1
    if not graded:
        console.print("[yellow]Nothing gradeable yet — come back after the next close.")


def cmd_history(args):
    cfg = load_config(args.config)
    jr = Journal(cfg["_paths"]["journal"])
    hist = jr.history(demo=args.demo)
    if hist.empty:
        console.print("[dim]Journal is empty.[/dim]")
        return
    render_history(console, hist)



def cmd_flow(args):
    cfg = load_config(args.config)
    fcfg = dict(cfg["flow"])
    if args.window:
        fcfg["window_min"] = args.window
    console = Console()
    jr = None if args.no_journal else Journal(cfg["_paths"]["journal"])
    store = flow_mod.new_state_store()

    if args.demo:
        from src.demo import FlowDemo
        fd = FlowDemo()
        med = flow_mod.default_curve()
        marks = [15, 35, 60, 90, 115, 135, 160, 185, 215, 260, 330][: args.ticks or 11]
        base = dt.datetime.combine(fd.day, dt.time(9, 30), tzinfo=NY)
        for k, m in enumerate(marks):
            now = base + dt.timedelta(minutes=m)
            rows, events = flow_mod.snapshot(
                fd.bars_until(now), fd.adv, fd.prev_close, {}, med, now, fcfg, store)
            ri, ro = flow_mod.rotation_lists(rows)
            report.render_flow(console, now, rows, events, ri, ro, "demo",
                               fcfg["window_min"],
                               clear=console.is_terminal and k > 0)
            _dump_flow_state(cfg, now, rows, ri, ro, "demo", fcfg["window_min"])
            if k == len(marks) - 1:
                demo_radar = [{"ticker": "ROTA", "pace": 5.3, "last": 16.5,
                               "day_pct": 0.058, "dollar_day": 3.4e6, "promoted": True},
                              {"ticker": "VLTA", "pace": 4.7, "last": 7.21,
                               "day_pct": 0.132, "dollar_day": 1.9e6, "promoted": True},
                              {"ticker": "IGNA", "pace": 3.9, "last": 8.7,
                               "day_pct": 0.096, "dollar_day": 2.6e6, "promoted": True},
                              {"ticker": "QRVX", "pace": 3.1, "last": 42.10,
                               "day_pct": -0.041, "dollar_day": 8.8e6, "promoted": False}]
                with open(os.path.join(_state_dir(cfg), "latest_radar.json"), "w") as f:
                    json.dump({"ts": now.isoformat(timespec="seconds"),
                               "rows": demo_radar}, f)
            for ev in events:
                if jr:
                    jr.record_flow_event(True, ev)
            time.sleep(args.interval if args.interval is not None else
                       (0.8 if console.is_terminal else 0))
        return

    from src.providers_alpaca import creds as alpaca_creds
    ac = alpaca_creds()
    if ac:
        from src import flow_alpaca
        from src.providers_alpaca import AlpacaData
        ap = AlpacaData(*ac)
        sdir = _state_dir(cfg)
        watch = read_ticker_file(Path(cfg["_paths"]["root"]) / cfg["universe"]["watchlist_file"])
        seed = list(dict.fromkeys(
            (jr.latest_picks(False, 30) if jr else []) + watch))
        base = flow_alpaca.prepare(ap, sdir, seed, fcfg,
                                   lambda m: console.print(f"[dim]{m}[/dim]"))
        if base is None:
            console.print("[red]alpaca baselines failed — falling back to "
                          "yahoo flow this run[/red]")
        else:
            today = ny_today()
            store_path = os.path.join(sdir, f"flow_store_{today.isoformat()}.json")
            try:
                with open(store_path) as f:
                    store.update(json.load(f))
            except Exception:
                pass
            console.print(f"[dim]alpaca radar: {len(base['adv'])} symbols · "
                          f"engine seed {len(seed)} names · ctrl-c to stop[/dim]")
            tick = 0
            try:
                while True:
                    now = dt.datetime.now(NY)
                    radar_rows, fresh, watchset, bars = flow_alpaca.fetch_tick(
                        ap, base, seed, now, sdir, fcfg)
                    rows, events = flow_mod.snapshot(
                        bars, base["adv"], base["prev_close"], base["curves"],
                        base["med"], now, fcfg, store)
                    ri, ro = flow_mod.rotation_lists(rows)
                    report.render_flow(console, now, rows, events, ri, ro,
                                       "alpaca-iex", fcfg["window_min"],
                                       clear=console.is_terminal and tick > 0)
                    if fresh:
                        console.print("[bold magenta]radar promoted:[/bold magenta] "
                                      + "  ".join(fresh))
                    for ev in events:
                        if jr:
                            jr.record_flow_event(False, ev)
                    _dump_flow_state(cfg, now, rows, ri, ro, "alpaca-iex",
                                     fcfg["window_min"])
                    flow_alpaca.dump_radar_state(sdir, now, radar_rows)
                    with open(store_path, "w") as f:
                        json.dump(store, f)
                    tick += 1
                    if args.ticks and tick >= args.ticks:
                        return
                    time.sleep(args.interval if args.interval is not None
                               else fcfg["refresh_sec"])
            except KeyboardInterrupt:
                console.print("\n[dim]flow monitor stopped.[/dim]")
            return

    provider = get_provider(False)
    watch = read_ticker_file(Path(cfg["_paths"]["root"]) / cfg["universe"]["watchlist_file"])
    monitor = list(dict.fromkeys(
        (jr.latest_picks(False, 30) if jr else [])
        + provider.screen("most_actives", 25)
        + provider.screen("day_gainers", 15)
        + watch))[: fcfg["monitor_cap"]]
    if len(monitor) < 5:
        console.print("[red]not enough names to monitor — run a scan first "
                      "or add tickers to watchlist.txt[/red]")
        sys.exit(1)
    today = ny_today()
    base_path = os.path.join(_state_dir(cfg), f"flow_base_{today.isoformat()}.json")
    base = None
    try:
        with open(base_path) as f:
            base = json.load(f)
    except Exception:
        pass
    if base:
        adv = base["adv"]
        prev_close = base["prev_close"]
        curves = {t: __import__("numpy").array(c) for t, c in base["curves"].items()}
        med = __import__("numpy").array(base["med"])
    else:
        with console.status("loading ADV + intraday volume-curve baselines…"):
            daily = provider.get_history(monitor, days=50)
            adv, prev_close = {}, {}
            for t, df in daily.items():
                hist = df[df.index.date < today] if df.index.date[-1] == today else df
                if len(hist) < 21:
                    continue
                adv[t] = float(hist["Volume"].iloc[-20:].mean())
                prev_close[t] = float(hist["Close"].iloc[-1])
            curves, med = flow_mod.build_curves(provider.get_intraday_recent(monitor))
        with open(base_path, "w") as f:
            json.dump({"adv": adv, "prev_close": prev_close,
                       "curves": {t: list(map(float, c)) for t, c in curves.items()},
                       "med": list(map(float, med))}, f)
    console.print(f"[dim]monitoring {len(adv)} names · per-name curves for "
                  f"{len(curves)} · refresh {fcfg['refresh_sec']}s · ctrl-c to stop[/dim]")
    store_path = os.path.join(_state_dir(cfg), f"flow_store_{today.isoformat()}.json")
    try:
        with open(store_path) as f:
            store.update(json.load(f))
    except Exception:
        pass
    tick = 0
    try:
        while True:
            now = dt.datetime.now(NY)
            bars = provider.get_intraday_batch(list(adv))
            rows, events = flow_mod.snapshot(bars, adv, prev_close, curves,
                                             med, now, fcfg, store)
            if not rows:
                console.print("[yellow]no intraday bars yet — is the market "
                              "open? retrying…[/yellow]")
            else:
                ri, ro = flow_mod.rotation_lists(rows)
                report.render_flow(console, now, rows, events, ri, ro,
                                   provider.name, fcfg["window_min"],
                                   clear=console.is_terminal and tick > 0)
                for ev in events:
                    if jr:
                        jr.record_flow_event(False, ev)
                _dump_flow_state(cfg, now, rows, ri, ro, provider.name,
                                 fcfg["window_min"])
                with open(store_path, "w") as f:
                    json.dump(store, f)
            tick += 1
            if args.ticks and tick >= args.ticks:
                return
            time.sleep(args.interval if args.interval is not None
                       else fcfg["refresh_sec"])
    except KeyboardInterrupt:
        console.print("\n[dim]flow monitor stopped.[/dim]")



def cmd_hub(args):
    cfg = load_config(args.config)
    from src import hub
    from src.config import PROJECT_ROOT
    out = args.out or os.path.join(PROJECT_ROOT, "docs")
    path = hub.build(cfg, out, demo=args.demo)
    Console().print(f"[bold]hub → {path}[/bold]")


def cmd_paths(args):
    cfg = load_config(args.config)
    for k, v in cfg["_paths"].items():
        console.print(f"[bold]{k:8}[/bold] {v}")


def main():
    p = argparse.ArgumentParser(prog="ignition", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("scan", help="rank tomorrow's likely movers")
    ps.add_argument("--demo", action="store_true", help="offline synthetic market")
    ps.add_argument("--premarket", action="store_true",
                    help="morning mode: use pre-market tape instead of after-hours")
    ps.add_argument("--fast", action="store_true", help="skip the enrichment pass")
    ps.add_argument("--top", type=int, default=None, help="rows to display")
    ps.add_argument("--enrich", type=int, default=None, help="names to enrich")
    ps.add_argument("--no-journal", action="store_true")
    ps.add_argument("--csv", default=None)
    ps.add_argument("--json", default=None)
    ps.add_argument("--tv", default=None, help="write TradingView watchlist file")
    ps.set_defaults(func=cmd_scan)

    pe = sub.add_parser("eval", help="grade past scans against realized activity")
    pe.add_argument("--demo", action="store_true")
    pe.set_defaults(func=cmd_eval)

    ph = sub.add_parser("history", help="rolling IC / edge across all scans")
    ph.add_argument("--demo", action="store_true")
    ph.set_defaults(func=cmd_history)

    pf = sub.add_parser("flow", help="live intraday money-rotation monitor")
    pf.add_argument("--demo", action="store_true")
    pf.add_argument("--ticks", type=int, default=None, help="stop after N refreshes")
    pf.add_argument("--interval", type=float, default=None, help="seconds between refreshes")
    pf.add_argument("--window", type=int, default=None, help="trailing window minutes")
    pf.add_argument("--no-journal", action="store_true")
    pf.add_argument("--config", default=None)
    pf.set_defaults(func=cmd_flow)

    pb = sub.add_parser("hub", help="render the static phone dashboard (docs/)")
    pb.add_argument("--demo", action="store_true")
    pb.add_argument("--out", default=None)
    pb.add_argument("--config", default=None)
    pb.set_defaults(func=cmd_hub)

    pp = sub.add_parser("paths", help="show config / journal / cache locations")
    pp.set_defaults(func=cmd_paths)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
